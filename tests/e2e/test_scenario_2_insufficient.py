from __future__ import annotations

from pathlib import Path

from .helpers import (
    approve_and_wait,
    assert_no_final_outputs,
    output_path,
    read_json,
    seed_scraped_job,
)


def test_insufficient_bullets_calls_candidate_api_and_parks(
    job_queue_context,
    reset_ledger,
    monkeypatch,
):
    reset_ledger("empty")
    monkeypatch.setenv("JQ_E2E_SCENARIO", "insufficient")

    job_id = seed_scraped_job(job_queue_context, source_url="https://example.com/job/insufficient")
    job = approve_and_wait(job_queue_context, job_id, "needs_bullet_approval")

    assert job["status"] == "needs_bullet_approval"
    assert job["generation"]["candidate_generation_status"] == "succeeded"
    candidate_path = Path(job["generation"]["candidate_artifact_path"])
    assert candidate_path == output_path(job_queue_context, job_id, "resume_bullet_candidates.json")
    assert candidate_path.exists()
    assert_no_final_outputs(job_queue_context, job_id)

    selection_log = read_json(output_path(job_queue_context, job_id, "selection_log.json"))
    assert selection_log["needs_bullet_approval"]["triggered"] is True
    assert (
        selection_log["needs_bullet_approval"]["reason"]
        == "insufficient_approved_source_bullets"
    )

    artifact = read_json(candidate_path)
    assert artifact["status"] == "candidate_artifact"
    assert artifact["candidates"]
    candidate = artifact["candidates"][0]
    assert candidate["approval_status"] == "candidate_only"
    assert candidate["usable_in_resume"] is False
    assert candidate["source_record_id"] is None
