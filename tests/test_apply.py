"""Tests for the manual apply workflow (schema v2).

Covers orchestrator state transitions, validation guards, and crash recovery.
Browser automation itself is mocked — these are unit tests around orchestration.
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, PropertyMock

from models import (
    Job,
    JobStatus,
    ApplyData,
    ExtractionData,
    ReviewData,
    WorkerInfo,
    is_workflow_terminal,
)
from orchestrator import (
    Orchestrator,
    StateTransitionError,
    _is_linkedin_url,
    _should_skip_generation,
)
from persistence import JsonJobStore


@pytest.fixture
def event_loop():
    """Create event loop for Python 3.9 compat (asyncio.Semaphore needs it)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def tmp_jobs_dir(tmp_path):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    return jobs_dir


@pytest.fixture
def store(tmp_jobs_dir):
    return JsonJobStore(jobs_dir=tmp_jobs_dir)


@pytest.fixture
def orch(store, event_loop, tmp_jobs_dir):
    """Create Orchestrator, patching config.JOBS_DIR to use tmp dir."""
    asyncio.set_event_loop(event_loop)
    with patch('orchestrator.config') as mock_config:
        mock_config.JOBS_DIR = tmp_jobs_dir
        mock_config.GENERATION_LOCK_FILE = tmp_jobs_dir / ".generation_lock"
        mock_config.APPLY_TIMEOUT_SECONDS = 600
        mock_config.APPLY_SCRIPT = Path("/fake/apply_job.py")
        mock_config.BROWSER_USE_PROJECT_DIR = Path("/fake/browser-use")
        mock_config.PROFILE_PATH = Path("/fake/profile.json")
        mock_config.PROJECT_DIR = Path("/fake")
        o = Orchestrator(store)
        o.set_event_queue(asyncio.Queue())
        o._mock_config = mock_config  # keep reference alive
        yield o


