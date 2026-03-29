"""Tests for Job data models and state machine (schema v2)."""

import pytest
from models import (
    ApplyData,
    FieldReview,
    GenerationData,
    Job,
    JobStatus,
    ExtractionData,
    ReviewData,
    ScoreData,
    SCHEMA_VERSION,
    STATUS_MIGRATION,
    is_legal_transition,
    is_workflow_terminal,
    is_url_closed,
    LEGAL_TRANSITIONS,
    WORKFLOW_TERMINAL,
    URL_CLOSED,
    IN_FLIGHT_STATES,
    EDITABLE_STATES,
)


class TestJobModel:
    def test_default_creation(self):
        job = Job(source_url="https://example.com/job/1")
        assert job.source_url == "https://example.com/job/1"
        assert job.status == JobStatus.SUBMITTED
        assert job.schema_version == SCHEMA_VERSION
        assert job.schema_version == 2
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

    def test_default_score_data(self):
        job = Job(source_url="https://example.com/job/1")
        assert job.score.overall_score is None
        assert job.score.keyword_score is None
        assert job.score.semantic_score is None
        assert job.score.algorithm is None
        assert job.score.requirement_scores == []
        assert job.score.computed_at is None

    def test_default_generation_data(self):
        job = Job(source_url="https://example.com/job/1")
        assert job.generation.output_folder is None
        assert job.generation.resume_path is None
        assert job.generation.cover_letter_path is None
        assert job.generation.selection_log_path is None
        assert job.generation.completed_at is None

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

    def test_serialization_with_new_fields(self):
        job = Job(source_url="https://example.com/job/1")
        job.generation = GenerationData(
            output_folder="/path/to/output",
            resume_path="/path/to/resume.pdf",
            cover_letter_path="/path/to/cl.pdf",
            selection_log_path="/path/to/sel.json",
        )
        job.score = ScoreData(
            overall_score=72.5,
            keyword_score=65.0,
            semantic_score=80.0,
            algorithm="jd-match-resume",
        )
        json_str = job.model_dump_json()
        restored = Job.model_validate_json(json_str)
        assert restored.generation.resume_path == "/path/to/resume.pdf"
        assert restored.score.overall_score == 72.5
        assert restored.score.algorithm == "jd-match-resume"

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
                    f"{from_state} -> {to_state} should be legal"
                )

    def test_workflow_terminal_states_have_no_transitions(self):
        for state in WORKFLOW_TERMINAL:
            assert LEGAL_TRANSITIONS[state] == set(), (
                f"Workflow-terminal state {state} should have no outgoing transitions"
            )

    def test_illegal_transitions(self):
        assert not is_legal_transition(JobStatus.GENERATED, JobStatus.EXTRACTING)
        assert not is_legal_transition(JobStatus.CANCELLED, JobStatus.QUEUED)
        assert not is_legal_transition(JobStatus.SUBMITTED, JobStatus.GENERATED)
        assert not is_legal_transition(JobStatus.QUEUED, JobStatus.EXTRACTING)

    def test_happy_path_transitions(self):
        """submitted -> extracting -> scraped -> queued -> generating -> generated"""
        path = [
            (JobStatus.SUBMITTED, JobStatus.EXTRACTING),
            (JobStatus.EXTRACTING, JobStatus.SCRAPED),
            (JobStatus.SCRAPED, JobStatus.QUEUED),
            (JobStatus.QUEUED, JobStatus.GENERATING),
            (JobStatus.GENERATING, JobStatus.GENERATED),
        ]
        for from_s, to_s in path:
            assert is_legal_transition(from_s, to_s)

    def test_scored_path(self):
        """generated -> scored -> applying -> applied"""
        path = [
            (JobStatus.GENERATED, JobStatus.SCORED),
            (JobStatus.SCORED, JobStatus.APPLYING),
            (JobStatus.APPLYING, JobStatus.APPLIED),
        ]
        for from_s, to_s in path:
            assert is_legal_transition(from_s, to_s)

    def test_skip_score_path(self):
        """generated -> applying -> applied (skip score)"""
        assert is_legal_transition(JobStatus.GENERATED, JobStatus.APPLYING)
        assert is_legal_transition(JobStatus.APPLYING, JobStatus.APPLIED)

    def test_apply_failure_and_retry(self):
        """applying -> apply_failed -> applying (retry)"""
        assert is_legal_transition(JobStatus.APPLYING, JobStatus.APPLY_FAILED)
        assert is_legal_transition(JobStatus.APPLY_FAILED, JobStatus.APPLYING)

    def test_apply_cancellation(self):
        assert is_legal_transition(JobStatus.APPLYING, JobStatus.CANCELLED)
        assert is_legal_transition(JobStatus.APPLY_FAILED, JobStatus.CANCELLED)

    def test_applied_is_terminal(self):
        assert LEGAL_TRANSITIONS[JobStatus.APPLIED] == set()

    def test_extraction_failed_to_scraped(self):
        """Manual fill path: extraction_failed -> scraped"""
        assert is_legal_transition(JobStatus.EXTRACTION_FAILED, JobStatus.SCRAPED)

    def test_retry_transitions(self):
        assert is_legal_transition(JobStatus.EXTRACTION_FAILED, JobStatus.EXTRACTING)
        assert is_legal_transition(JobStatus.GENERATION_FAILED, JobStatus.GENERATING)
        assert is_legal_transition(JobStatus.APPLY_FAILED, JobStatus.APPLYING)

    def test_cancel_from_cancellable_states(self):
        """All states except workflow-terminal and MANUAL_APPLY can be cancelled."""
        non_cancellable = {JobStatus.MANUAL_APPLY}
        cancellable = [
            s for s in JobStatus
            if not is_workflow_terminal(s) and s not in non_cancellable
        ]
        for state in cancellable:
            assert is_legal_transition(state, JobStatus.CANCELLED), (
                f"Should be able to cancel from {state}"
            )

    def test_manual_apply_can_be_cancelled(self):
        """MANUAL_APPLY allows APPLIED and CANCELLED."""
        assert is_legal_transition(JobStatus.MANUAL_APPLY, JobStatus.CANCELLED)

    def test_generated_does_not_auto_apply(self):
        """GENERATED allows APPLYING but must be explicitly triggered."""
        assert is_legal_transition(JobStatus.GENERATED, JobStatus.APPLYING)
        # Cannot go back to extraction
        assert not is_legal_transition(JobStatus.GENERATED, JobStatus.EXTRACTING)

    def test_apply_queued_removed(self):
        """APPLY_QUEUED is no longer a valid status."""
        assert not hasattr(JobStatus, "APPLY_QUEUED")

    def test_old_status_names_removed(self):
        """READY_FOR_REVIEW and COMPLETED are removed from enum."""
        assert not hasattr(JobStatus, "READY_FOR_REVIEW")
        assert not hasattr(JobStatus, "COMPLETED")


