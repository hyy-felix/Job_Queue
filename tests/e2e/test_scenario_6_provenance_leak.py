from __future__ import annotations

import pytest

from .helpers import (
    approve_and_wait,
    assert_no_leak_tokens,
    extracted_pdf_text,
    output_path,
    read_json,
    run_approval_retry_flow,
    seed_scraped_job,
)


@pytest.mark.invariant
def test_invariant_no_candidate_in_final_pdf(
    job_queue_context,
    reset_ledger,
    scenario_state_file,
    monkeypatch,
):
    reset_ledger("empty")
    state_file = scenario_state_file("insufficient")
    monkeypatch.setenv("JQ_E2E_STATE_FILE", str(state_file))
    monkeypatch.setenv("JQ_E2E_SCENARIO", "sufficient_after_approval")

    first, approval, final = run_approval_retry_flow(job_queue_context, state_file)
    candidate_artifact = first["candidate_artifact_snapshot"]
    candidate = candidate_artifact["candidates"][0]

    assert first["had_final_outputs_before_retry"] is False
    assert candidate["approval_status"] == "candidate_only"
    assert approval["record"]["source_candidate"]["candidate_id"] == candidate["candidate_id"]

    selection_log = read_json(final["generation"]["selection_log_path"])
    selected = selection_log["selected_projects"][0]
    assert selected["source_record_id"] == approval["source_record_id"]
    assert selected["source_record_id"].startswith("approved_accpro:")

    final_text = "\n".join(
        [
            extracted_pdf_text(final["generation"]["resume_path"]),
            extracted_pdf_text(final["generation"]["cover_letter_path"]),
        ]
    )
    assert candidate["candidate_id"] not in final_text
    assert "candidate_only" not in final_text


@pytest.mark.invariant
def test_invariant_provenance_tokens_not_in_final_pdf(
    job_queue_context,
    reset_ledger,
    scenario_state_file,
    monkeypatch,
):
    reset_ledger("valid")
    monkeypatch.setenv("JQ_E2E_SCENARIO", "sufficient")
    first_job_id = seed_scraped_job(
        job_queue_context,
        source_url="https://example.com/job/provenance-sufficient",
    )
    first = approve_and_wait(job_queue_context, first_job_id, "generated")

    reset_ledger("empty")
    state_file = scenario_state_file("insufficient")
    monkeypatch.setenv("JQ_E2E_STATE_FILE", str(state_file))
    monkeypatch.setenv("JQ_E2E_SCENARIO", "sufficient_after_approval")
    _, _, second = run_approval_retry_flow(job_queue_context, state_file)

    assert_no_leak_tokens(
        [
            first["generation"]["resume_path"],
            first["generation"]["cover_letter_path"],
            second["generation"]["resume_path"],
            second["generation"]["cover_letter_path"],
        ]
    )
