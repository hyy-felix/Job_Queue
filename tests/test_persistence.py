"""Tests for JsonJobStore persistence layer."""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from models import Job, JobStatus
from persistence import JsonJobStore, JobNotFoundError


@pytest.fixture
def store(tmp_path):
    """Create a JsonJobStore backed by a temp directory."""
    return JsonJobStore(jobs_dir=tmp_path / "jobs")


class TestCreateJob:
    def test_creates_workspace_dirs(self, store):
        job = store.create_job("https://example.com/job/1")
        job_dir = store._job_dir(job.job_id)
        assert (job_dir / "input").is_dir()
        assert (job_dir / "run").is_dir()
        assert (job_dir / "state").is_dir()
        assert (job_dir / "output").is_dir()

    def test_writes_status_json(self, store):
        job = store.create_job("https://example.com/job/1")
        status_path = store._status_path(job.job_id)
        assert status_path.exists()
        restored = Job.model_validate_json(status_path.read_text())
        assert restored.source_url == "https://example.com/job/1"
        assert restored.status == JobStatus.SUBMITTED

    def test_writes_source_url_file(self, store):
        job = store.create_job("https://example.com/job/1")
        url_file = store._job_dir(job.job_id) / "input" / "source_url.txt"
        assert url_file.read_text() == "https://example.com/job/1"

    def test_unique_ids(self, store):
        j1 = store.create_job("https://example.com/1")
        j2 = store.create_job("https://example.com/2")
        assert j1.job_id != j2.job_id


class TestGetJob:
    def test_get_existing(self, store):
        created = store.create_job("https://example.com/job/1")
        fetched = store.get_job(created.job_id)
        assert fetched.source_url == "https://example.com/job/1"
        assert fetched.job_id == created.job_id

    def test_get_nonexistent_raises(self, store):
        with pytest.raises(JobNotFoundError):
            store.get_job("nonexistent-id")


class TestListJobs:
    def test_empty_store(self, store):
        assert store.list_jobs() == []

    def test_lists_all_jobs(self, store):
        store.create_job("https://example.com/1")
        store.create_job("https://example.com/2")
        store.create_job("https://example.com/3")
        jobs = store.list_jobs()
        assert len(jobs) == 3

    def test_sorted_by_created_at(self, store):
        j1 = store.create_job("https://example.com/1")
        j2 = store.create_job("https://example.com/2")
        jobs = store.list_jobs()
        assert jobs[0].job_id == j1.job_id
        assert jobs[1].job_id == j2.job_id


class TestUpdateJob:
    def test_updates_status(self, store):
        job = store.create_job("https://example.com/1")
        job.status = JobStatus.EXTRACTING
        updated = store.update_job(job)
        assert updated.status == JobStatus.EXTRACTING
        assert updated.version == 2

    def test_updates_persisted(self, store):
        job = store.create_job("https://example.com/1")
        job.status = JobStatus.EXTRACTING
        store.update_job(job)
        fetched = store.get_job(job.job_id)
        assert fetched.status == JobStatus.EXTRACTING

    def test_version_increments(self, store):
        job = store.create_job("https://example.com/1")
        assert job.version == 1
        job = store.update_job(job)
        assert job.version == 2
        job = store.update_job(job)
        assert job.version == 3

    def test_update_nonexistent_raises(self, store):
        job = Job(source_url="https://example.com/1")
        job.job_id = "does-not-exist"
        with pytest.raises(JobNotFoundError):
            store.update_job(job)


class TestDeleteJob:
    def test_deletes_workspace(self, store):
        job = store.create_job("https://example.com/1")
        job_dir = store._job_dir(job.job_id)
        assert job_dir.exists()
        store.delete_job(job.job_id)
        assert not job_dir.exists()

    def test_delete_nonexistent_raises(self, store):
        with pytest.raises(JobNotFoundError):
            store.delete_job("does-not-exist")

    def test_deleted_job_not_in_list(self, store):
        j1 = store.create_job("https://example.com/1")
        j2 = store.create_job("https://example.com/2")
        store.delete_job(j1.job_id)
        jobs = store.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].job_id == j2.job_id


class TestGetQueuedJobs:
    def test_returns_only_queued(self, store):
        j1 = store.create_job("https://example.com/1")
        j2 = store.create_job("https://example.com/2")
        j1.status = JobStatus.QUEUED
        store.update_job(j1)
        queued = store.get_queued_jobs()
        assert len(queued) == 1
        assert queued[0].job_id == j1.job_id


class TestFindActiveUrl:
    def test_finds_active(self, store):
        job = store.create_job("https://example.com/1")
        found = store.find_active_url("https://example.com/1")
        assert found is not None
        assert found.job_id == job.job_id

    def test_ignores_terminal(self, store):
        job = store.create_job("https://example.com/1")
        job.status = JobStatus.COMPLETED
        store.update_job(job)
        found = store.find_active_url("https://example.com/1")
        assert found is None

    def test_returns_none_for_unknown(self, store):
        assert store.find_active_url("https://unknown.com") is None
