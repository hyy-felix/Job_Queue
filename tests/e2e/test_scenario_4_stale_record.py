from __future__ import annotations

import pytest

from .helpers import (
    approve_and_wait,
    assert_no_final_outputs,
    output_path,
    read_json,
    seed_scraped_job,
)


@pytest.mark.invariant
def test_invariant_stale_record_excluded_from_final(
    job_queue_context,
    reset_ledger,
    monkeypatch,
):
    reset_ledger("stale")
    monkeypatch.setenv("JQ_E2E_SCENARIO", "stale")

    job_id = seed_scraped_job(job_queue_context, source_url="https://example.com/job/stale")
    job = approve_and_wait(job_queue_context, job_id, "needs_bullet_approval")

    assert job["status"] == "needs_bullet_approval"
    assert job["status"] != "generated"
    assert_no_final_outputs(job_queue_context, job_id)
    selection_log = read_json(output_path(job_queue_context, job_id, "selection_log.json"))
    assert (
        selection_log["needs_bullet_approval"]["reason"]
        == "insufficient_approved_source_bullets"
    )
