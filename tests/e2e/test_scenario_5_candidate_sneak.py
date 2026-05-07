from __future__ import annotations

from .helpers import (
    approve_and_wait,
    assert_no_final_outputs,
    output_path,
    read_json,
    seed_scraped_job,
)


def test_candidate_only_record_in_approved_path_is_refused(
    job_queue_context,
    reset_ledger,
    monkeypatch,
):
    reset_ledger("candidate_sneak")
    monkeypatch.setenv("JQ_E2E_SCENARIO", "candidate_sneak")

    job_id = seed_scraped_job(
        job_queue_context,
        source_url="https://example.com/job/candidate-sneak",
    )
    job = approve_and_wait(job_queue_context, job_id, "needs_bullet_approval")

    assert job["status"] == "needs_bullet_approval"
    assert_no_final_outputs(job_queue_context, job_id)
    selection_log = read_json(output_path(job_queue_context, job_id, "selection_log.json"))
    assert selection_log["selected_projects"] == []
    assert (
        selection_log["needs_bullet_approval"]["reason"]
        == "insufficient_approved_source_bullets"
    )
