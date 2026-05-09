"""Tests for mode-aware generation queueing."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from models import ExtractionData, GenerationData, Job, JobStatus, is_legal_transition
from orchestrator import (
    ArtifactConflictError,
    Orchestrator,
    _job_summary,
)
from persistence import JsonJobStore


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)


@pytest.fixture
def jobs_dir(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setattr(config, "JOBS_DIR", jobs)
    monkeypatch.setattr(config, "GENERATION_LOCK_FILE", jobs / ".generation_lock")
    return jobs


@pytest.fixture
def store(jobs_dir):
    return JsonJobStore(jobs_dir=jobs_dir)


@pytest.fixture
def orch(store, event_loop):
    orchestrator = Orchestrator(store)
    orchestrator._event_queue = None
    return orchestrator


def _make_job(
    store: JsonJobStore,
    *,
    status: JobStatus = JobStatus.SCRAPED,
    source_url: str = "https://example.com/job",
) -> Job:
    job = store.create_job(source_url)
    job.extraction = ExtractionData(
        company_name="Acme",
        role_title="Engineer",
        job_description="Build reliable systems.",
    )
    job.status = status
    return store.update_job(job)


def _make_generated_job(
    store: JsonJobStore,
    *,
    source_url: str = "https://example.com/generated",
    resume_completed_at: datetime | None = None,
    cover_letter_completed_at: datetime | None = None,
    resume_path: str | None = None,
    cover_letter_path: str | None = None,
) -> Job:
    job = _make_job(store, status=JobStatus.GENERATED, source_url=source_url)
    job.generation = GenerationData(
        completed_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        resume_completed_at=resume_completed_at,
        cover_letter_completed_at=cover_letter_completed_at,
        resume_path=resume_path,
        cover_letter_path=cover_letter_path,
    )
    return store.update_job(job)


def test_run_generation_dispatches_both_then_single_leg(orch, store, monkeypatch):
    calls: list[tuple[str, str, bool]] = []

    async def fake_run_one_leg(self, job, mode, *, is_final_leg):
        calls.append((job.job_id, mode, is_final_leg))
        current = self.store.get_job(job.job_id)
        gen = current.generation.model_copy(deep=True)
        gen.output_folder = f"/tmp/{current.job_id}/{mode}"
        gen.completed_at = datetime.now(timezone.utc)
        if mode == "resume_only":
            gen.resume_completed_at = datetime.now(timezone.utc)
            gen.resume_path = f"/tmp/{current.job_id}/resume.pdf"
            gen.selection_log_path = f"/tmp/{current.job_id}/selection_log.json"
        if mode == "cover_letter_only":
            gen.cover_letter_completed_at = datetime.now(timezone.utc)
            gen.cover_letter_path = f"/tmp/{current.job_id}/cover_letter.pdf"
        current.generation = gen
        current.status = JobStatus.GENERATED
        self.store.update_job(current)

    monkeypatch.setattr(Orchestrator, "_run_one_leg", fake_run_one_leg)

    both_job = _make_job(store, source_url="https://example.com/both")
    queued = _run(orch.queue_job(both_job.job_id, mode="both"))
    _run(orch._run_generation(queued))

    assert calls == [
        (both_job.job_id, "resume_only", False),
        (both_job.job_id, "cover_letter_only", True),
    ]

    calls.clear()
    resume_job = _make_job(store, source_url="https://example.com/resume")
    queued = _run(orch.queue_job(resume_job.job_id, mode="resume_only"))
    _run(orch._run_generation(queued))

    assert calls == [(resume_job.job_id, "resume_only", True)]


def test_queue_job_artifact_conflicts_and_force_override(orch, store):
    resume_time = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    cover_time = datetime(2026, 5, 1, 13, tzinfo=timezone.utc)

    resume_job = _make_generated_job(
        store,
        source_url="https://example.com/resume-conflict",
        resume_completed_at=resume_time,
    )
    with pytest.raises(ArtifactConflictError) as resume_exc:
        _run(orch.queue_job(resume_job.job_id, mode="resume_only", force=False))
    assert resume_exc.value.mode == "resume"

    forced_resume = _run(
        orch.queue_job(resume_job.job_id, mode="resume_only", force=True)
    )
    assert forced_resume.status == JobStatus.QUEUED
    assert forced_resume.generation.resume_completed_at is None

    cover_job = _make_generated_job(
        store,
        source_url="https://example.com/cover-conflict",
        cover_letter_completed_at=cover_time,
    )
    with pytest.raises(ArtifactConflictError) as cover_exc:
        _run(orch.queue_job(cover_job.job_id, mode="cover_letter_only", force=False))
    assert cover_exc.value.mode == "cover_letter"

    forced_cover = _run(
        orch.queue_job(cover_job.job_id, mode="cover_letter_only", force=True)
    )
    assert forced_cover.status == JobStatus.QUEUED
    assert forced_cover.generation.cover_letter_completed_at is None

    both_resume_job = _make_generated_job(
        store,
        source_url="https://example.com/both-resume-conflict",
        resume_completed_at=resume_time,
    )
    with pytest.raises(ArtifactConflictError) as both_resume_exc:
        _run(orch.queue_job(both_resume_job.job_id, mode="both", force=False))
    assert both_resume_exc.value.mode == "resume"

    both_cover_job = _make_generated_job(
        store,
        source_url="https://example.com/both-cover-conflict",
        cover_letter_completed_at=cover_time,
    )
    with pytest.raises(ArtifactConflictError) as both_cover_exc:
        _run(orch.queue_job(both_cover_job.job_id, mode="both", force=False))
    assert both_cover_exc.value.mode == "cover_letter"

    both_force_job = _make_generated_job(
        store,
        source_url="https://example.com/both-force",
        resume_completed_at=resume_time,
        cover_letter_completed_at=cover_time,
    )
    forced_both = _run(orch.queue_job(both_force_job.job_id, mode="both", force=True))
    assert forced_both.status == JobStatus.QUEUED
    assert forced_both.generation.resume_completed_at is None
    assert forced_both.generation.cover_letter_completed_at is None


def test_cover_letter_only_auto_promotes_to_both_without_resume(orch, store):
    job = _make_job(store)

    queued = _run(orch.queue_job(job.job_id, mode="cover_letter_only"))

    assert queued.status == JobStatus.QUEUED
    assert queued.generation_mode == "both"


def test_http_queue_endpoint_shapes(jobs_dir, store, orch, monkeypatch):
    import server as server_module

    monkeypatch.setattr(server_module, "store", store)
    monkeypatch.setattr(server_module, "orchestrator", orch)
    client = TestClient(server_module.app)

    bad_mode_job = _make_job(store, source_url="https://example.com/bad-mode")
    response = client.post(
        f"/api/jobs/{bad_mode_job.job_id}/queue",
        json={"mode": "bogus"},
    )
    assert response.status_code == 409
    assert "Invalid generation mode" in response.json()["detail"]

    response = client.post(
        "/api/jobs/missing-job/queue",
        json={"mode": "resume_only"},
    )
    assert response.status_code == 404

    conflict_job = _make_generated_job(
        store,
        source_url="https://example.com/http-conflict",
        resume_completed_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    response = client.post(
        f"/api/jobs/{conflict_job.job_id}/queue",
        json={"mode": "resume_only"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "artifact_exists"
    assert detail["mode"] == "resume"

    success_job = _make_job(store, source_url="https://example.com/http-success")
    response = client.post(
        f"/api/jobs/{success_job.job_id}/queue",
        json={"mode": "resume_only"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == success_job.job_id
    assert body["status"] == "queued"
    assert body["generation_mode"] == "resume_only"
    assert "extraction" in body

    approve_job = _make_job(store, source_url="https://example.com/http-approve")
    response = client.post(f"/api/jobs/{approve_job.job_id}/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["generation_mode"] == "both"


def test_generated_to_queued_transition_is_legal():
    assert is_legal_transition(JobStatus.GENERATED, JobStatus.QUEUED) is True


class FakeProcess:
    pid = 12345
    returncode = 0

    async def wait(self):
        return 0


def _fake_subprocess_for_output(output_folder: Path):
    async def fake_create_subprocess_exec(*args, stdout=None, **kwargs):
        stdout.write(f"OUTPUT_PATH: {output_folder}\n")
        stdout.flush()
        return FakeProcess()

    return fake_create_subprocess_exec


def _write_resume_package(output_folder: Path) -> None:
    (output_folder / "note").mkdir(parents=True, exist_ok=True)
    (output_folder / "resume.pdf").write_bytes(b"%PDF resume")
    (output_folder / "note" / "compile_resume.log").write_text(
        "Writing `resume.pdf`\nBUILD: SUCCESS",
        encoding="utf-8",
    )
    (output_folder / "note" / "selection_log.json").write_text(
        json.dumps({"jd_requirements": ["Python"], "selected_projects": []}),
        encoding="utf-8",
    )


def _write_cover_letter_package(output_folder: Path) -> None:
    (output_folder / "note").mkdir(parents=True, exist_ok=True)
    (output_folder / "cover_letter.pdf").write_bytes(b"%PDF cover letter")
    (output_folder / "note" / "compile_cover_letter.log").write_text(
        "Writing `cover_letter.pdf`\nBUILD: SUCCESS",
        encoding="utf-8",
    )


def test_run_one_leg_resume_timestamp_does_not_touch_cover_letter(
    orch, store, jobs_dir, tmp_path, monkeypatch
):
    output_folder = tmp_path / "Re-Generator" / "applications" / "05012026" / "Acme_Engineer"
    _write_resume_package(output_folder)
    original_cover_path = "/existing/cover_letter.pdf"

    job = _make_job(store, status=JobStatus.QUEUED)
    job.generation = GenerationData(cover_letter_path=original_cover_path)
    store.update_job(job)

    monkeypatch.setattr(config, "RESUME_GENERATOR_DIR", tmp_path / "Re-Generator")
    monkeypatch.setattr(config, "APPLY_JD_SKILL_FILE", tmp_path / "missing" / "SKILL.md")
    monkeypatch.setattr(config, "CLAUDE_CLI", "claude")
    monkeypatch.setattr(config, "GENERATION_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        _fake_subprocess_for_output(output_folder),
    )

    _run(orch._run_one_leg(job, "resume_only", is_final_leg=True))

    final = store.get_job(job.job_id)
    assert final.status == JobStatus.GENERATED
    assert final.generation.resume_completed_at is not None
    assert final.generation.cover_letter_completed_at is None
    assert final.generation.cover_letter_path == original_cover_path


def test_run_one_leg_cover_letter_timestamp_does_not_clobber_resume(
    orch, store, jobs_dir, tmp_path, monkeypatch
):
    output_folder = tmp_path / "Re-Generator" / "applications" / "05012026" / "Acme_Engineer"
    _write_cover_letter_package(output_folder)
    resume_completed_at = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    resume_path = str(jobs_dir / "existing" / "resume.pdf")

    job = _make_job(store, status=JobStatus.QUEUED)
    existing_resume = jobs_dir / job.job_id / "output" / "resume.pdf"
    existing_resume.write_bytes(b"%PDF existing resume")
    job.generation = GenerationData(
        output_folder=str(output_folder),
        resume_path=resume_path,
        resume_completed_at=resume_completed_at,
    )
    store.update_job(job)

    monkeypatch.setattr(config, "RESUME_GENERATOR_DIR", tmp_path / "Re-Generator")
    monkeypatch.setattr(config, "APPLY_JD_SKILL_FILE", tmp_path / "missing" / "SKILL.md")
    monkeypatch.setattr(config, "CLAUDE_CLI", "claude")
    monkeypatch.setattr(config, "GENERATION_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        _fake_subprocess_for_output(output_folder),
    )

    _run(orch._run_one_leg(job, "cover_letter_only", is_final_leg=True))

    final = store.get_job(job.job_id)
    assert final.status == JobStatus.GENERATED
    assert final.generation.cover_letter_completed_at is not None
    assert final.generation.resume_completed_at == resume_completed_at
    assert final.generation.resume_path == resume_path
    assert existing_resume.exists()


def test_job_summary_includes_generation_mode_and_per_leg_timestamps():
    resume_completed_at = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    cover_letter_completed_at = datetime(2026, 5, 1, 13, tzinfo=timezone.utc)
    job = Job(source_url="https://example.com/summary")
    job.generation_mode = "cover_letter_only"
    job.generation = GenerationData(
        resume_completed_at=resume_completed_at,
        cover_letter_completed_at=cover_letter_completed_at,
    )

    summary = _job_summary(job)

    assert summary["generation_mode"] == "cover_letter_only"
    assert "generation" in summary
    assert summary["generation"]["resume_completed_at"] == resume_completed_at.isoformat()
    assert (
        summary["generation"]["cover_letter_completed_at"]
        == cover_letter_completed_at.isoformat()
    )

    resume_only_job = Job(source_url="https://example.com/resume-only-summary")
    resume_only_job.generation = GenerationData(resume_completed_at=resume_completed_at)
    resume_only_summary = _job_summary(resume_only_job)
    assert "generation" in resume_only_summary
    assert (
        resume_only_summary["generation"]["resume_completed_at"]
        == resume_completed_at.isoformat()
    )

    cover_only_job = Job(source_url="https://example.com/cover-only-summary")
    cover_only_job.generation = GenerationData(
        cover_letter_completed_at=cover_letter_completed_at
    )
    cover_only_summary = _job_summary(cover_only_job)
    assert "generation" in cover_only_summary
    assert (
        cover_only_summary["generation"]["cover_letter_completed_at"]
        == cover_letter_completed_at.isoformat()
    )
