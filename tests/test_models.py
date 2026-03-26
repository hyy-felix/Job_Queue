"""Tests for Job data models and state machine."""

import pytest
from models import (
    ApplyData,
    Job,
    JobStatus,
    ExtractionData,
    SCHEMA_VERSION,
    is_legal_transition,
    is_workflow_terminal,
    is_url_closed,
    LEGAL_TRANSITIONS,
    WORKFLOW_TERMINAL,
    URL_CLOSED,
    IN_FLIGHT_STATES,
)


class TestJobModel:
    def test_default_creation(self):
        job = Job(source_url="https://example.com/job/1")
        assert job.source_url == "https://example.com/job/1"
        assert job.status == JobStatus.SUBMITTED
        assert job.schema_version == SCHEMA_VERSION
        assert job.version == 1
        assert job.job_id  # non-empty
        assert job.created_at is not None
        assert job.extraction.role_title is None
        assert job.error.retry_count == 0
        assert job.worker.pid is None

    def test_default_apply_data(self):
        job = Job(source_url="https://example.com/job/1")
        assert job.apply.form_filled is False
        assert job.apply.resume_uploaded is False
        assert job.apply.screenshot_path is None
        assert job.apply.applied_at is None
        assert job.apply.fields_filled == []

    def test_serialization_roundtrip(self):
        job = Job(source_url="https://example.com/job/1")
        job.extraction = ExtractionData(
            role_title="Engineer",
            company_name="Acme",
            job_description="Build things",
        )
        json_str = job.model_dump_json()
        restored = Job.model_validate_json(json_str)
        assert restored.source_url == job.source_url
        assert restored.extraction.role_title == "Engineer"
        assert restored.extraction.company_name == "Acme"

    def test_serialization_with_apply_data(self):
        job = Job(source_url="https://example.com/job/1")
        job.apply = ApplyData(
            form_filled=True,
            resume_uploaded=True,
            fields_filled=["name", "email"],
        )
        json_str = job.model_dump_json()
        restored = Job.model_validate_json(json_str)
        assert restored.apply.form_filled is True
        assert restored.apply.resume_uploaded is True
        assert restored.apply.fields_filled == ["name", "email"]

    def test_unique_job_ids(self):
        jobs = [Job(source_url="https://example.com") for _ in range(10)]
        ids = {j.job_id for j in jobs}
        assert len(ids) == 10


