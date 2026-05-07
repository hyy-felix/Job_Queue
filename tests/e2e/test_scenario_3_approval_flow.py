from __future__ import annotations

from .helpers import read_json, run_approval_retry_flow


def test_approval_flow_adds_approved_record_and_retry_generates(
    job_queue_context,
    reset_ledger,
    scenario_state_file,
    monkeypatch,
    current_ledger,
):
    reset_ledger("empty")
    state_file = scenario_state_file("insufficient")
    monkeypatch.setenv("JQ_E2E_STATE_FILE", str(state_file))
    monkeypatch.setenv("JQ_E2E_SCENARIO", "sufficient_after_approval")

    first, approval, final = run_approval_retry_flow(job_queue_context, state_file)

    assert first["status"] == "needs_bullet_approval"
    assert approval["status"] == "approved"
    assert approval["source_record_id"].startswith("approved_accpro:")

    ledger = current_ledger()
    assert len(ledger["records"]) == 1
    assert ledger["records"][0]["source_record_id"] == approval["source_record_id"]
    assert ledger["records"][0]["approval_status"] == "approved"
    assert ledger["records"][0]["status"] == "active"

    assert final["status"] == "generated"
    selection_log = read_json(final["generation"]["selection_log_path"])
    selected = selection_log["selected_projects"][0]
    assert selected["source_record_id"] == approval["source_record_id"]
    assert selected["source_type"] == "approved_accpro_bullet"
    assert final["generation"]["resume_path"]
    assert final["generation"]["cover_letter_path"]
