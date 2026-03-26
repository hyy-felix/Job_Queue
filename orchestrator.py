"""
Job_Queue orchestrator.
Owns all state transitions, extraction dispatch, generation queue loop, and crash recovery.

Writer ownership: ONLY this module writes to status.json via the JobStore.
Subprocesses write to their own artifact files only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config
from models import (
    ApplyData,
    ExtractionData,
    ErrorInfo,
    GenerationData,
    Job,
    JobStatus,
    WorkerInfo,
    is_legal_transition,
    is_workflow_terminal,
    IN_FLIGHT_STATES,
)
from persistence import JsonJobStore, JobNotFoundError

logger = logging.getLogger("job_queue")


class StateTransitionError(Exception):
    pass


def _build_no_proxy_env() -> dict:
    """Build subprocess env that bypasses proxy for CDP localhost connections.
    Appends to existing NO_PROXY rather than replacing it."""
    env = os.environ.copy()
    localhost_entries = "localhost,127.0.0.1,::1"
    for key in ("no_proxy", "NO_PROXY"):
        existing = env.get(key, "")
        if existing:
            # Append localhost entries if not already present
            entries = {e.strip() for e in existing.split(",")}
            for entry in localhost_entries.split(","):
                entries.add(entry)
            env[key] = ",".join(sorted(entries))
        else:
            env[key] = localhost_entries
    return env


class Orchestrator:
    def __init__(self, store: JsonJobStore):
        self.store = store
        self._extraction_semaphore = asyncio.Semaphore(3)  # Fixed at 3 per design doc
        self._generation_lock_path = config.GENERATION_LOCK_FILE
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}
        self._event_queue: asyncio.Queue | None = None
        self._queue_task: asyncio.Task | None = None
        self._running = False

    def set_event_queue(self, queue: asyncio.Queue) -> None:
        """Set the SSE event queue for broadcasting state changes."""
        self._event_queue = queue

    async def _emit_event(self, event_type: str, data: dict) -> None:
        if self._event_queue:
            await self._event_queue.put({"event": event_type, "data": data})

    # ── State transitions ─────────────────────────────────────

    def _transition(self, job: Job, new_status: JobStatus, **kwargs) -> Job:
        """Enforce legal state transition, update job, persist."""
        if not is_legal_transition(job.status, new_status):
            raise StateTransitionError(
                f"Illegal transition: {job.status.value} → {new_status.value} "
                f"for job {job.job_id}"
            )
        old_status = job.status
        job.status = new_status
        for key, value in kwargs.items():
            if hasattr(job, key):
                setattr(job, key, value)
        job = self.store.update_job(job)
        logger.info(
            "Job %s: %s → %s", job.job_id, old_status.value, new_status.value
        )
        return job

    # ── Crash recovery ────────────────────────────────────────

    async def recover_on_startup(self) -> None:
        """Reset in-flight jobs and clean up orphaned processes on startup."""
        logger.info("Running crash recovery...")

        # Clear stale generation lock
        if self._generation_lock_path.exists():
            self._generation_lock_path.unlink()
            logger.info("Cleared stale generation lock")

        for job in self.store.list_jobs():
            if job.status == JobStatus.EXTRACTING:
                self._reap_worker(job)
                job.error.last_error = "Server restarted during extraction"
                job.worker = WorkerInfo()
                self._transition(job, JobStatus.EXTRACTION_FAILED)

            elif job.status == JobStatus.GENERATING:
                self._reap_worker(job)
                job.error.last_error = "Server restarted during generation"
                job.worker = WorkerInfo()
                self._transition(job, JobStatus.GENERATION_FAILED)

            elif job.status == JobStatus.APPLYING:
                self._reap_worker(job)
                job.error.last_error = "Server restarted during apply"
                job.worker = WorkerInfo()
                self._transition(job, JobStatus.APPLY_FAILED)

            elif job.status == JobStatus.APPLY_QUEUED:
                # Task was scheduled but never started — reset to COMPLETED
                # so user can re-trigger manually
                job.error.last_error = "Server restarted before apply started"
                self._transition(job, JobStatus.CANCELLED)
                logger.info("Reset APPLY_QUEUED job %s to CANCELLED", job.job_id)

        logger.info("Crash recovery complete")

    def _reap_worker(self, job: Job) -> None:
        """Try to kill orphaned subprocess if PID/PGID is recorded."""
        if job.worker.pgid:
            try:
                os.killpg(job.worker.pgid, signal.SIGTERM)
                logger.info("Sent SIGTERM to orphaned process group %d", job.worker.pgid)
            except (ProcessLookupError, PermissionError):
                pass

    # ── Generation lock ───────────────────────────────────────

    def _acquire_generation_lock(self, job_id: str) -> bool:
        try:
            fd = os.open(
                str(self._generation_lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.write(fd, job_id.encode())
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def _release_generation_lock(self) -> None:
        try:
            self._generation_lock_path.unlink()
        except FileNotFoundError:
            pass

    # ── Extraction ────────────────────────────────────────────

    async def dispatch_extraction(self, job: Job) -> None:
        """Dispatch extraction as a background task with semaphore limiting."""
        asyncio.create_task(self._run_extraction(job))

    async def _run_extraction(self, job: Job) -> None:
        async with self._extraction_semaphore:
            job = self.store.get_job(job.job_id)
            # Accept SUBMITTED (new job) or EXTRACTING (retry — already transitioned)
            if job.status not in (JobStatus.SUBMITTED, JobStatus.EXTRACTING):
                return

            if job.status == JobStatus.SUBMITTED:
                job = self._transition(job, JobStatus.EXTRACTING)
            await self._emit_event("job_updated", _job_summary(job))

            job_dir = config.JOBS_DIR / job.job_id
            output_path = job_dir / "input" / "job.json"
            log_path = job_dir / "run" / "extraction.log"

            # Delete stale output from previous attempt (prevents consuming old data on retry)
            if output_path.exists():
                output_path.unlink()

            try:
                # Build extraction command
                if config.MOCK_EXTRACTION:
                    mock_script = config.PROJECT_DIR / "extractors" / "mock_extract.py"
                    cmd = ["python3", str(mock_script), job.source_url, str(output_path)]
                    logger.info("[MOCK] Using mock extraction for job %s", job.job_id)
                else:
                    cmd = [
                        "uv", "run", "--project", str(config.BROWSER_USE_REPO),
                        "python", str(config.EXTRACT_SCRIPT),
                        job.source_url, str(output_path),
                    ]
                    # Attach to existing Chrome via CDP if configured
                    if config.CDP_URL:
                        cmd.extend(["--cdp-url", config.CDP_URL])
                        logger.info("Using CDP browser at %s for job %s", config.CDP_URL, job.job_id)

                # Build env that bypasses proxy for CDP (localhost) connections
                sub_env = _build_no_proxy_env()

                with open(log_path, "w") as log_file:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=log_file,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                        env=sub_env,
                    )
                    job.worker = WorkerInfo(pid=proc.pid, pgid=proc.pid)
                    self.store.update_job(job)
                    self._active_processes[job.job_id] = proc

                    try:
                        await asyncio.wait_for(
                            proc.wait(),
                            timeout=config.EXTRACTION_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        os.killpg(proc.pid, signal.SIGTERM)
                        await asyncio.sleep(2)
                        if proc.returncode is None:
                            os.killpg(proc.pid, signal.SIGKILL)
                        raise

                self._active_processes.pop(job.job_id, None)
                job = self.store.get_job(job.job_id)

                if job.status == JobStatus.CANCELLED:
                    return

                if proc.returncode == 0 and output_path.exists():
                    extraction_data = self._parse_extraction_output(output_path)
                    if extraction_data.job_description:
                        job.extraction = extraction_data
                        job = self._transition(job, JobStatus.READY_FOR_REVIEW)
                    else:
                        job.error.last_error = "Extraction succeeded but no job_description found"
                        job = self._transition(job, JobStatus.EXTRACTION_FAILED)
                else:
                    job.error.last_error = f"Extraction failed with exit code {proc.returncode}"
                    job = self._transition(job, JobStatus.EXTRACTION_FAILED)

            except asyncio.TimeoutError:
                job = self.store.get_job(job.job_id)
                if job.status != JobStatus.CANCELLED:
                    job.error.last_error = f"Extraction timed out after {config.EXTRACTION_TIMEOUT_SECONDS}s"
                    job = self._transition(job, JobStatus.EXTRACTION_FAILED)
            except Exception as exc:
                job = self.store.get_job(job.job_id)
                if job.status != JobStatus.CANCELLED:
                    job.error.last_error = f"Extraction error: {exc}"
                    job = self._transition(job, JobStatus.EXTRACTION_FAILED)
                logger.exception("Extraction error for job %s", job.job_id)
            finally:
                # Re-read to avoid overwriting state changed by cancel_job
                job = self.store.get_job(job.job_id)
                job.worker = WorkerInfo()
                self.store.update_job(job)
                self._active_processes.pop(job.job_id, None)
                await self._emit_event("job_updated", _job_summary(job))

    def _parse_extraction_output(self, path: Path) -> ExtractionData:
        """Parse the extraction output JSON into ExtractionData."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return ExtractionData(
                role_title=raw.get("role_title"),
                company_name=raw.get("company_name"),
                salary=raw.get("salary"),
                location=raw.get("location"),
                job_description=raw.get("job_description"),
                scraped_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            logger.error("Failed to parse extraction output: %s", exc)
            return ExtractionData()

    # ── Generation ────────────────────────────────────────────

    async def start_generation_loop(self) -> None:
        """Background loop: pick queued jobs one at a time, run generation."""
        self._running = True
        logger.info("Generation queue loop started")
        while self._running:
            queued = self.store.get_queued_jobs()
            if not queued:
                await asyncio.sleep(2)
                continue

            job = queued[0]
            if not self._acquire_generation_lock(job.job_id):
                logger.warning("Generation lock already held, waiting...")
                await asyncio.sleep(5)
                continue

            try:
                await self._run_generation(job)
            finally:
                self._release_generation_lock()

    async def _run_generation(self, job: Job) -> None:
        job = self.store.get_job(job.job_id)
        if job.status != JobStatus.QUEUED:
            return

        job = self._transition(job, JobStatus.GENERATING)
        await self._emit_event("job_updated", _job_summary(job))

        job_dir = config.JOBS_DIR / job.job_id
        log_path = job_dir / "run" / "generation.log"

        # Write JD to temp file for Claude
        jd_tmp = config.jd_temp_path(job.job_id)
        jd_text = job.extraction.job_description or ""
        jd_tmp.write_text(jd_text, encoding="utf-8")

        company = job.extraction.company_name or "Unknown"
        role = job.extraction.role_title or "Unknown"

        # Snapshot applications/ tree before invocation
        apps_dir = config.WORK_DIR / "applications"
        pre_dirs = set()
        if apps_dir.exists():
            for date_dir in apps_dir.iterdir():
                if date_dir.is_dir():
                    for role_dir in date_dir.iterdir():
                        if role_dir.is_dir():
                            pre_dirs.add(str(role_dir))

        prompt = (
            f"Build a resume and cover letter for the following job. "
            f"Company: {company}, Role: {role}. "
            f"The full job description is in the file {jd_tmp} — read it and use it. "
            f"After completing all files, print the exact output folder path as the "
            f"very last line of your output, prefixed with OUTPUT_PATH: "
        )

        try:
            with open(log_path, "w") as log_file:
                proc = await asyncio.create_subprocess_exec(
                    config.CLAUDE_CLI, "--print",
                    "--dangerously-skip-permissions",
                    "-p", prompt,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(config.WORK_DIR),
                    start_new_session=True,
                )
                job.worker = WorkerInfo(pid=proc.pid, pgid=proc.pid)
                self.store.update_job(job)
                self._active_processes[job.job_id] = proc

                try:
                    await asyncio.wait_for(
                        proc.wait(),
                        timeout=config.GENERATION_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    os.killpg(proc.pid, signal.SIGTERM)
                    await asyncio.sleep(5)
                    if proc.returncode is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                    raise

            self._active_processes.pop(job.job_id, None)
            job = self.store.get_job(job.job_id)

            if job.status == JobStatus.CANCELLED:
                return

            if proc.returncode == 0:
                output_folder = self._resolve_output_folder(log_path, apps_dir, pre_dirs)
                if output_folder:
                    validation = self._validate_and_copy(job, output_folder)
                    if validation:
                        job.generation = GenerationData(
                            output_folder=str(output_folder),
                            completed_at=datetime.now(timezone.utc),
                        )
                        job = self._transition(job, JobStatus.COMPLETED)
                    else:
                        job.error.last_error = "Output validation failed"
                        job = self._transition(job, JobStatus.GENERATION_FAILED)
                else:
                    job.error.last_error = "Could not locate output folder"
                    job = self._transition(job, JobStatus.GENERATION_FAILED)
            else:
                job.error.last_error = f"Generation failed with exit code {proc.returncode}"
                job = self._transition(job, JobStatus.GENERATION_FAILED)

        except asyncio.TimeoutError:
            job = self.store.get_job(job.job_id)
            if job.status != JobStatus.CANCELLED:
                job.error.last_error = f"Generation timed out after {config.GENERATION_TIMEOUT_SECONDS}s"
                job = self._transition(job, JobStatus.GENERATION_FAILED)
        except Exception as exc:
            job = self.store.get_job(job.job_id)
            if job.status != JobStatus.CANCELLED:
                job.error.last_error = f"Generation error: {exc}"
                job = self._transition(job, JobStatus.GENERATION_FAILED)
            logger.exception("Generation error for job %s", job.job_id)
        finally:
            # Re-read to avoid overwriting state changed by cancel_job
            job = self.store.get_job(job.job_id)
            job.worker = WorkerInfo()
            self.store.update_job(job)
            self._active_processes.pop(job.job_id, None)
            # Clean up temp JD file
            jd_tmp.unlink(missing_ok=True)
            await self._emit_event("job_updated", _job_summary(job))

    def _resolve_output_folder(
        self, log_path: Path, apps_dir: Path, pre_dirs: set[str]
    ) -> Path | None:
        """
        Two-tier output resolution:
        1. Primary: parse OUTPUT_PATH: from generation log
        2. Fallback: diff the applications/ tree
        """
        # Primary: parse OUTPUT_PATH from log
        try:
            log_text = log_path.read_text(encoding="utf-8")
            for line in reversed(log_text.splitlines()):
                line = line.strip()
                if line.startswith("OUTPUT_PATH:"):
                    candidate = Path(line.split("OUTPUT_PATH:", 1)[1].strip())
                    if candidate.is_dir():
                        logger.info("Output folder from prompt: %s", candidate)
                        return candidate
        except Exception:
            pass

        # Fallback: tree diff
        if not apps_dir.exists():
            return None
        post_dirs = set()
        for date_dir in apps_dir.iterdir():
            if date_dir.is_dir():
                for role_dir in date_dir.iterdir():
                    if role_dir.is_dir():
                        post_dirs.add(str(role_dir))

        new_dirs = post_dirs - pre_dirs
        # Filter: must contain both PDFs + some compile log
        candidates = []
        for d in new_dirs:
            dp = Path(d)
            has_pdfs = (dp / "resume.pdf").exists() and (dp / "cover_letter.pdf").exists()
            has_compile = (
                (dp / "note" / "compile.log").exists()
                or (dp / "note" / "compile_resume.log").exists()
            )
            if has_pdfs and has_compile:
                candidates.append(dp)

        if len(candidates) == 1:
            logger.info("Output folder from tree diff: %s", candidates[0])
            return candidates[0]
        elif len(candidates) > 1:
            logger.error("Fail-closed: %d candidate output folders found", len(candidates))
        else:
            logger.error("Fail-closed: 0 candidate output folders found")
        return None

    def _validate_and_copy(self, job: Job, output_folder: Path) -> bool:
        """Validate outputs and copy artifacts to job workspace."""
        job_dir = config.JOBS_DIR / job.job_id

        resume = output_folder / "resume.pdf"
        cover_letter = output_folder / "cover_letter.pdf"
        compile_log = output_folder / "note" / "compile.log"

        # Validate
        if not resume.exists() or resume.stat().st_size == 0:
            logger.error("resume.pdf missing or empty")
            return False
        if not cover_letter.exists() or cover_letter.stat().st_size == 0:
            logger.error("cover_letter.pdf missing or empty")
            return False

        # Check compile logs — look for successful PDF writing indicators
        # The workflow may use compile.log, compile_resume.log, or compile_cover_letter.log
        compile_logs = [
            compile_log,
            output_folder / "note" / "compile_resume.log",
            output_folder / "note" / "compile_cover_letter.log",
        ]
        found_compile_evidence = False
        for cl in compile_logs:
            try:
                log_text = cl.read_text(encoding="utf-8")
                if "BUILD: SUCCESS" in log_text or "Writing `" in log_text:
                    found_compile_evidence = True
                    break
            except FileNotFoundError:
                continue
            except Exception:
                continue

        if not found_compile_evidence:
            logger.warning("No compile log evidence found, relying on PDF existence + size check")

        # Copy artifacts
        import shutil
        out_dir = job_dir / "output"
        shutil.copy2(str(resume), str(out_dir / "resume.pdf"))
        shutil.copy2(str(cover_letter), str(out_dir / "cover_letter.pdf"))

        # Copy additional artifacts if they exist
        selection_log = output_folder / "note" / "selection_log.json"
        notes = output_folder / "note" / "notes.md"
        if selection_log.exists():
            shutil.copy2(str(selection_log), str(out_dir / "selection_log.json"))
        if notes.exists():
            shutil.copy2(str(notes), str(out_dir / "notes.md"))

        # Write metadata
        metadata = {
            "job_id": job.job_id,
            "source_url": job.source_url,
            "company_name": job.extraction.company_name,
            "role_title": job.extraction.role_title,
            "source_output_folder": str(output_folder),
            "resume_pdf": str(out_dir / "resume.pdf"),
            "cover_letter_pdf": str(out_dir / "cover_letter.pdf"),
            "copied_at": datetime.now(timezone.utc).isoformat(),
        }
        (out_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )

        logger.info("Artifacts copied to workspace for job %s", job.job_id)
        return True

    # ── API-facing mutations (called by server.py) ─────────────
    # These ensure ALL state writes go through the orchestrator.

    async def submit_urls(self, urls: list[str]) -> list[Job]:
        """Create jobs for URLs and dispatch extraction. Returns created jobs."""
        created = []
        for url in urls:
            url = url.strip()
            if not url:
                continue
            existing = self.store.find_active_url(url)
            if existing:
                raise StateTransitionError(
                    f"URL already has an active job: {existing.job_id}"
                )
            job = self.store.create_job(url)
            await self.dispatch_extraction(job)
            created.append(job)
            await self._emit_event("job_created", _job_summary(job))
        return created

    def edit_job(self, job_id: str, updates: dict) -> Job:
        """Edit extraction fields for a job in an editable state."""
        from models import EDITABLE_STATES
        job = self.store.get_job(job_id)
        if job.status not in EDITABLE_STATES:
            raise StateTransitionError(
                f"Job is in {job.status.value} state, not editable"
            )
        for key in ("role_title", "company_name", "salary", "location", "job_description"):
            if key in updates and updates[key] is not None:
                setattr(job.extraction, key, updates[key])
        job = self.store.update_job(job)
        return job

    async def approve_job(self, job_id: str) -> Job:
        """Approve a job for generation (from ready_for_review or extraction_failed with JD)."""
        job = self.store.get_job(job_id)

        if job.status == JobStatus.EXTRACTION_FAILED:
            if not job.extraction.job_description:
                raise StateTransitionError("Cannot approve: job_description is required")
            job = self._transition(job, JobStatus.READY_FOR_REVIEW)

        if job.status != JobStatus.READY_FOR_REVIEW:
            raise StateTransitionError(
                f"Cannot approve job in {job.status.value} state"
            )

        # Save snapshot of extraction data at approval time
        job_dir = config.JOBS_DIR / job.job_id
        snapshot = job.extraction.model_dump(mode="json")
        (job_dir / "input" / "job.json").write_text(
            json.dumps(snapshot, indent=2), encoding="utf-8"
        )

        job = self._transition(job, JobStatus.QUEUED)
        await self._emit_event("job_updated", _job_summary(job))
        return job

    async def retry_job(self, job_id: str) -> Job:
        """Retry a failed extraction or generation."""
        job = self.store.get_job(job_id)

        if job.status == JobStatus.EXTRACTION_FAILED:
            job.error.retry_count += 1
            job.error.last_error = None
            job = self._transition(job, JobStatus.EXTRACTING)
            await self.dispatch_extraction(job)
            await self._emit_event("job_updated", _job_summary(job))
            return job

        elif job.status == JobStatus.GENERATION_FAILED:
            job.error.retry_count += 1
            job.error.last_error = None
            job = self._transition(job, JobStatus.QUEUED)
            await self._emit_event("job_updated", _job_summary(job))
            return job

        elif job.status == JobStatus.APPLY_FAILED:
            job.error.retry_count += 1
            job.error.last_error = None
            self.store.update_job(job)
            # Reuse queue_apply for consistent artifact validation
            return await self.queue_apply(job_id)

        else:
            raise StateTransitionError(
                f"Cannot retry job in {job.status.value} state"
            )

    def delete_job(self, job_id: str) -> None:
        """Delete a job that is not in-flight."""
        job = self.store.get_job(job_id)
        if job.status in IN_FLIGHT_STATES:
            raise StateTransitionError(
                f"Cannot delete job in {job.status.value} state"
            )
        self.store.delete_job(job_id)
        logger.info("Job %s deleted", job_id)

    # ── Cancellation ──────────────────────────────────────────

    async def cancel_job(self, job_id: str) -> Job:
        job = self.store.get_job(job_id)
        if is_workflow_terminal(job.status):
            raise StateTransitionError(f"Cannot cancel job in {job.status.value} state")

        # Kill subprocess if in-flight
        proc = self._active_processes.pop(job_id, None)
        if proc and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                await asyncio.sleep(5)
                if proc.returncode is None:
                    os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

        job.worker = WorkerInfo()
        job = self._transition(job, JobStatus.CANCELLED)
        await self._emit_event("job_updated", _job_summary(job))
        return job

    # ── Apply (manual trigger only) ──────────────────────

    async def queue_apply(self, job_id: str) -> Job:
        """Manually queue a completed job for application.

        Validates artifacts exist before transitioning. Does NOT auto-trigger —
        must be called explicitly via POST /api/jobs/{id}/apply.
        """
        job = self.store.get_job(job_id)
        if job.status not in {JobStatus.COMPLETED, JobStatus.APPLY_FAILED}:
            raise StateTransitionError(
                f"Cannot apply from {job.status.value} state"
            )

        # Validate required artifacts exist
        metadata_path = config.JOBS_DIR / job_id / "output" / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"No output metadata for job {job_id}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        resume_pdf = metadata.get("resume_pdf", "")
        if not resume_pdf or not Path(resume_pdf).exists():
            raise FileNotFoundError(f"Resume PDF not found for job {job_id}")

        job = self._transition(job, JobStatus.APPLY_QUEUED)
        await self._emit_event("job_updated", _job_summary(job))
        asyncio.create_task(self._dispatch_apply(job_id))
        return job

    async def _dispatch_apply(self, job_id: str) -> None:
        """Run the apply subprocess. Mirrors _run_extraction pattern.

        Writes stdout to apply.log file descriptor (not PIPE) to avoid
        memory bloat from verbose browser-use output.
        """
        job = self.store.get_job(job_id)
        if job.status != JobStatus.APPLY_QUEUED:
            return

        job = self._transition(job, JobStatus.APPLYING)
        await self._emit_event("job_updated", _job_summary(job))

        job_dir = config.JOBS_DIR / job_id
        output_dir = job_dir / "output"
        log_path = job_dir / "run" / "apply.log"

        # Ensure run/ dir exists
        (job_dir / "run").mkdir(parents=True, exist_ok=True)

        metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))

        cmd = [
            "uv", "run", "--project", str(config.BROWSER_USE_PROJECT_DIR),
            "python", str(config.APPLY_SCRIPT),
            job.source_url,
            metadata["resume_pdf"],
            metadata.get("cover_letter_pdf", ""),
            str(output_dir),
            str(config.PROFILE_PATH),
        ]
        # Attach to existing Chrome via CDP if configured
        if config.CDP_URL:
            cmd.extend(["--cdp-url", config.CDP_URL])

        logger.info(
            "Apply started for job %s: url=%s, cdp=%s",
            job_id, job.source_url, config.CDP_URL or "none",
        )

        try:
            # Bypass proxy for CDP localhost connections (same as extraction)
            apply_env = _build_no_proxy_env()

            with open(log_path, "w") as log_file:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                    env=apply_env,
                )
                job.worker = WorkerInfo(pid=proc.pid, pgid=proc.pid)
                self.store.update_job(job)
                self._active_processes[job_id] = proc

                try:
                    await asyncio.wait_for(
                        proc.wait(),
                        timeout=config.APPLY_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    os.killpg(proc.pid, signal.SIGTERM)
                    await asyncio.sleep(2)
                    if proc.returncode is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                    raise

            self._active_processes.pop(job_id, None)
            job = self.store.get_job(job_id)

            if job.status == JobStatus.CANCELLED:
                return

            if proc.returncode == 0:
                # Read result JSON, update ApplyData
                result_path = output_dir / "apply_result.json"
                apply_data = {}
                if result_path.exists():
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    apply_data = {
                        "screenshot_path": str(output_dir / result.get("screenshot_path", ""))
                            if result.get("screenshot_path") else None,
                        "result_path": str(result_path),
                        "form_filled": result.get("status") in ("form_filled", "partial_fill"),
                        "resume_uploaded": result.get("resume_uploaded", False),
                        "fields_filled": result.get("fields_filled", []),
                    }
                    # applied_at is set by orchestrator, not runner
                # Always persist apply evidence for debugging, even on failure
                if apply_data:
                    job.apply = ApplyData(**apply_data)

                # If resume upload failed, treat as APPLY_FAILED
                if not apply_data.get("resume_uploaded", False):
                    job.error.last_error = "Resume upload unconfirmed"
                    job = self._transition(job, JobStatus.APPLY_FAILED)
                    logger.warning("Apply partial for job %s: resume upload unconfirmed", job_id)
                else:
                    job.apply.applied_at = datetime.now(timezone.utc)
                    job = self._transition(job, JobStatus.APPLIED)
                    logger.info("Apply succeeded for job %s", job_id)
            else:
                job.error.last_error = f"Apply subprocess exited {proc.returncode}"
                job = self._transition(job, JobStatus.APPLY_FAILED)
                logger.error("Apply failed for job %s: exit code %d", job_id, proc.returncode)

        except asyncio.TimeoutError:
            job = self.store.get_job(job_id)
            if job.status != JobStatus.CANCELLED:
                job.error.last_error = f"Apply timed out after {config.APPLY_TIMEOUT_SECONDS}s"
                job = self._transition(job, JobStatus.APPLY_FAILED)
                logger.error("Apply timed out for job %s", job_id)
        except Exception as exc:
            job = self.store.get_job(job_id)
            if job.status != JobStatus.CANCELLED:
                job.error.last_error = f"Apply error: {exc}"
                job = self._transition(job, JobStatus.APPLY_FAILED)
            logger.exception("Apply error for job %s", job_id)
        finally:
            job = self.store.get_job(job_id)
            job.worker = WorkerInfo()
            self.store.update_job(job)
            self._active_processes.pop(job_id, None)
            await self._emit_event("job_updated", _job_summary(job))

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        await self.recover_on_startup()
        self._queue_task = asyncio.create_task(self.start_generation_loop())

    async def stop(self) -> None:
        self._running = False
        if self._queue_task:
            self._queue_task.cancel()
            try:
                await self._queue_task
            except asyncio.CancelledError:
                pass


def _job_summary(job: Job) -> dict:
    """Create a summary dict for SSE events."""
    return {
        "job_id": job.job_id,
        "source_url": job.source_url,
        "status": job.status.value,
        "role_title": job.extraction.role_title,
        "company_name": job.extraction.company_name,
        "location": job.extraction.location,
        "salary": job.extraction.salary,
        "error": job.error.last_error,
    }
