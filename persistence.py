"""
Job_Queue persistence layer.
JsonJobStore reads/writes per-job status.json files with atomic writes and per-job locks.

Writer ownership rule:
  ONLY the orchestrator writes to status.json via this store.
  Subprocesses only write to their own output files (job.json, logs, PDFs).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from models import Job, JobStatus, is_url_closed, STATUS_MIGRATION, SCHEMA_VERSION
import config

logger = logging.getLogger("job_queue.persistence")


class JobNotFoundError(Exception):
    pass


class JobStore(ABC):
    @abstractmethod
    def create_job(self, url: str) -> Job:
        ...

    @abstractmethod
    def get_job(self, job_id: str) -> Job:
        ...

    @abstractmethod
    def list_jobs(self) -> list[Job]:
        ...

    @abstractmethod
    def update_job(self, job: Job) -> Job:
        ...

    @abstractmethod
    def delete_job(self, job_id: str) -> None:
        ...

    @abstractmethod
    def get_queued_jobs(self) -> list[Job]:
        ...

    @abstractmethod
    def find_active_url(self, url: str) -> Job | None:
        ...


class JsonJobStore(JobStore):
    """
    Reads/writes per-job status.json files in the workspace directory.
    Atomic writes via temp-file-and-rename.
    Per-job locks prevent concurrent writes from extraction threads and API calls.
    """

    def __init__(self, jobs_dir: Path | None = None):
        self._jobs_dir = jobs_dir or config.JOBS_DIR
        self._jobs_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def _job_dir(self, job_id: str) -> Path:
        return self._jobs_dir / job_id

    def _status_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "state" / "status.json"

    def _get_lock(self, job_id: str) -> threading.Lock:
        with self._global_lock:
            if job_id not in self._locks:
                self._locks[job_id] = threading.Lock()
            return self._locks[job_id]

    def _write_atomic(self, path: Path, data: str) -> None:
        """Write data to path via temp file + rename for crash safety."""
        tmp_path = path.parent / f".{path.name}.tmp"
        tmp_path.write_text(data, encoding="utf-8")
        os.rename(str(tmp_path), str(path))

    def create_job(self, url: str) -> Job:
        job = Job(source_url=url)
        job_dir = self._job_dir(job.job_id)

        # Create workspace directories
        (job_dir / "input").mkdir(parents=True, exist_ok=True)
        (job_dir / "run").mkdir(parents=True, exist_ok=True)
        (job_dir / "state").mkdir(parents=True, exist_ok=True)
        (job_dir / "output").mkdir(parents=True, exist_ok=True)

        # Write source URL convenience file
        (job_dir / "input" / "source_url.txt").write_text(url, encoding="utf-8")

        # Write initial status.json
        self._write_atomic(
            self._status_path(job.job_id),
            job.model_dump_json(indent=2),
        )
        return job

    def get_job(self, job_id: str) -> Job:
        path = self._status_path(job_id)
        if not path.exists():
            raise JobNotFoundError(f"Job {job_id} not found")
        data = path.read_text(encoding="utf-8")
        job = self._migrate_and_parse(data)
        return job

    @staticmethod
    def _migrate_and_parse(raw_json: str) -> Job:
        """Parse job JSON, migrating old status values if needed."""
        import json as _json
        raw = _json.loads(raw_json)
        status_val = raw.get("status", "")
        migrated = False

        # Migrate old status values to new enum names
        if status_val in STATUS_MIGRATION:
            raw["status"] = STATUS_MIGRATION[status_val]
            migrated = True

        # Bump schema version
        if raw.get("schema_version", 1) < SCHEMA_VERSION:
            raw["schema_version"] = SCHEMA_VERSION
            migrated = True

        if migrated:
            return Job.model_validate(raw)
        else:
            return Job.model_validate_json(raw_json)

    def list_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        if not self._jobs_dir.exists():
            return jobs
        for entry in self._jobs_dir.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            status_path = entry / "state" / "status.json"
            if status_path.exists():
                try:
                    data = status_path.read_text(encoding="utf-8")
                    jobs.append(self._migrate_and_parse(data))
                except Exception as exc:
                    logger.warning("Skipping corrupt casefile %s: %s", entry.name, exc)
                    continue
        jobs.sort(key=lambda j: j.created_at)
        return jobs

    def update_job(self, job: Job) -> Job:
        lock = self._get_lock(job.job_id)
        with lock:
            path = self._status_path(job.job_id)
            if not path.exists():
                raise JobNotFoundError(f"Job {job.job_id} not found")
            job.updated_at = datetime.now(timezone.utc)
            job.version += 1
            self._write_atomic(path, job.model_dump_json(indent=2))
        return job

    def delete_job(self, job_id: str) -> None:
        lock = self._get_lock(job_id)
        with lock:
            job_dir = self._job_dir(job_id)
            if not job_dir.exists():
                raise JobNotFoundError(f"Job {job_id} not found")
            shutil.rmtree(str(job_dir))
        with self._global_lock:
            self._locks.pop(job_id, None)

    def get_queued_jobs(self) -> list[Job]:
        return [j for j in self.list_jobs() if j.status == JobStatus.QUEUED]

    def find_active_url(self, url: str) -> Job | None:
        """Find a job with this URL that is still active (not url-closed).

        Uses is_url_closed() predicate: GENERATED, SCORED, APPLIED, CANCELLED,
        and MANUAL_APPLY are all considered "closed" — the URL slot is released.
        """
        for job in self.list_jobs():
            if job.source_url == url and not is_url_closed(job.status):
                return job
        return None
