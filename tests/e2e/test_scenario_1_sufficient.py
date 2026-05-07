from __future__ import annotations

from .helpers import (
    approve_and_wait,
    output_path,
    score_job,
    seed_scraped_job,
    set_apply_method,
)


def test_sufficient_generation_score_and_manual_apply(
    job_queue_context,
    reset_ledger,
    monkeypatch,
):
    reset_ledger("valid")
    monkeypatch.setenv("JQ_E2E_SCENARIO", "sufficient")

    job_id = seed_scraped_job(job_queue_context, source_url="https://example.com/job/sufficient")
    generated = approve_and_wait(job_queue_context, job_id, "generated")

    assert generated["status"] == "generated"
    assert output_path(job_queue_context, job_id, "resume.pdf").stat().st_size > 0
    assert output_path(job_queue_context, job_id, "cover_letter.pdf").stat().st_size > 0
    assert output_path(job_queue_context, job_id, "selection_log.json").exists()
    assert not output_path(job_queue_context, job_id, "resume_bullet_candidates.json").exists()

    scored = score_job(job_queue_context, job_id)
    assert scored["status"] == "scored"
    assert scored["score"]["overall_score"] == 88.0

    set_apply_method(job_queue_context, job_id, "easy_apply")
    response = job_queue_context.client.post(f"/api/jobs/{job_id}/apply")

    assert response.status_code == 200
    assert response.json()["status"] == "manual_apply"
    assert output_path(job_queue_context, job_id, "resume.pdf").exists()
