import asyncio
import json

import pytest

import config
from models import JobStatus
from orchestrator import CANDIDATE_ARTIFACT_NAME, Orchestrator, StateTransitionError
from persistence import JsonJobStore


def _build_orchestrator(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    monkeypatch.setattr(config, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(config, "GENERATION_LOCK_FILE", jobs_dir / ".generation_lock")
    store = JsonJobStore(jobs_dir)
    orch = Orchestrator.__new__(Orchestrator)
    orch.store = store
    orch._active_processes = {}
    orch._event_queue = None
    orch._queue_task = None
    orch._running = False
    return orch, store, jobs_dir


@pytest.mark.invariant
def test_invariant_no_score_or_apply_from_needs_bullet_approval(tmp_path, monkeypatch):
    orch, store, jobs_dir = _build_orchestrator(tmp_path, monkeypatch)
    job = store.create_job("https://example.com/job/invariant-score-apply")
    job.status = JobStatus.NEEDS_BULLET_APPROVAL
    store.update_job(job)

    with pytest.raises(StateTransitionError):
        asyncio.run(orch.receive_score(job.job_id, {"overall_score": 80}))
    with pytest.raises(StateTransitionError):
        asyncio.run(orch.queue_apply(job.job_id))

    out_dir = jobs_dir / job.job_id / "output"
    assert not (out_dir / "metadata.json").exists()
    assert not (out_dir / "resume.pdf").exists()
    assert not (out_dir / "cover_letter.pdf").exists()


@pytest.mark.invariant
def test_invariant_candidate_artifact_not_used_as_source(tmp_path, monkeypatch):
    orch, store, jobs_dir = _build_orchestrator(tmp_path, monkeypatch)
    job = store.create_job("https://example.com/job/invariant-candidate-artifact")
    job.status = JobStatus.NEEDS_BULLET_APPROVAL
    store.update_job(job)

    out_dir = jobs_dir / job.job_id / "output"
    candidate_artifact = {
        "schema_version": 1,
        "status": "candidate_artifact",
        "candidates": [
            {
                "candidate_id": "accpro:case_001:claim_001",
                "candidate_text": "Built closed-loop controls for a robotics platform.",
                "candidate_rendering_mode": "claim_text_echo",
                "approval_status": "candidate_only",
                "usable_in_resume": False,
                "source_record_id": None,
                "source_refs": [
                    {
                        "casefile_id": "case_001",
                        "claim_id": "claim_001",
                        "claim_text": "Built closed-loop controls for a robotics platform.",
                    }
                ],
            }
        ],
    }
    (out_dir / CANDIDATE_ARTIFACT_NAME).write_text(
        json.dumps(candidate_artifact),
        encoding="utf-8",
    )
    (out_dir / "selection_log.json").write_text("{}", encoding="utf-8")
    (out_dir / "notes.md").write_text("# Needs bullets", encoding="utf-8")
    (out_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (out_dir / "resume.pdf").write_bytes(b"%PDF-stale-resume")
    (out_dir / "cover_letter.pdf").write_bytes(b"%PDF-stale-cover-letter")

    result = asyncio.run(orch.retry_job(job.job_id))

    assert result.status == JobStatus.QUEUED
    assert not (out_dir / CANDIDATE_ARTIFACT_NAME).exists()
    assert not (out_dir / "selection_log.json").exists()
    assert not (out_dir / "notes.md").exists()
    assert not (out_dir / "metadata.json").exists()
    assert not (out_dir / "resume.pdf").exists()
    assert not (out_dir / "cover_letter.pdf").exists()