def _run(coro):
    """Run async code in the current event loop."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


def _make_generated_job(store: JsonJobStore, tmp_jobs_dir: Path) -> Job:
    """Helper: create a job and walk it to GENERATED with valid output artifacts."""
    job = store.create_job("https://example.com/job/123")
    for status in [
        JobStatus.EXTRACTING, JobStatus.SCRAPED,
        JobStatus.QUEUED, JobStatus.GENERATING, JobStatus.GENERATED,
    ]:
        job.status = status
        store.update_job(job)

    output_dir = tmp_jobs_dir / job.job_id / "output"
    resume_pdf = output_dir / "resume.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4 fake resume")
    cover_letter_pdf = output_dir / "cover_letter.pdf"
    cover_letter_pdf.write_bytes(b"%PDF-1.4 fake cover letter")
    metadata = {
        "job_id": job.job_id,
        "source_url": job.source_url,
        "resume_pdf": str(resume_pdf),
        "cover_letter_pdf": str(cover_letter_pdf),
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata))
    return job


class TestQueueApplyValidation:

    def test_apply_from_generated(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        with patch.object(orch, '_dispatch_apply', new_callable=AsyncMock):
            result = _run(orch.queue_apply(job.job_id))
        assert result.status == JobStatus.APPLYING

    def test_apply_from_scored(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        job.status = JobStatus.SCORED
        store.update_job(job)
        with patch.object(orch, '_dispatch_apply', new_callable=AsyncMock):
            result = _run(orch.queue_apply(job.job_id))
        assert result.status == JobStatus.APPLYING

    def test_apply_from_apply_failed(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        for status in [JobStatus.APPLYING, JobStatus.APPLY_FAILED]:
            job.status = status
            store.update_job(job)
        with patch.object(orch, '_dispatch_apply', new_callable=AsyncMock):
            result = _run(orch.queue_apply(job.job_id))
        assert result.status == JobStatus.APPLYING

    def test_apply_from_invalid_state_raises(self, orch, store):
        job = store.create_job("https://example.com/job/456")
        with pytest.raises(StateTransitionError, match="Cannot apply"):
            _run(orch.queue_apply(job.job_id))

    def test_apply_from_generating_raises(self, orch, store):
        job = store.create_job("https://example.com/job/789")
        for status in [
            JobStatus.EXTRACTING, JobStatus.SCRAPED,
            JobStatus.QUEUED, JobStatus.GENERATING,
        ]:
            job.status = status
            store.update_job(job)
        with pytest.raises(StateTransitionError, match="Cannot apply"):
            _run(orch.queue_apply(job.job_id))

    @pytest.mark.invariant
    def test_invariant_apply_blocked_from_needs_bullet_approval(self, orch, store):
        job = store.create_job("https://example.com/job/needs-bullets")
        job.status = JobStatus.NEEDS_BULLET_APPROVAL
        store.update_job(job)

        with pytest.raises(StateTransitionError, match="Cannot apply"):
            _run(orch.queue_apply(job.job_id))

    def test_apply_missing_metadata_raises(self, orch, store, tmp_jobs_dir):
        job = store.create_job("https://example.com/job/no-meta")
        for status in [
            JobStatus.EXTRACTING, JobStatus.SCRAPED,
            JobStatus.QUEUED, JobStatus.GENERATING, JobStatus.GENERATED,
        ]:
            job.status = status
            store.update_job(job)
        with pytest.raises(FileNotFoundError, match="No output metadata"):
            _run(orch.queue_apply(job.job_id))

    def test_apply_missing_resume_raises(self, orch, store, tmp_jobs_dir):
        job = store.create_job("https://example.com/job/no-resume")
        for status in [
            JobStatus.EXTRACTING, JobStatus.SCRAPED,
            JobStatus.QUEUED, JobStatus.GENERATING, JobStatus.GENERATED,
        ]:
            job.status = status
            store.update_job(job)
        output_dir = tmp_jobs_dir / job.job_id / "output"
        metadata = {"resume_pdf": "/nonexistent/resume.pdf"}
        (output_dir / "metadata.json").write_text(json.dumps(metadata))
        with pytest.raises(FileNotFoundError, match="Resume PDF not found"):
            _run(orch.queue_apply(job.job_id))


class TestLinkedInQueueApply:

    def test_easy_apply_goes_to_manual_apply_without_artifacts(self, orch, store):
        job = store.create_job("https://www.linkedin.com/jobs/view/123")
        for status in [
            JobStatus.EXTRACTING, JobStatus.SCRAPED,
            JobStatus.QUEUED, JobStatus.GENERATED,
        ]:
            job.status = status
            store.update_job(job)
        job.extraction = ExtractionData(apply_method="easy_apply")
        store.update_job(job)
        with patch('orchestrator.subprocess'):
            result = _run(orch.queue_apply(job.job_id))
        assert result.status == JobStatus.MANUAL_APPLY

    def test_linkedin_apply_requires_generated_artifacts(self, orch, store):
        job = store.create_job("https://www.linkedin.com/jobs/view/123")
        for status in [
            JobStatus.EXTRACTING, JobStatus.SCRAPED,
            JobStatus.QUEUED, JobStatus.GENERATED,
        ]:
            job.status = status
            store.update_job(job)
        job.extraction = ExtractionData(apply_method="apply")
        store.update_job(job)

        with pytest.raises(FileNotFoundError, match="No output metadata"):
            _run(orch.queue_apply(job.job_id))

    def test_non_linkedin_url_goes_to_applying(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        with patch.object(orch, '_dispatch_apply', new_callable=AsyncMock):
            result = _run(orch.queue_apply(job.job_id))
        assert result.status == JobStatus.APPLYING


class TestCrashRecovery:

    def test_applying_reset_on_startup(self, orch, store):
        job = store.create_job("https://example.com/job/stuck")
        for status in [
            JobStatus.EXTRACTING, JobStatus.SCRAPED,
            JobStatus.QUEUED, JobStatus.GENERATING, JobStatus.GENERATED,
            JobStatus.APPLYING,
        ]:
            job.status = status
            store.update_job(job)
        job.worker = WorkerInfo(pid=99999, pgid=99999)
        store.update_job(job)

        _run(orch.recover_on_startup())

        recovered = store.get_job(job.job_id)
        assert recovered.status == JobStatus.APPLY_FAILED
        assert recovered.error.last_error == "Server restarted during apply"
        assert recovered.worker.pid is None


class TestCancelWithApplyStates:

    def test_cancel_from_applying(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        job.status = JobStatus.APPLYING
        store.update_job(job)
        result = _run(orch.cancel_job(job.job_id))
        assert result.status == JobStatus.CANCELLED

    def test_cannot_cancel_from_applied(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        for status in [JobStatus.APPLYING, JobStatus.APPLIED]:
            job.status = status
            store.update_job(job)
        assert is_workflow_terminal(job.status)
        with pytest.raises(StateTransitionError, match="Cannot cancel"):
            _run(orch.cancel_job(job.job_id))

    def test_cancel_from_generated(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        result = _run(orch.cancel_job(job.job_id))
        assert result.status == JobStatus.CANCELLED

    def test_cancel_from_scored(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        job.status = JobStatus.SCORED
        store.update_job(job)
        result = _run(orch.cancel_job(job.job_id))
        assert result.status == JobStatus.CANCELLED


class TestNoAutoApply:

    def test_generation_does_not_trigger_apply(self, orch, store):
        job = store.create_job("https://example.com/job/gen-test")
        for status in [
            JobStatus.EXTRACTING, JobStatus.SCRAPED,
            JobStatus.QUEUED, JobStatus.GENERATING, JobStatus.GENERATED,
        ]:
            job.status = status
            store.update_job(job)
        final = store.get_job(job.job_id)
        assert final.status == JobStatus.GENERATED


class TestLinkedInDetection:

    def test_linkedin_com_detected(self):
        assert _is_linkedin_url("https://www.linkedin.com/jobs/view/123")

    def test_linkedin_no_www(self):
        assert _is_linkedin_url("https://linkedin.com/jobs/view/456")

    def test_country_subdomain(self):
        assert _is_linkedin_url("https://ca.linkedin.com/jobs/view/789")

    def test_non_linkedin_url(self):
        assert not _is_linkedin_url("https://example.com/jobs/apply")

    def test_linkedin_in_path_not_detected(self):
        assert not _is_linkedin_url("https://example.com/linkedin/jobs")

    def test_empty_url(self):
        assert not _is_linkedin_url("")

    def test_invalid_url(self):
        assert not _is_linkedin_url("not a url")


class TestExtractionApplyMethodParsing:

    def test_parse_output_normalizes_linkedin_apply_button(self, orch, tmp_path):
        output = tmp_path / "extraction.json"
        output.write_text(json.dumps({
            "role_title": "Engineer",
            "company_name": "Acme",
            "job_description": "Build things",
            "apply_method": "APPLY",
        }))

        extraction = orch._parse_extraction_output(output)

        assert extraction.apply_method == "apply"

    def test_parse_output_normalizes_easy_apply_button(self, orch, tmp_path):
        output = tmp_path / "extraction.json"
        output.write_text(json.dumps({
            "role_title": "Engineer",
            "company_name": "Acme",
            "job_description": "Build things",
            "apply_method": "Easy Apply",
        }))

        extraction = orch._parse_extraction_output(output)

        assert extraction.apply_method == "easy_apply"


def _make_review_required_job(store, tmp_jobs_dir):
    """Helper: create a job and walk it to REVIEW_REQUIRED."""
    job = _make_generated_job(store, tmp_jobs_dir)
    for status in [JobStatus.APPLYING, JobStatus.REVIEW_REQUIRED]:
        job.status = status
        store.update_job(job)
    return job


def _make_manual_apply_job(store, tmp_jobs_dir):
    """Helper: create a job at MANUAL_APPLY state."""
    job = _make_generated_job(store, tmp_jobs_dir)
    job.status = JobStatus.MANUAL_APPLY
    store.update_job(job)
    return job


class TestApproveApply:

    def test_approve_from_review_required(self, orch, store, tmp_jobs_dir):
        job = _make_review_required_job(store, tmp_jobs_dir)
        result = _run(orch.approve_apply(job.job_id, "approve"))
        assert result.status == JobStatus.APPLIED
        assert result.apply.applied_at is not None

    def test_reject_from_review_required(self, orch, store, tmp_jobs_dir):
        job = _make_review_required_job(store, tmp_jobs_dir)
        result = _run(orch.approve_apply(job.job_id, "reject", "Fields look wrong"))
        assert result.status == JobStatus.APPLY_FAILED
        assert "Fields look wrong" in result.error.last_error

    def test_approve_from_wrong_state_raises(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        with pytest.raises(StateTransitionError, match="Cannot approve-apply"):
            _run(orch.approve_apply(job.job_id, "approve"))

    def test_invalid_action_raises(self, orch, store, tmp_jobs_dir):
        job = _make_review_required_job(store, tmp_jobs_dir)
        with pytest.raises(StateTransitionError, match="Invalid action"):
            _run(orch.approve_apply(job.job_id, "maybe"))


class TestMarkApplied:

    def test_mark_applied_from_manual_apply(self, orch, store, tmp_jobs_dir):
        job = _make_manual_apply_job(store, tmp_jobs_dir)
        result = _run(orch.mark_applied(job.job_id))
        assert result.status == JobStatus.APPLIED
        assert result.apply.applied_at is not None

    def test_mark_applied_from_wrong_state_raises(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        with pytest.raises(StateTransitionError, match="Cannot mark-applied"):
            _run(orch.mark_applied(job.job_id))


class TestCrashRecoveryNewStates:

    def test_review_required_survives_restart(self, orch, store, tmp_jobs_dir):
        job = _make_review_required_job(store, tmp_jobs_dir)
        _run(orch.recover_on_startup())
        recovered = store.get_job(job.job_id)
        assert recovered.status == JobStatus.REVIEW_REQUIRED

    def test_manual_apply_survives_restart(self, orch, store, tmp_jobs_dir):
        job = _make_manual_apply_job(store, tmp_jobs_dir)
        _run(orch.recover_on_startup())
        recovered = store.get_job(job.job_id)
        assert recovered.status == JobStatus.MANUAL_APPLY


class TestLinkedInGenerationSkip:

    def test_easy_apply_skips_generation(self, store):
        """Easy Apply is the only generation-skip signal."""
        job = store.create_job("https://www.linkedin.com/jobs/view/123")
        job.extraction = ExtractionData(apply_method="easy_apply")
        job.is_linkedin = True
        store.update_job(job)

        assert _should_skip_generation(job) is True

    def test_easy_apply_display_text_skips_generation(self, store):
        job = store.create_job("https://www.linkedin.com/jobs/view/123")
        job.extraction = ExtractionData(apply_method="Easy Apply")
        job.is_linkedin = True
        store.update_job(job)

        assert _should_skip_generation(job) is True

    def test_linkedin_apply_does_not_skip_generation(self, store):
        job = store.create_job("https://www.linkedin.com/jobs/view/456")
        job.extraction = ExtractionData(apply_method="apply")
        job.is_linkedin = True
        store.update_job(job)

        assert _should_skip_generation(job) is False

    def test_linkedin_uppercase_apply_does_not_skip_generation(self, store):
        job = store.create_job("https://www.linkedin.com/jobs/view/456")
        job.extraction = ExtractionData(apply_method="APPLY")
        job.is_linkedin = True
        store.update_job(job)

        assert _should_skip_generation(job) is False

    def test_unknown_apply_method_does_not_skip_generation(self, store):
        job = store.create_job("https://www.linkedin.com/jobs/view/789")
        job.extraction = ExtractionData(apply_method="unknown")
        job.is_linkedin = True
        store.update_job(job)

        assert _should_skip_generation(job) is False

    def test_is_linkedin_set_during_extraction(self, orch, store):
        assert _is_linkedin_url("https://www.linkedin.com/jobs/view/123") is True
        assert _is_linkedin_url("https://example.com/jobs") is False


class TestCancelNewStates:

    def test_cancel_from_review_required(self, orch, store, tmp_jobs_dir):
        job = _make_review_required_job(store, tmp_jobs_dir)
        result = _run(orch.cancel_job(job.job_id))
        assert result.status == JobStatus.CANCELLED
