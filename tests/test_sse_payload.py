"""Tests for Wave 4: enriched SSE event payloads.

Verifies _job_summary() includes the right fields at each lifecycle stage.
"""

from datetime import datetime, timezone

from models import (
    ExtractionData,
    GenerationData,
    Job,
    JobStatus,
    RequirementItem,
    ScoreData,
)
from orchestrator import _job_summary


class TestSSEPayloadBasic:

    def test_submitted_job_has_minimal_fields(self):
        job = Job(source_url="https://example.com/job/1")
        summary = _job_summary(job)
        assert summary["job_id"] == job.job_id
        assert summary["source_url"] == "https://example.com/job/1"
        assert summary["status"] == "submitted"
        assert "jd_text" not in summary
        assert "generation" not in summary
        assert "score" not in summary
        assert "requirements_count" not in summary

    def test_scraped_job_includes_jd_text(self):
        job = Job(source_url="https://example.com/job/2")
        job.status = JobStatus.SCRAPED
        job.extraction = ExtractionData(
            role_title="Engineer",
            company_name="Acme",
            location="SF",
            salary="$150k",
            job_description="We need a Python expert with 5 years experience.",
        )
        summary = _job_summary(job)
        assert summary["status"] == "scraped"
        assert summary["role_title"] == "Engineer"
        assert summary["company_name"] == "Acme"
        assert "jd_text" in summary
        assert "Python expert" in summary["jd_text"]

    def test_generated_job_includes_generation_and_requirements(self):
        job = Job(source_url="https://example.com/job/3")
        job.status = JobStatus.GENERATED
        job.extraction = ExtractionData(job_description="Some JD text")
        job.generation = GenerationData(
            output_folder="/path/to/output",
            completed_at=datetime(2026, 3, 29, tzinfo=timezone.utc),
            resume_path="/path/to/resume.pdf",
            cover_letter_path="/path/to/cl.pdf",
            selection_log_path="/path/to/sel.json",
        )
        job.requirements = [
            RequirementItem(id="req_001", text="Python"),
            RequirementItem(id="req_002", text="CAD"),
        ]
        summary = _job_summary(job)
        assert summary["status"] == "generated"
        assert "generation" in summary
        assert summary["generation"]["resume_path"] == "/path/to/resume.pdf"
        assert summary["generation"]["cover_letter_path"] == "/path/to/cl.pdf"
        assert summary["requirements_count"] == 2
        assert "score" not in summary

    def test_scored_job_includes_score(self):
        job = Job(source_url="https://example.com/job/4")
        job.status = JobStatus.SCORED
        job.extraction = ExtractionData(job_description="JD")
        job.generation = GenerationData(
            completed_at=datetime(2026, 3, 29, tzinfo=timezone.utc),
            resume_path="/path/to/resume.pdf",
        )
        job.requirements = [RequirementItem(id="req_001", text="Python")]
        job.score = ScoreData(
            overall_score=72.5,
            keyword_score=65.0,
            semantic_score=80.0,
            algorithm="jd-match-resume",
        )
        summary = _job_summary(job)
        assert summary["status"] == "scored"
        assert "score" in summary
        assert summary["score"]["overall_score"] == 72.5
        assert summary["score"]["keyword_score"] == 65.0
        assert summary["score"]["semantic_score"] == 80.0
        assert summary["score"]["algorithm"] == "jd-match-resume"
        assert "generation" in summary
        assert summary["requirements_count"] == 1


class TestSSEPayloadEdgeCases:

    def test_error_always_included(self):
        job = Job(source_url="https://example.com")
        job.error.last_error = "Something failed"
        summary = _job_summary(job)
        assert summary["error"] == "Something failed"

    def test_no_error_is_none(self):
        job = Job(source_url="https://example.com")
        summary = _job_summary(job)
        assert summary["error"] is None

    def test_empty_requirements_not_included(self):
        job = Job(source_url="https://example.com")
        job.requirements = []
        summary = _job_summary(job)
        assert "requirements_count" not in summary

    def test_no_jd_text_not_included(self):
        job = Job(source_url="https://example.com")
        job.extraction = ExtractionData(role_title="Eng", job_description=None)
        summary = _job_summary(job)
        assert "jd_text" not in summary

    def test_generation_without_completed_at_not_included(self):
        job = Job(source_url="https://example.com")
        job.generation = GenerationData(output_folder="/some/path")
        summary = _job_summary(job)
        assert "generation" not in summary
