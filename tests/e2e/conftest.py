from __future__ import annotations

import importlib
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from .helpers import E2EContext, empty_ledger, read_json, write_json


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
STUB_PATH = Path(__file__).resolve().parent / "stubs" / "claude_stub.py"
FRONTEND_WORKTREE = REPO_ROOT.parent / "Relevel_frontend_phase5a_staleness"
GENERATOR_WORKTREE = REPO_ROOT.parent / "Re-Generator_phase5b_hardening"
VALIDATOR_PATH = GENERATOR_WORKTREE / "scripts" / "validate_approved_accpro_bullets.py"


class E2EPaths:
    def __init__(self, root: Path):
        self.root = root
        self.ledger_path = Path(
            os.environ.get(
                "APPROVED_ACCPRO_BULLETS_PATH",
                str(root / "sources" / "approved_accpro_bullets.json"),
            )
        )
        self.casefiles_dir = Path(
            os.environ.get("ACCPRO_CASEFILES_DIR", str(root / "casefiles"))
        )
        self.output_root = Path(
            os.environ.get("JOB_QUEUE_OUTPUT_ROOT", str(root / "job_queue_output"))
        )


class RelevelHandle:
    def __init__(self, url: str, client=None):
        self.url = url.rstrip("/")
        self.client = client


def _copy_casefiles(casefiles_dir: Path) -> None:
    casefiles_dir.mkdir(parents=True, exist_ok=True)
    for fixture in (FIXTURE_DIR / "casefiles").glob("*.json"):
        shutil.copy2(fixture, casefiles_dir / fixture.name)


def _wait_for_http(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if 200 <= response.status < 500:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"server did not respond at {url}: {last_error}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _candidate_request_body_for_phase5a(self, job, out_dir, selection_log_data):
    signal_data = selection_log_data.get("needs_bullet_approval", {})
    jd = (job.extraction.job_description or "").strip()
    if not jd:
        copied_jd = out_dir / "job_description.txt"
        if copied_jd.exists():
            jd = copied_jd.read_text(encoding="utf-8").strip()
    casefile_ids = signal_data.get("casefile_ids") or selection_log_data.get("casefile_ids")
    return {
        "jd": jd,
        "jd_requirements": selection_log_data.get("jd_requirements", []),
        "unmatched_jd_gaps": selection_log_data.get("unmatched_jd_gaps", []),
        "casefile_ids": [
            item.strip()
            for item in (casefile_ids or [])
            if isinstance(item, str) and item.strip()
        ],
    }


@pytest.fixture(scope="session")
def e2e_paths(tmp_path_factory):
    paths = E2EPaths(tmp_path_factory.mktemp("phase6_e2e"))
    paths.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    paths.output_root.mkdir(parents=True, exist_ok=True)
    if not paths.ledger_path.exists():
        shutil.copy2(FIXTURE_DIR / "approved_accpro_bullets.valid.json", paths.ledger_path)
    _copy_casefiles(paths.casefiles_dir)
    os.environ.setdefault("APPROVED_ACCPRO_BULLETS_PATH", str(paths.ledger_path))
    os.environ.setdefault("ACCPRO_CASEFILES_DIR", str(paths.casefiles_dir))
    os.environ.setdefault("JOB_QUEUE_OUTPUT_ROOT", str(paths.output_root))
    os.environ["EMBEDDING_PROVIDER"] = "tfidf"
    if VALIDATOR_PATH.exists():
        os.environ.setdefault("APPROVED_ACCPRO_LEDGER_VALIDATOR", str(VALIDATOR_PATH))
    return paths


def _inprocess_relevel_handle(e2e_paths):
    os.environ["APPROVED_ACCPRO_BULLETS_PATH"] = str(e2e_paths.ledger_path)
    os.environ["APPROVED_ACCPRO_LEDGER_VALIDATOR"] = str(VALIDATOR_PATH)
    os.environ["ACCPRO_CASEFILES_DIR"] = str(e2e_paths.casefiles_dir)
    os.environ["JOB_QUEUE_OUTPUT_ROOT"] = str(e2e_paths.output_root)
    os.environ["EMBEDDING_PROVIDER"] = "tfidf"
    for module_name in list(sys.modules):
        if module_name == "backend" or module_name.startswith("backend."):
            sys.modules.pop(module_name, None)
    if str(FRONTEND_WORKTREE) not in sys.path:
        sys.path.insert(0, str(FRONTEND_WORKTREE))
    from fastapi import FastAPI

    from backend.api.resume_bullet_approvals import router as approvals_router
    from backend.api.resume_bullet_candidates import router as candidates_router

    app = FastAPI(title="Resume_Go JD Matcher E2E Fallback")
    app.include_router(candidates_router, prefix="/api")
    app.include_router(approvals_router, prefix="/api")

    return TestClient(app)


@pytest.fixture(scope="session")
def relevel_frontend(e2e_paths):
    existing_url = os.environ.get("RELEVEL_FRONTEND_URL")
    if existing_url:
        base_url = existing_url.rstrip("/")
        docs_url = base_url[:-4] if base_url.endswith("/api") else base_url
        _wait_for_http(f"{docs_url}/docs")
        yield RelevelHandle(base_url)
        return

    if not FRONTEND_WORKTREE.exists():
        pytest.skip(f"Relevel_frontend worktree missing: {FRONTEND_WORKTREE}")

    try:
        port = _free_port()
    except PermissionError:
        client = _inprocess_relevel_handle(e2e_paths)
        with client:
            yield RelevelHandle("http://relevel-e2e.local/api", client)
        return

    url = f"http://127.0.0.1:{port}/api"
    log_path = e2e_paths.root / "relevel_frontend_uvicorn.log"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(FRONTEND_WORKTREE),
            "APPROVED_ACCPRO_BULLETS_PATH": str(e2e_paths.ledger_path),
            "APPROVED_ACCPRO_LEDGER_VALIDATOR": str(VALIDATOR_PATH),
            "ACCPRO_CASEFILES_DIR": str(e2e_paths.casefiles_dir),
            "JOB_QUEUE_OUTPUT_ROOT": str(e2e_paths.output_root),
            "EMBEDDING_PROVIDER": "tfidf",
        }
    )
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(FRONTEND_WORKTREE),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_http(f"http://127.0.0.1:{port}/docs")
        yield RelevelHandle(url)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


