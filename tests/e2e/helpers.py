from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests


LEAK_TOKENS = [
    "source_record_id",
    "approved_accpro:",
    "approved_text_hash",
    "source_fingerprint",
    "casefile_id",
    "claim_id",
    "approval_scope",
]

JD_TEXT = (
    "We need robotics controls software experience, grounded sensor feedback, "
    "and production-quality validation."
)
COMPANY = "Example Company"
ROLE = "Robotics Software Engineer"


@dataclass
class E2EContext:
    client: object
    server: object
    jobs_dir: Path
    output_root: Path
    resume_generator_dir: Path
    relevel_url: str
    relevel_client: object | None = None


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def empty_ledger() -> dict:
    return {
        "schema_version": 1,
        "ledger_type": "approved_accpro_bullets",
        "updated_at": None,
        "records": [],
    }


def seed_scraped_job(
    ctx: E2EContext,
    *,
    source_url: str = "https://example.com/job/e2e",
    company: str = COMPANY,
    role: str = ROLE,
    apply_method: str = "apply",
) -> str:
    from models import ExtractionData, JobStatus

    job = ctx.server.store.create_job(source_url)
    job.status = JobStatus.SCRAPED
    job.extraction = ExtractionData(
        role_title=role,
        company_name=company,
        location="Remote",
        salary="$150k",
        job_description=JD_TEXT,
        apply_method=apply_method,
    )
    ctx.server.store.update_job(job)
    return job.job_id


def wait_for_status(
    ctx: E2EContext,
    job_id: str,
    expected: str | Iterable[str],
    *,
    timeout: float = 35.0,
) -> dict:
    expected_values = {expected} if isinstance(expected, str) else set(expected)
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        response = ctx.client.get(f"/api/jobs/{job_id}")
        response.raise_for_status()
        last = response.json()
        if last["status"] in expected_values:
            return last
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {expected_values}; last job was {last}")


def approve_for_generation(ctx: E2EContext, job_id: str) -> dict:
    response = ctx.client.post(f"/api/jobs/{job_id}/approve")
    response.raise_for_status()
    return response.json()


def approve_and_wait(ctx: E2EContext, job_id: str, expected: str | Iterable[str]) -> dict:
    approve_for_generation(ctx, job_id)
    return wait_for_status(ctx, job_id, expected)


def score_job(ctx: E2EContext, job_id: str) -> dict:
    response = ctx.client.put(
        f"/api/jobs/{job_id}/score",
        json={
            "overall_score": 88.0,
            "keyword_score": 84.0,
            "semantic_score": 91.0,
            "algorithm": "jd-match-resume",
            "requirement_scores": [{"id": "req_001", "score": 0.9}],
        },
    )
    response.raise_for_status()
    return response.json()


def set_apply_method(ctx: E2EContext, job_id: str, apply_method: str) -> dict:
    from models import ExtractionData

    job = ctx.server.store.get_job(job_id)
    job.extraction = ExtractionData(**job.extraction.model_dump())
    job.extraction.apply_method = apply_method
    ctx.server.store.update_job(job)
    return ctx.client.get(f"/api/jobs/{job_id}").json()


def output_path(ctx: E2EContext, job_id: str, name: str) -> Path:
    return ctx.jobs_dir / job_id / "output" / name


def assert_no_final_outputs(ctx: E2EContext, job_id: str) -> None:
    for name in ("resume.pdf", "cover_letter.pdf", "resume.tex", "cover_letter.tex"):
        assert not output_path(ctx, job_id, name).exists()


def extracted_pdf_text(path: str | Path) -> str:
    return Path(path).read_bytes().decode("latin-1", errors="ignore")


def assert_no_leak_tokens(paths: Iterable[str | Path]) -> None:
    for path in paths:
        text = extracted_pdf_text(path)
        for token in LEAK_TOKENS:
            assert token not in text


def approve_first_candidate(ctx: E2EContext, job: dict) -> dict:
    artifact_path = Path(job["generation"]["candidate_artifact_path"])
    artifact = read_json(artifact_path)
    candidate = artifact["candidates"][0]
    payload = {
        "job_id": job["job_id"],
        "candidate_artifact_path": str(artifact_path),
        "candidate_id": candidate["candidate_id"],
        "approved_text": candidate["candidate_text"],
        "approval_scope": {
            "type": "jd_specific",
            "jd_hash": "sha256:e2e-jd",
            "company": job["extraction"]["company_name"],
            "role": job["extraction"]["role_title"],
        },
        "approved_by": "phase6-e2e",
        "approval_notes": "Approved exact candidate text for Phase 6 e2e retry.",
    }
    if ctx.relevel_client is not None:
        for suffix in ("/api/resume-bullet-approvals/approve", "/api/resume-bullet-approvals"):
            response = ctx.relevel_client.post(suffix, json=payload)
            if response.status_code != 404:
                response.raise_for_status()
                return response.json()
        raise AssertionError("approval endpoint was not found")

    for suffix in ("/resume-bullet-approvals/approve", "/resume-bullet-approvals"):
        response = requests.post(f"{ctx.relevel_url}{suffix}", json=payload, timeout=10)
        if response.status_code != 404:
            response.raise_for_status()
            return response.json()
    raise AssertionError("approval endpoint was not found")


def run_approval_retry_flow(ctx: E2EContext, state_file: Path) -> tuple[dict, dict, dict]:
    write_json(state_file, {"scenario": "insufficient"})
    job_id = seed_scraped_job(ctx, source_url="https://example.com/job/approval-flow")
    first = approve_and_wait(ctx, job_id, "needs_bullet_approval")
    first = dict(first)
    first["candidate_artifact_snapshot"] = read_json(
        first["generation"]["candidate_artifact_path"]
    )
    first["had_final_outputs_before_retry"] = any(
        (ctx.jobs_dir / job_id / "output" / name).exists()
        for name in ("resume.pdf", "cover_letter.pdf")
    )
    approval = approve_first_candidate(ctx, first)

    write_json(state_file, {"scenario": "sufficient_after_approval"})
    response = ctx.client.post(f"/api/jobs/{job_id}/retry")
    response.raise_for_status()
    final = wait_for_status(ctx, job_id, "generated")
    return first, approval, final
