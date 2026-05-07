from __future__ import annotations

import pytest

from .helpers import approve_and_wait, seed_scraped_job


@pytest.mark.invariant
def test_invariant_apply_blocked_from_needs_bullet_approval(
    job_queue_context,
    reset_ledger,
    monkeypatch,
):
    reset_ledger("empty")
    monkeypatch.setenv("JQ_E2E_SCENARIO", "insufficient")

    job_id = seed_scraped_job(job_queue_context, source_url="https://example.com/job/apply-gate")
    job = approve_and_wait(job_queue_context, job_id, "needs_bullet_approval")
    assert job["status"] == "needs_bullet_approval"

    response = job_queue_context.client.post(f"/api/jobs/{job_id}/apply")

    assert response.status_code == 409
    assert "Cannot apply from needs_bullet_approval state" in response.json()["detail"]
    assert job_queue_context.client.get(f"/api/jobs/{job_id}").json()["status"] == "needs_bullet_approval"