@pytest.fixture
def reset_ledger(e2e_paths):
    def _reset(name: str = "valid") -> Path:
        if name == "empty":
            write_json(e2e_paths.ledger_path, empty_ledger())
        else:
            source = FIXTURE_DIR / f"approved_accpro_bullets.{name}.json"
            shutil.copy2(source, e2e_paths.ledger_path)
        return e2e_paths.ledger_path

    return _reset


@pytest.fixture
def scenario_state_file(tmp_path):
    def _make(scenario: str) -> Path:
        path = tmp_path / "scenario_state.json"
        write_json(path, {"scenario": scenario})
        return path

    return _make


@pytest.fixture
def job_queue_context(tmp_path, monkeypatch, e2e_paths, relevel_frontend):
    _copy_casefiles(e2e_paths.casefiles_dir)
    jobs_dir = e2e_paths.output_root / "jobs"
    if jobs_dir.exists():
        shutil.rmtree(jobs_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)

    resume_generator_dir = tmp_path / "Re-Generator"
    skill_file = resume_generator_dir / ".claude" / "skills" / "apply-jd" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("Phase 6 e2e apply-jd placeholder.\n", encoding="utf-8")
    (resume_generator_dir / "applications").mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("JQ_CLAUDE_CLI", str(STUB_PATH))
    monkeypatch.setenv("JQ_RESUME_GENERATOR_DIR", str(resume_generator_dir))
    monkeypatch.setenv("JQ_GENERATION_TIMEOUT", "60")
    monkeypatch.setenv("JQ_CANDIDATE_API_TIMEOUT", "10")
    monkeypatch.setenv("JQ_MOCK_EXTRACTION", "true")
    monkeypatch.setenv("RELEVEL_FRONTEND_URL", relevel_frontend.url)
    monkeypatch.setenv("APPROVED_ACCPRO_BULLETS_PATH", str(e2e_paths.ledger_path))
    monkeypatch.setenv("ACCPRO_CASEFILES_DIR", str(e2e_paths.casefiles_dir))
    monkeypatch.setenv("JOB_QUEUE_OUTPUT_ROOT", str(e2e_paths.output_root))

    import config

    importlib.reload(config)
    config.JOBS_DIR = jobs_dir
    config.GENERATION_LOCK_FILE = jobs_dir / ".generation_lock"
    config.RESUME_GENERATOR_DIR = resume_generator_dir
    config.APPLY_JD_SKILL_FILE = skill_file
    config.CLAUDE_CLI = str(STUB_PATH)
    config.GENERATION_TIMEOUT_SECONDS = 60

    import orchestrator
    import persistence

    importlib.reload(orchestrator)
    importlib.reload(persistence)
    sys.modules.pop("server", None)
    import server

    def _request_candidate_artifact_inprocess(api_url, payload, timeout_seconds):
        response = relevel_frontend.client.post("/api/resume-bullet-candidates", json=payload)
        if response.status_code >= 400:
            raise orchestrator.CandidateGenerationError(
                f"candidate API HTTP {response.status_code}"
            )
        return response.json()

    completed = subprocess.CompletedProcess(args=["open"], returncode=0)
    patches = [
        patch.object(
            orchestrator.Orchestrator,
            "_candidate_request_body",
            _candidate_request_body_for_phase5a,
        ),
        patch.object(orchestrator.subprocess, "run", return_value=completed),
    ]
    if relevel_frontend.client is not None:
        patches.append(
            patch.object(
                orchestrator.Orchestrator,
                "_request_candidate_artifact",
                staticmethod(_request_candidate_artifact_inprocess),
            )
        )

    with patches[0], patches[1]:
        if len(patches) == 3:
            patches[2].start()
        try:
            with TestClient(server.app) as client:
                yield E2EContext(
                    client=client,
                    server=server,
                    jobs_dir=jobs_dir,
                    output_root=e2e_paths.output_root,
                    resume_generator_dir=resume_generator_dir,
                    relevel_url=relevel_frontend.url,
                    relevel_client=relevel_frontend.client,
                )
        finally:
            if len(patches) == 3:
                patches[2].stop()


@pytest.fixture
def current_ledger(e2e_paths):
    return lambda: read_json(e2e_paths.ledger_path)