class TestStateMachine:
    def test_all_legal_transitions(self):
        """Every declared legal transition should pass."""
        for from_state, to_states in LEGAL_TRANSITIONS.items():
            for to_state in to_states:
                assert is_legal_transition(from_state, to_state), (
                    f"{from_state} → {to_state} should be legal"
                )

    def test_workflow_terminal_states_have_no_transitions(self):
        for state in WORKFLOW_TERMINAL:
            assert LEGAL_TRANSITIONS[state] == set(), (
                f"Workflow-terminal state {state} should have no outgoing transitions"
            )

    def test_illegal_transitions(self):
        assert not is_legal_transition(JobStatus.COMPLETED, JobStatus.EXTRACTING)
        assert not is_legal_transition(JobStatus.CANCELLED, JobStatus.QUEUED)
        assert not is_legal_transition(JobStatus.SUBMITTED, JobStatus.COMPLETED)
        assert not is_legal_transition(JobStatus.QUEUED, JobStatus.EXTRACTING)
        # Apply-specific illegal transitions
        assert not is_legal_transition(JobStatus.SUBMITTED, JobStatus.APPLY_QUEUED)
        assert not is_legal_transition(JobStatus.GENERATING, JobStatus.APPLY_QUEUED)
        assert not is_legal_transition(JobStatus.APPLIED, JobStatus.APPLY_QUEUED)

    def test_happy_path_transitions(self):
        """submitted → extracting → ready_for_review → queued → generating → completed"""
        path = [
            (JobStatus.SUBMITTED, JobStatus.EXTRACTING),
            (JobStatus.EXTRACTING, JobStatus.READY_FOR_REVIEW),
            (JobStatus.READY_FOR_REVIEW, JobStatus.QUEUED),
            (JobStatus.QUEUED, JobStatus.GENERATING),
            (JobStatus.GENERATING, JobStatus.COMPLETED),
        ]
        for from_s, to_s in path:
            assert is_legal_transition(from_s, to_s)

    def test_apply_happy_path(self):
        """completed → apply_queued → applying → applied"""
        path = [
            (JobStatus.COMPLETED, JobStatus.APPLY_QUEUED),
            (JobStatus.APPLY_QUEUED, JobStatus.APPLYING),
            (JobStatus.APPLYING, JobStatus.APPLIED),
        ]
        for from_s, to_s in path:
            assert is_legal_transition(from_s, to_s)

    def test_apply_failure_and_retry(self):
        """applying → apply_failed → apply_queued (retry)"""
        assert is_legal_transition(JobStatus.APPLYING, JobStatus.APPLY_FAILED)
        assert is_legal_transition(JobStatus.APPLY_FAILED, JobStatus.APPLY_QUEUED)

    def test_apply_cancellation(self):
        assert is_legal_transition(JobStatus.APPLY_QUEUED, JobStatus.CANCELLED)
        assert is_legal_transition(JobStatus.APPLYING, JobStatus.CANCELLED)
        assert is_legal_transition(JobStatus.APPLY_FAILED, JobStatus.CANCELLED)

    def test_applied_is_terminal(self):
        assert LEGAL_TRANSITIONS[JobStatus.APPLIED] == set()

    def test_extraction_failed_to_ready_for_review(self):
        """Manual fill path: extraction_failed → ready_for_review"""
        assert is_legal_transition(
            JobStatus.EXTRACTION_FAILED, JobStatus.READY_FOR_REVIEW
        )

    def test_retry_transitions(self):
        assert is_legal_transition(JobStatus.EXTRACTION_FAILED, JobStatus.EXTRACTING)
        assert is_legal_transition(JobStatus.GENERATION_FAILED, JobStatus.GENERATING)
        assert is_legal_transition(JobStatus.APPLY_FAILED, JobStatus.APPLY_QUEUED)

    def test_cancel_from_cancellable_states(self):
        """All states except workflow-terminal and COMPLETED can be cancelled.
        COMPLETED can't be cancelled because its only transition is APPLY_QUEUED."""
        cancellable = [
            s for s in JobStatus
            if not is_workflow_terminal(s) and s != JobStatus.COMPLETED
        ]
        for state in cancellable:
            assert is_legal_transition(state, JobStatus.CANCELLED), (
                f"Should be able to cancel from {state}"
            )
        # COMPLETED cannot be cancelled (only goes to APPLY_QUEUED)
        assert not is_legal_transition(JobStatus.COMPLETED, JobStatus.CANCELLED)

    def test_no_auto_apply_after_generation(self):
        """COMPLETED allows APPLY_QUEUED but NOT auto-transition to APPLYING.
        The apply must be explicitly triggered via queue_apply()."""
        # COMPLETED can go to APPLY_QUEUED (manual trigger)
        assert is_legal_transition(JobStatus.COMPLETED, JobStatus.APPLY_QUEUED)
        # But COMPLETED cannot go directly to APPLYING
        assert not is_legal_transition(JobStatus.COMPLETED, JobStatus.APPLYING)


class TestPredicates:
    def test_is_workflow_terminal(self):
        assert is_workflow_terminal(JobStatus.APPLIED) is True
        assert is_workflow_terminal(JobStatus.CANCELLED) is True
        assert is_workflow_terminal(JobStatus.COMPLETED) is False
        assert is_workflow_terminal(JobStatus.SUBMITTED) is False
        assert is_workflow_terminal(JobStatus.APPLYING) is False

    def test_is_url_closed(self):
        assert is_url_closed(JobStatus.COMPLETED) is True
        assert is_url_closed(JobStatus.APPLIED) is True
        assert is_url_closed(JobStatus.CANCELLED) is True
        assert is_url_closed(JobStatus.APPLYING) is False
        assert is_url_closed(JobStatus.APPLY_QUEUED) is False
        assert is_url_closed(JobStatus.SUBMITTED) is False

    def test_in_flight_includes_applying(self):
        assert JobStatus.APPLYING in IN_FLIGHT_STATES
        assert JobStatus.EXTRACTING in IN_FLIGHT_STATES
        assert JobStatus.GENERATING in IN_FLIGHT_STATES


class TestApplyData:
    def test_default_values(self):
        data = ApplyData()
        assert data.screenshot_path is None
        assert data.result_path is None
        assert data.form_filled is False
        assert data.resume_uploaded is False
        assert data.applied_at is None
        assert data.fields_filled == []