class TestPredicates:
    def test_is_workflow_terminal(self):
        assert is_workflow_terminal(JobStatus.APPLIED) is True
        assert is_workflow_terminal(JobStatus.CANCELLED) is True
        assert is_workflow_terminal(JobStatus.GENERATED) is False
        assert is_workflow_terminal(JobStatus.SCORED) is False
        assert is_workflow_terminal(JobStatus.SUBMITTED) is False
        assert is_workflow_terminal(JobStatus.APPLYING) is False

    def test_is_url_closed(self):
        assert is_url_closed(JobStatus.GENERATED) is True
        assert is_url_closed(JobStatus.SCORED) is True
        assert is_url_closed(JobStatus.APPLIED) is True
        assert is_url_closed(JobStatus.CANCELLED) is True
        assert is_url_closed(JobStatus.MANUAL_APPLY) is True
        assert is_url_closed(JobStatus.APPLYING) is False
        assert is_url_closed(JobStatus.SUBMITTED) is False

    def test_in_flight_includes_applying(self):
        assert JobStatus.APPLYING in IN_FLIGHT_STATES
        assert JobStatus.EXTRACTING in IN_FLIGHT_STATES
        assert JobStatus.GENERATING in IN_FLIGHT_STATES

    def test_editable_states(self):
        assert JobStatus.SCRAPED in EDITABLE_STATES
        assert JobStatus.EXTRACTION_FAILED in EDITABLE_STATES
        assert JobStatus.GENERATED not in EDITABLE_STATES


class TestApplyData:
    def test_default_values(self):
        data = ApplyData()
        assert data.screenshot_path is None
        assert data.result_path is None
        assert data.form_filled is False
        assert data.resume_uploaded is False
        assert data.applied_at is None
        assert data.fields_filled == []


class TestScoreData:
    def test_default_values(self):
        data = ScoreData()
        assert data.overall_score is None
        assert data.algorithm is None
        assert data.requirement_scores == []

    def test_serialization_roundtrip(self):
        data = ScoreData(
            overall_score=72.5,
            keyword_score=65.0,
            semantic_score=80.0,
            algorithm="jd-match-resume",
            requirement_scores=[{"req": "Python", "score": 0.9}],
        )
        json_str = data.model_dump_json()
        restored = ScoreData.model_validate_json(json_str)
        assert restored.overall_score == 72.5
        assert restored.algorithm == "jd-match-resume"
        assert len(restored.requirement_scores) == 1


