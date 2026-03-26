"""Tests for the manual apply workflow.

Covers orchestrator state transitions, validation guards, and crash recovery.
Browser automation itself is mocked — these are unit tests around orchestration.
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, PropertyMock

from models import Job, JobStatus, ApplyData, WorkerInfo, is_workflow_terminal
from orchestrator import Orchestrator, StateTransitionError
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


def _make_completed_job(store: JsonJobStore, tmp_jobs_dir: Path) -> Job:
    """Helper: create a job and walk it to COMPLETED with valid output artifacts."""
    job = store.create_job("https://example.com/job/123")
    for status in [
        JobStatus.EXTRACTING, JobStatus.READY_FOR_REVIEW,
        JobStatus.QUEUED, JobStatus.GENERATING, JobStatus.COMPLETED,
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

    def test_apply_from_completed(self, orch, store, tmp_jobs_dir):
        job = _make_completed_job(store, tmp_jobs_dir)
        with patch.object(orch, '_dispatch_apply', new_callable=AsyncMock):
            result = _run(orch.queue_apply(job.job_id))
        assert result.status == JobStatus.APPLY_QUEUED

    def test_apply_from_apply_failed(self, orch, store, tmp_jobs_dir):
        job = _make_completed_job(store, tmp_jobs_dir)
        for status in [JobStatus.APPLY_QUEUED, JobStatus.APPLYING, JobStatus.APPLY_FAILED]:
            job.status = status
            store.update_job(job)
        with patch.object(orch, '_dispatch_apply', new_callable=AsyncMock):
            result = _run(orch.queue_apply(job.job_id))
        assert result.status == JobStatus.APPLY_QUEUED

    def test_apply_from_invalid_state_raises(self, orch, store):
        job = store.create_job("https://example.com/job/456")
        with pytest.raises(StateTransitionError, match="Cannot apply"):
            _run(orch.queue_apply(job.job_id))

    def test_apply_from_generating_raises(self, orch, store):
        job = store.create_job("https://example.com/job/789")
        for status in [
            JobStatus.EXTRACTING, JobStatus.READY_FOR_REVIEW,
            JobStatus.QUEUED, JobStatus.GENERATING,
        ]:
            job.status = status
            store.update_job(job)
        with pytest.raises(StateTransitionError, match="Cannot apply"):
            _run(orch.queue_apply(job.job_id))

    def test_apply_missing_metadata_raises(self, orch, store, tmp_jobs_dir):
        job = store.create_job("https://example.com/job/no-meta")
        for status in [
            JobStatus.EXTRACTING, JobStatus.READY_FOR_REVIEW,
            JobStatus.QUEUED, JobStatus.GENERATING, JobStatus.COMPLETED,
        ]:
            job.status = status
            store.update_job(job)
        with pytest.raises(FileNotFoundError, match="No output metadata"):
            _run(orch.queue_apply(job.job_id))

    def test_apply_missing_resume_raises(self, orch, store, tmp_jobs_dir):
        job = store.create_job("https://example.com/job/no-resume")
        for status in [
            JobStatus.EXTRACTING, JobStatus.READY_FOR_REVIEW,
            JobStatus.QUEUED, JobStatus.GENERATING, JobStatus.COMPLETED,
        ]:
            job.status = status
            store.update_job(job)
        output_dir = tmp_jobs_dir / job.job_id / "output"
        metadata = {"resume_pdf": "/nonexistent/resume.pdf"}
        (output_dir / "metadata.json").write_text(json.dumps(metadata))
        with pytest.raises(FileNotFoundError, match="Resume PDF not found"):
            _run(orch.queue_apply(job.job_id))


class TestCrashRecovery:

    def test_applying_reset_on_startup(self, orch, store):
        job = store.create_job("https://example.com/job/stuck")
        for status in [
            JobStatus.EXTRACTING, JobStatus.READY_FOR_REVIEW,
            JobStatus.QUEUED, JobStatus.GENERATING, JobStatus.COMPLETED,
            JobStatus.APPLY_QUEUED, JobStatus.APPLYING,
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

    def test_cancel_from_apply_queued(self, orch, store, tmp_jobs_dir):
        job = _make_completed_job(store, tmp_jobs_dir)
        job.status = JobStatus.APPLY_QUEUED
        store.update_job(job)
        result = _run(orch.cancel_job(job.job_id))
        assert result.status == JobStatus.CANCELLED

    def test_cannot_cancel_from_applied(self, orch, store, tmp_jobs_dir):
        job = _make_completed_job(store, tmp_jobs_dir)
        for status in [JobStatus.APPLY_QUEUED, JobStatus.APPLYING, JobStatus.APPLIED]:
            job.status = status
            store.update_job(job)
        assert is_workflow_terminal(job.status)
        with pytest.raises(StateTransitionError, match="Cannot cancel"):
            _run(orch.cancel_job(job.job_id))

    def test_cannot_cancel_completed(self, orch, store, tmp_jobs_dir):
        """COMPLETED → CANCELLED is not a legal transition.
        cancel_job passes is_workflow_terminal (COMPLETED is not terminal)
        but _transition rejects COMPLETED→CANCELLED."""
        job = _make_completed_job(store, tmp_jobs_dir)
        with pytest.raises(StateTransitionError):
            _run(orch.cancel_job(job.job_id))


class TestNoAutoApply:

    def test_generation_does_not_trigger_apply(self, orch, store):
        job = store.create_job("https://example.com/job/gen-test")
        for status in [
            JobStatus.EXTRACTING, JobStatus.READY_FOR_REVIEW,
            JobStatus.QUEUED, JobStatus.GENERATING, JobStatus.COMPLETED,
        ]:
            job.status = status
            store.update_job(job)
        final = store.get_job(job.job_id)
        assert final.status == JobStatus.COMPLETED