class TestNewStates:
    """Tests for MANUAL_APPLY, REVIEW_REQUIRED, SCRAPED, GENERATED, SCORED."""

    def test_scraped_in_enum(self):
        assert JobStatus.SCRAPED == "scraped"

    def test_generated_in_enum(self):
        assert JobStatus.GENERATED == "generated"

    def test_scored_in_enum(self):
        assert JobStatus.SCORED == "scored"

    def test_manual_apply_in_enum(self):
        assert JobStatus.MANUAL_APPLY == "manual_apply"

    def test_review_required_in_enum(self):
        assert JobStatus.REVIEW_REQUIRED == "review_required"

    def test_manual_apply_transitions(self):
        # GENERATED/SCORED -> MANUAL_APPLY (LinkedIn handoff)
        assert is_legal_transition(JobStatus.GENERATED, JobStatus.MANUAL_APPLY)
        assert is_legal_transition(JobStatus.SCORED, JobStatus.MANUAL_APPLY)
        # MANUAL_APPLY -> APPLIED (user confirms)
        assert is_legal_transition(JobStatus.MANUAL_APPLY, JobStatus.APPLIED)

    def test_review_required_transitions(self):
        # APPLYING -> REVIEW_REQUIRED (post-fill review)
        assert is_legal_transition(JobStatus.APPLYING, JobStatus.REVIEW_REQUIRED)
        # REVIEW_REQUIRED -> APPLIED (approve)
        assert is_legal_transition(JobStatus.REVIEW_REQUIRED, JobStatus.APPLIED)
        # REVIEW_REQUIRED -> APPLY_FAILED (reject)
        assert is_legal_transition(JobStatus.REVIEW_REQUIRED, JobStatus.APPLY_FAILED)
        # REVIEW_REQUIRED -> CANCELLED
        assert is_legal_transition(JobStatus.REVIEW_REQUIRED, JobStatus.CANCELLED)

    def test_manual_apply_illegal_transitions(self):
        assert not is_legal_transition(JobStatus.MANUAL_APPLY, JobStatus.EXTRACTING)
        assert not is_legal_transition(JobStatus.MANUAL_APPLY, JobStatus.GENERATING)

    def test_manual_apply_not_in_flight(self):
        assert JobStatus.MANUAL_APPLY not in IN_FLIGHT_STATES

    def test_review_required_not_in_flight(self):
        assert JobStatus.REVIEW_REQUIRED not in IN_FLIGHT_STATES

    def test_manual_apply_in_url_closed(self):
        assert is_url_closed(JobStatus.MANUAL_APPLY)

    def test_manual_apply_not_workflow_terminal(self):
        assert not is_workflow_terminal(JobStatus.MANUAL_APPLY)

    def test_review_required_not_workflow_terminal(self):
        assert not is_workflow_terminal(JobStatus.REVIEW_REQUIRED)

    def test_review_happy_path(self):
        assert is_legal_transition(JobStatus.APPLYING, JobStatus.REVIEW_REQUIRED)
        assert is_legal_transition(JobStatus.REVIEW_REQUIRED, JobStatus.APPLIED)


class TestStatusMigration:
    """Tests for backward compatibility status migration."""

    def test_migration_map_exists(self):
        assert "ready_for_review" in STATUS_MIGRATION
        assert "completed" in STATUS_MIGRATION
        assert "apply_queued" in STATUS_MIGRATION

    def test_ready_for_review_maps_to_scraped(self):
        assert STATUS_MIGRATION["ready_for_review"] == "scraped"

    def test_completed_maps_to_generated(self):
        assert STATUS_MIGRATION["completed"] == "generated"

    def test_apply_queued_maps_to_generated(self):
        assert STATUS_MIGRATION["apply_queued"] == "generated"

    def test_current_statuses_not_in_migration(self):
        """Current valid status values should not need migration."""
        for status in JobStatus:
            assert status.value not in STATUS_MIGRATION


class TestReviewData:
    def test_default_values(self):
        data = ReviewData()
        assert data.overall_confidence == 0.0
        assert data.fields == []
        assert data.flags == []
        assert data.recommendation == "review_required"
        assert data.screenshot_path is None
        assert data.reviewed_at is None

    def test_field_review_defaults(self):
        field = FieldReview(field_name="name", filled_value="John")
        assert field.source == "unknown"
        assert field.confidence == 0.0
        assert field.issue is None

    def test_serialization_roundtrip(self):
        data = ReviewData(
            overall_confidence=0.85,
            fields=[
                FieldReview(
                    field_name="Full Name",
                    filled_value="Felix",
                    source="profile.full_name",
                    confidence=1.0,
                )
            ],
            flags=["Work auth inferred"],
            recommendation="review_required",
        )
        json_str = data.model_dump_json()
        restored = ReviewData.model_validate_json(json_str)
        assert restored.overall_confidence == 0.85
        assert len(restored.fields) == 1
        assert restored.fields[0].field_name == "Full Name"
        assert restored.flags == ["Work auth inferred"]

    def test_job_with_review_data(self):
        job = Job(source_url="https://example.com/job/1")
        job.review = ReviewData(
            overall_confidence=0.9,
            fields=[FieldReview(field_name="email", filled_value="a@b.com", confidence=1.0)],
        )
        json_str = job.model_dump_json()
        restored = Job.model_validate_json(json_str)
        assert restored.review.overall_confidence == 0.9
        assert len(restored.review.fields) == 1


class TestLinkedInSkip:
    """Tests for LinkedIn Easy Apply generation skip."""

    def test_is_linkedin_default_false(self):
        job = Job(source_url="https://example.com/job/1")
        assert job.is_linkedin is False

    def test_is_linkedin_serialization(self):
        job = Job(source_url="https://www.linkedin.com/jobs/view/123")
        job.is_linkedin = True
        json_str = job.model_dump_json()
        restored = Job.model_validate_json(json_str)
        assert restored.is_linkedin is True
