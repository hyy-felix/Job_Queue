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
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


def _get_descendant_pids(pid: int) -> list[int]:
    """Return all descendant PIDs of *pid* (children, grandchildren, etc.)."""
    descendants: list[int] = []
    try:
        out = subprocess.check_output(
            ["pgrep", "-P", str(pid)], text=True, timeout=5,
        )
        for line in out.strip().splitlines():
            child = int(line.strip())
            descendants.append(child)
            descendants.extend(_get_descendant_pids(child))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        pass
    return descendants


async def _kill_process_tree(proc: asyncio.subprocess.Process, label: str = "") -> None:
    """Kill a subprocess and all its descendants, ensuring no orphans remain.

    1. Send SIGTERM to the process group (covers well-behaved children).
    2. Collect any descendant PIDs that may have escaped the group.
    3. Wait up to 5 s for graceful shutdown.
    4. SIGKILL anything still alive (group + individual descendants).
    """
    tag = f"[{label}] " if label else ""
    logger = logging.getLogger("orchestrator")

    # Snapshot descendant PIDs before sending signals
    descendants = _get_descendant_pids(proc.pid)
    all_pids = [proc.pid] + descendants
    if descendants:
        logger.info("%sDescendant PIDs of %d: %s", tag, proc.pid, descendants)

    # 1. SIGTERM the process group
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        logger.info("%sSent SIGTERM to process group %d", tag, proc.pid)
    except (ProcessLookupError, PermissionError):
        pass

    # 2. SIGTERM individual descendants that may have left the group
    for dpid in descendants:
        try:
            os.kill(dpid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    # 3. Grace period
    await asyncio.sleep(5)

    # 4. SIGKILL anything still alive
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            logger.warning("%sSent SIGKILL to process group %d", tag, proc.pid)
        except (ProcessLookupError, PermissionError):
            pass

    # Also SIGKILL lingering descendants
    for dpid in descendants:
        try:
            os.kill(dpid, 0)  # probe
            os.kill(dpid, signal.SIGKILL)
            logger.warning("%sSent SIGKILL to orphaned descendant %d", tag, dpid)
        except (ProcessLookupError, PermissionError):
            pass

import config
from models import (
    ApplyData,
    ExtractionData,
    ErrorInfo,
    FieldReview,
    GenerationData,
    Job,
    JobStatus,
    ReviewData,
    WorkerInfo,
    is_legal_transition,
    is_workflow_terminal,
    IN_FLIGHT_STATES,
)
from persistence import JsonJobStore, JobNotFoundError

logger = logging.getLogger("job_queue")


class StateTransitionError(Exception):
    pass


@dataclass(frozen=True)
class GenerationPackageResult:
    """Validated Re-Generator package copy result."""

    valid: bool
    status: JobStatus | None = None
    selection_log_path: Path | None = None
    resume_path: Path | None = None
    cover_letter_path: Path | None = None
    error: str | None = None


def _is_linkedin_url(url: str) -> bool:
    """Detect LinkedIn job URLs for source metadata only."""
    try:
        parsed = urlparse(url)
        return "linkedin.com" in parsed.netloc.lower()
    except Exception:
        return False


def _normalized_apply_method(method: str | None) -> str:
    """Normalize extraction apply_method into the supported contract."""
    normalized = (method or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized == "easyapply":
        normalized = "easy_apply"
    if normalized in {"easy_apply", "apply", "unknown"}:
        return normalized
    return "unknown"


def _should_skip_generation(job: Job) -> bool:
    """Easy Apply is the only signal that skips resume generation."""
    return _normalized_apply_method(job.extraction.apply_method) == "easy_apply"


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

            # APPLY_QUEUED removed in schema v2 — any migrated jobs
            # in GENERATED state are safe (apply can be re-triggered).

        logger.info("Crash recovery complete")

    def _reap_worker(self, job: Job) -> None:
        """Try to kill orphaned subprocess and its descendants."""
        if job.worker.pgid:
            descendants = _get_descendant_pids(job.worker.pgid)
            all_pids = [job.worker.pgid] + descendants
            for pid in all_pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                    logger.info("Sent SIGTERM to orphaned PID %d (job %s)", pid, job.job_id)
                except (ProcessLookupError, PermissionError):
                    pass
            # Also SIGTERM the process group
            try:
                os.killpg(job.worker.pgid, signal.SIGTERM)
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
                        logger.warning(
                            "Extraction timed out for job %s after %ds — killing process tree",
                            job.job_id, config.EXTRACTION_TIMEOUT_SECONDS,
                        )
                        await _kill_process_tree(proc, label=f"extract:{job.job_id}")
                        raise

                self._active_processes.pop(job.job_id, None)
                job = self.store.get_job(job.job_id)

                if job.status == JobStatus.CANCELLED:
                    return

                if proc.returncode == 0 and output_path.exists():
                    extraction_data = self._parse_extraction_output(output_path)
                    if extraction_data.job_description:
                        job.extraction = extraction_data
                        job.is_linkedin = _is_linkedin_url(job.source_url)
                        job = self._transition(job, JobStatus.SCRAPED)
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
                apply_method=_normalized_apply_method(raw.get("apply_method")),
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
            try:
                queued = self.store.get_queued_jobs()
                if not queued:
                    await asyncio.sleep(2)
                    continue

                job = queued[0]

                # Easy Apply: skip generation entirely. Plain "APPLY" still generates.
                job = self.store.get_job(job.job_id)
                if _should_skip_generation(job):
                    job.generation = GenerationData(
                        completed_at=datetime.now(timezone.utc),
                    )
                    job = self._transition(job, JobStatus.GENERATED)
                    logger.info(
                        "Easy Apply detected — skipping generation for job %s", job.job_id
                    )
                    await self._emit_event("job_updated", _job_summary(job))
                    continue

                if not self._acquire_generation_lock(job.job_id):
                    logger.warning("Generation lock already held, waiting...")
                    await asyncio.sleep(5)
                    continue

                try:
                    await self._run_generation(job)
                finally:
                    self._release_generation_lock()
            except Exception as exc:
                logger.exception("Generation loop error (continuing): %s", exc)
                await asyncio.sleep(5)

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
        apps_dir = config.RESUME_GENERATOR_DIR / "applications"
        pre_dirs = set()
        if apps_dir.exists():
            for date_dir in apps_dir.iterdir():
                if date_dir.is_dir():
                    for role_dir in date_dir.iterdir():
                        if role_dir.is_dir():
                            pre_dirs.add(str(role_dir))

        prompt = (
            f"Company: {company}, Role: {role}. "
            f"The full job description is in the file {jd_tmp} — read it and use it. "
            f"After completing all files, print the exact output folder path as the "
            f"very last line of your output, prefixed with OUTPUT_PATH: "
            f"Do not perform the git save step."
        )

        # Build command — inject SKILL.md for deterministic workflow if available
        cmd = [
            config.CLAUDE_CLI, "--print",
            "--dangerously-skip-permissions",
        ]
        if config.APPLY_JD_SKILL_FILE.exists():
            cmd.extend(["--append-system-prompt-file", str(config.APPLY_JD_SKILL_FILE)])
        else:
            logger.warning(
                "apply-jd SKILL.md not found at %s — using free-form prompt",
                config.APPLY_JD_SKILL_FILE,
            )
        cmd.extend(["-p", prompt])

        try:
            with open(log_path, "w") as log_file:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(config.RESUME_GENERATOR_DIR),
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
                    logger.warning(
                        "Generation timed out for job %s after %ds — killing process tree",
                        job.job_id, config.GENERATION_TIMEOUT_SECONDS,
                    )
                    await _kill_process_tree(proc, label=f"gen:{job.job_id}")
                    raise

            self._active_processes.pop(job.job_id, None)
            job = self.store.get_job(job.job_id)

            if job.status == JobStatus.CANCELLED:
                return

            if proc.returncode == 0:
                output_folder = self._resolve_output_folder(log_path, apps_dir, pre_dirs)
                if output_folder:
                    package = self._validate_and_copy(job, output_folder)
                    if package.valid and package.status:
                        job.generation = GenerationData(
                            output_folder=str(output_folder),
                            completed_at=datetime.now(timezone.utc),
                            resume_path=str(package.resume_path) if package.resume_path else None,
                            cover_letter_path=str(package.cover_letter_path) if package.cover_letter_path else None,
                            selection_log_path=str(package.selection_log_path) if package.selection_log_path else None,
                        )
                        # Extract structured requirements from selection_log
                        if package.selection_log_path:
                            job.requirements = self._extract_requirements(package.selection_log_path)
                        job = self._transition(job, package.status)
                    else:
                        job.error.last_error = package.error or "Output validation failed"
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

    def _validate_and_copy(self, job: Job, output_folder: Path) -> GenerationPackageResult:
        """Validate outputs and copy artifacts to job workspace."""
        resume = output_folder / "resume.pdf"
        cover_letter = output_folder / "cover_letter.pdf"
        compile_log = output_folder / "note" / "compile.log"
        selection_log = output_folder / "note" / "selection_log.json"

        sl_data = self._load_selection_log(selection_log)
        if sl_data is None:
            return GenerationPackageResult(False, error="selection_log.json missing or unreadable")
        if "jd_requirements" not in sl_data:
            logger.error("selection_log.json missing 'jd_requirements' key")
            return GenerationPackageResult(False, error="selection_log.json missing jd_requirements")

        nba = sl_data.get("needs_bullet_approval")
        if nba is not None:
            if not isinstance(nba, dict):
                logger.error("needs_bullet_approval signal must be an object")
                return GenerationPackageResult(False, error="Malformed needs_bullet_approval signal")
            if self._is_needs_bullet_approval_signal(sl_data):
                error = self._validate_insufficiency_package(output_folder, sl_data)
                if error:
                    return GenerationPackageResult(False, error=error)
                return self._copy_insufficiency_artifacts(job, output_folder, sl_data)
            if nba.get("triggered") is True:
                logger.error("Unsupported needs_bullet_approval reason: %s", nba.get("reason"))
                return GenerationPackageResult(False, error="Unsupported needs_bullet_approval reason")

        # Validate
        if not resume.exists() or resume.stat().st_size == 0:
            logger.error("resume.pdf missing or empty")
            return GenerationPackageResult(False, error="resume.pdf missing or empty")
        if not cover_letter.exists() or cover_letter.stat().st_size == 0:
            logger.error("cover_letter.pdf missing or empty")
            return GenerationPackageResult(False, error="cover_letter.pdf missing or empty")

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

        return self._copy_final_artifacts(job, output_folder)

    @staticmethod
    def _load_selection_log(selection_log: Path) -> dict | None:
        if not selection_log.exists():
            logger.error("selection_log.json missing at %s", selection_log)
            return None
        try:
            data = json.loads(selection_log.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("selection_log.json unreadable: %s", exc)
            return None
        if not isinstance(data, dict):
            logger.error("selection_log.json must contain a JSON object")
            return None
        return data

    @staticmethod
    def _is_needs_bullet_approval_signal(selection_log: dict) -> bool:
        signal_data = selection_log.get("needs_bullet_approval")
        return (
            isinstance(signal_data, dict)
            and signal_data.get("triggered") is True
            and signal_data.get("reason") == "insufficient_approved_source_bullets"
        )

    @staticmethod
    def _validate_insufficiency_package(output_folder: Path, selection_log: dict) -> str | None:
        notes = output_folder / "note" / "notes.md"
        if not notes.exists():
            logger.error("notes.md missing for needs_bullet_approval package")
            return "notes.md missing for needs_bullet_approval package"

        stale_artifacts = [
            output_folder / "resume.pdf",
            output_folder / "cover_letter.pdf",
            output_folder / "resume.tex",
            output_folder / "cover_letter.tex",
            output_folder / "note" / "compile.log",
            output_folder / "note" / "compile_resume.log",
            output_folder / "note" / "compile_cover_letter.log",
        ]
        stale_found = [path for path in stale_artifacts if path.exists()]
        if stale_found:
            logger.error("Stale final artifacts in insufficiency package: %s", stale_found)
            return "Stale final artifacts in needs_bullet_approval package"

        signal_data = selection_log.get("needs_bullet_approval", {})
        if signal_data.get("candidates_artifact") is not None:
            logger.error("candidates_artifact must be null for Phase 0C")
            return "candidates_artifact must be null for Phase 0C"

        if selection_log.get("selected_projects") != []:
            logger.error("selected_projects must be [] for needs_bullet_approval package")
            return "selected_projects must be empty for needs_bullet_approval package"

        if selection_log.get("trim_priority") != []:
            logger.error("trim_priority must be [] for needs_bullet_approval package")
            return "trim_priority must be empty for needs_bullet_approval package"

        final_outputs = selection_log.get("final_outputs")
        required_false_fields = (
            "resume_tex",
            "cover_letter_tex",
            "resume_pdf",
            "cover_letter_pdf",
            "compile_log",
        )
        if not isinstance(final_outputs, dict):
            logger.error("final_outputs object missing for needs_bullet_approval package")
            return "final_outputs object missing for needs_bullet_approval package"
        for field in required_false_fields:
            if final_outputs.get(field) is not False:
                logger.error("final_outputs.%s must be false", field)
                return f"final_outputs.{field} must be false"

        return None

    @staticmethod
    def _clear_output_artifacts(out_dir: Path) -> None:
        for name in (
            "resume.pdf",
            "cover_letter.pdf",
            "selection_log.json",
            "notes.md",
            "job_description.txt",
            "metadata.json",
            "resume.tex",
            "cover_letter.tex",
            "compile.log",
            "compile_resume.log",
            "compile_cover_letter.log",
        ):
            (out_dir / name).unlink(missing_ok=True)

    def _copy_final_artifacts(self, job: Job, output_folder: Path) -> GenerationPackageResult:
        job_dir = config.JOBS_DIR / job.job_id
        out_dir = job_dir / "output"
        resume = output_folder / "resume.pdf"
        cover_letter = output_folder / "cover_letter.pdf"
        selection_log = output_folder / "note" / "selection_log.json"

        self._clear_output_artifacts(out_dir)
        shutil.copy2(str(resume), str(out_dir / "resume.pdf"))
        shutil.copy2(str(cover_letter), str(out_dir / "cover_letter.pdf"))
        shutil.copy2(str(selection_log), str(out_dir / "selection_log.json"))

        # Copy optional artifacts
        notes = output_folder / "note" / "notes.md"
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
        return GenerationPackageResult(
            True,
            status=JobStatus.GENERATED,
            selection_log_path=out_dir / "selection_log.json",
            resume_path=out_dir / "resume.pdf",
            cover_letter_path=out_dir / "cover_letter.pdf",
        )

    def _copy_insufficiency_artifacts(
        self, job: Job, output_folder: Path, selection_log_data: dict
    ) -> GenerationPackageResult:
        job_dir = config.JOBS_DIR / job.job_id
        out_dir = job_dir / "output"
        selection_log = output_folder / "note" / "selection_log.json"
        notes = output_folder / "note" / "notes.md"
        job_description = output_folder / "note" / "job_description.txt"

        self._clear_output_artifacts(out_dir)
        shutil.copy2(str(selection_log), str(out_dir / "selection_log.json"))
        shutil.copy2(str(notes), str(out_dir / "notes.md"))
        copied_job_description = None
        if job_description.exists():
            copied_job_description = out_dir / "job_description.txt"
            shutil.copy2(str(job_description), str(copied_job_description))

        signal_data = selection_log_data["needs_bullet_approval"]
        metadata = {
            "job_id": job.job_id,
            "source_url": job.source_url,
            "company_name": job.extraction.company_name,
            "role_title": job.extraction.role_title,
            "source_output_folder": str(output_folder),
            "package_status": JobStatus.NEEDS_BULLET_APPROVAL.value,
            "resume_pdf": None,
            "cover_letter_pdf": None,
            "selection_log": str(out_dir / "selection_log.json"),
            "notes": str(out_dir / "notes.md"),
            "job_description": str(copied_job_description) if copied_job_description else None,
            "needs_bullet_approval": {
                "triggered": True,
                "reason": signal_data.get("reason"),
                "candidates_artifact": signal_data.get("candidates_artifact"),
                "unmatched_jd_gaps": signal_data.get(
                    "unmatched_jd_gaps",
                    selection_log_data.get("unmatched_jd_gaps", []),
                ),
            },
            "final_outputs": selection_log_data.get("final_outputs"),
            "copied_at": datetime.now(timezone.utc).isoformat(),
        }
        (out_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )

        logger.info("Insufficiency artifacts copied to workspace for job %s", job.job_id)
        return GenerationPackageResult(
            True,
            status=JobStatus.NEEDS_BULLET_APPROVAL,
            selection_log_path=out_dir / "selection_log.json",
        )

    @staticmethod
    def _extract_requirements(selection_log_path: Path) -> list:
        """Extract structured requirements from selection_log.json.

        Reads the jd_requirements string[] and converts each entry to a
        RequirementItem with default metadata. When resume-builder is upgraded
        to produce richer structured output, this method will pass it through.
        """
        from models import RequirementItem

        try:
            sl_data = json.loads(selection_log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

        # Check for structured requirements first (future: resume-builder upgrade)
        structured = sl_data.get("structured_requirements")
        if structured and isinstance(structured, list):
            items = []
            for i, req in enumerate(structured, 1):
                if isinstance(req, dict) and req.get("text"):
                    items.append(RequirementItem(
                        id=req.get("id", f"req_{i:03d}"),
                        text=req["text"],
                        type=req.get("type", "other"),
                        priority=req.get("priority", "must"),
                        keywords=req.get("keywords", []),
                        weight=req.get("weight", 1.0),
                        evidence_hint=req.get("evidence_hint"),
                        years_required=req.get("years_required"),
                    ))
            if items:
                logger.info("Using %d structured requirements from selection_log", len(items))
                return items

        # Fallback: convert string[] to minimal structured format
        raw_reqs = sl_data.get("jd_requirements", [])
        if not isinstance(raw_reqs, list):
            return []

        items = []
        for i, text in enumerate(raw_reqs, 1):
            if isinstance(text, str) and text.strip():
                items.append(RequirementItem(
                    id=f"req_{i:03d}",
                    text=text.strip(),
                    type="other",
                    priority="must",
                    keywords=[],
                    weight=1.0,
                ))

        if items:
            logger.info("Converted %d string requirements to structured format", len(items))
        return items

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
        if "apply_method" in updates and updates["apply_method"] is not None:
            job.extraction.apply_method = _normalized_apply_method(updates["apply_method"])
        job = self.store.update_job(job)
        return job

    async def approve_job(self, job_id: str) -> Job:
        """Approve a job for generation (from scraped or extraction_failed with JD)."""
        job = self.store.get_job(job_id)

        if job.status == JobStatus.EXTRACTION_FAILED:
            if not job.extraction.job_description:
                raise StateTransitionError("Cannot approve: job_description is required")
            job = self._transition(job, JobStatus.SCRAPED)

        if job.status != JobStatus.SCRAPED:
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

    async def delete_job(self, job_id: str) -> None:
        """Delete a job from any status.

        For in-flight jobs (EXTRACTING, GENERATING, APPLYING), kills the
        subprocess first, then deletes. No state restriction.
        """
        job = self.store.get_job(job_id)

        # Kill subprocess if in-flight
        if job.status in IN_FLIGHT_STATES:
            proc = self._active_processes.pop(job_id, None)
            if proc and proc.returncode is None:
                try:
                    _kill_process_tree(proc.pid)
                except Exception:
                    pass

        self.store.delete_job(job_id)
        await self._emit_event("job_deleted", {"job_id": job_id})
        logger.info("Job %s deleted (was %s)", job_id, job.status.value)

    # ── Score reception (pushed from Resume_Go) ────────────────

    async def receive_score(self, job_id: str, score_data: dict) -> Job:
        """Store a score pushed from Resume_Go and transition to SCORED.

        Idempotent: if already SCORED, returns existing job without error
        (handles network retries and duplicate SSE-driven pushes).
        """
        job = self.store.get_job(job_id)
        if job.status == JobStatus.SCORED:
            logger.info("Job %s already scored (%.1f%%), ignoring duplicate push",
                        job_id, job.score.overall_score or 0)
            return job
        if job.status != JobStatus.GENERATED:
            raise StateTransitionError(
                f"Cannot receive score in {job.status.value} state (expected generated)"
            )

        overall = score_data.get("overall_score")
        if overall is None or not isinstance(overall, (int, float)):
            raise StateTransitionError("overall_score is required and must be a number")
        if not (0 <= overall <= 100):
            raise StateTransitionError("overall_score must be between 0 and 100")

        from models import ScoreData
        job.score = ScoreData(
            overall_score=overall,
            keyword_score=score_data.get("keyword_score"),
            semantic_score=score_data.get("semantic_score"),
            algorithm=score_data.get("algorithm", "jd-match-resume"),
            requirement_scores=score_data.get("requirement_scores", []),
            computed_at=datetime.now(timezone.utc),
        )
        job = self._transition(job, JobStatus.SCORED)
        await self._emit_event("job_updated", _job_summary(job))
        logger.info("Job %s scored: %.1f%%", job_id, overall)
        return job

    # ── Cancellation ──────────────────────────────────────────

    async def cancel_job(self, job_id: str) -> Job:
        job = self.store.get_job(job_id)
        if is_workflow_terminal(job.status):
            raise StateTransitionError(f"Cannot cancel job in {job.status.value} state")

        # Kill subprocess if in-flight
        proc = self._active_processes.pop(job_id, None)
        if proc and proc.returncode is None:
            await _kill_process_tree(proc, label=f"cancel:{job_id}")

        job.worker = WorkerInfo()
        job = self._transition(job, JobStatus.CANCELLED)
        await self._emit_event("job_updated", _job_summary(job))
        return job

    # ── Apply (manual trigger only) ──────────────────────

    async def queue_apply(self, job_id: str) -> Job:
        """Trigger application for a generated/scored job.

        Validates artifacts exist before transitioning. Does NOT auto-trigger —
        must be called explicitly via POST /api/jobs/{id}/apply.
        Easy Apply routes to MANUAL_APPLY; others dispatch apply subprocess.
        """
        job = self.store.get_job(job_id)
        if job.status not in {JobStatus.GENERATED, JobStatus.SCORED, JobStatus.APPLY_FAILED}:
            raise StateTransitionError(
                f"Cannot apply from {job.status.value} state"
            )

        # Easy Apply intentionally skips generation, so no output artifacts are
        # expected before handing off to the user's browser.
        if _should_skip_generation(job):
            job = self._transition(job, JobStatus.MANUAL_APPLY)
            logger.info(
                "Easy Apply detected for job %s — handing off to daily browser.",
                job_id,
            )
            try:
                subprocess.run(["open", job.source_url], check=False)
            except Exception as exc:
                logger.warning("Failed to open URL in browser: %s", exc)
            await self._emit_event("job_updated", _job_summary(job))
            return job

        # Validate required artifacts exist before apply automation.
        metadata_path = config.JOBS_DIR / job_id / "output" / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"No output metadata for job {job_id}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        resume_pdf = metadata.get("resume_pdf", "")
        if not resume_pdf or not Path(resume_pdf).exists():
            raise FileNotFoundError(f"Resume PDF not found for job {job_id}")

        # Dispatch apply subprocess
        job = self._transition(job, JobStatus.APPLYING)
        await self._emit_event("job_updated", _job_summary(job))
        asyncio.create_task(self._dispatch_apply(job_id))
        return job

    async def _dispatch_apply(self, job_id: str) -> None:
        """Run the apply subprocess. Mirrors _run_extraction pattern.

        Only called after artifact validation (Easy Apply is handled by queue_apply).
        Runs browser-use, then optionally the review agent for confidence scoring
        (requires CDP + API key).
        """
        job = self.store.get_job(job_id)
        if job.status != JobStatus.APPLYING:
            return

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
                    logger.warning(
                        "Apply timed out for job %s after %ds — killing process tree",
                        job_id, config.APPLY_TIMEOUT_SECONDS,
                    )
                    await _kill_process_tree(proc, label=f"apply:{job_id}")
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
                    # ── Review agent (confidence-gated) ────────────
                    # Guards: CDP required (browser must persist for user to submit)
                    #         ANTHROPIC_API_KEY required for LLM review
                    review_ran = False
                    if config.CDP_URL and os.environ.get("ANTHROPIC_API_KEY"):
                        review_ran = await self._run_review_agent(job, output_dir)

                    if review_ran:
                        # Review succeeded — wait for user approval
                        job = self._transition(job, JobStatus.REVIEW_REQUIRED)
                        logger.info("Review required for job %s (confidence=%.2f)",
                                    job_id, job.review.overall_confidence)
                    else:
                        # No review — go straight to APPLIED (same as before)
                        job.apply.applied_at = datetime.now(timezone.utc)
                        job = self._transition(job, JobStatus.APPLIED)
                        logger.info("Apply succeeded for job %s (review skipped)", job_id)
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

    async def _run_review_agent(self, job: Job, output_dir: Path) -> bool:
        """Run the confidence-gated review agent. Returns True if review succeeded."""
        from appliers.review_agent import run_review

        result_path = output_dir / "apply_result.json"
        screenshot_path = output_dir / "apply_evidence.png"

        try:
            review_dict = await asyncio.wait_for(
                run_review(result_path, screenshot_path, config.PROFILE_PATH),
                timeout=config.REVIEW_TIMEOUT_SECONDS,
            )

            # Convert dict to ReviewData model
            fields = []
            for f in review_dict.get("fields", []):
                fields.append(FieldReview(
                    field_name=f.get("field_name", ""),
                    filled_value=f.get("filled_value", ""),
                    source=f.get("source", "unknown"),
                    confidence=float(f.get("confidence", 0.0)),
                    issue=f.get("issue"),
                ))
            job.review = ReviewData(
                overall_confidence=float(review_dict.get("overall_confidence", 0.0)),
                fields=fields,
                flags=review_dict.get("flags", []),
                recommendation=review_dict.get("recommendation", "review_required"),
                screenshot_path=review_dict.get("screenshot_path"),
                reviewed_at=datetime.now(timezone.utc),
            )
            self.store.update_job(job)
            return True

        except asyncio.TimeoutError:
            logger.warning("Review agent timed out for job %s", job.job_id)
            return False
        except Exception as exc:
            logger.warning("Review agent failed for job %s: %s", job.job_id, exc)
            return False

    # ── Review approval / manual apply confirmation ─────────

    async def approve_apply(self, job_id: str, action: str, reason: str = "") -> Job:
        """Approve or reject a job in REVIEW_REQUIRED state.

        action: "approve" → APPLIED, "reject" → APPLY_FAILED
        """
        job = self.store.get_job(job_id)
        if job.status != JobStatus.REVIEW_REQUIRED:
            raise StateTransitionError(
                f"Cannot approve-apply from {job.status.value} state"
            )

        if action == "approve":
            job.apply.applied_at = datetime.now(timezone.utc)
            job = self._transition(job, JobStatus.APPLIED)
            logger.info("Job %s approved by user", job_id)
        elif action == "reject":
            job.error.last_error = reason or "Rejected by user after review"
            job = self._transition(job, JobStatus.APPLY_FAILED)
            logger.info("Job %s rejected by user: %s", job_id, reason)
        else:
            raise StateTransitionError(f"Invalid action: {action}")

        await self._emit_event("job_updated", _job_summary(job))
        return job

    async def mark_applied(self, job_id: str) -> Job:
        """Mark a MANUAL_APPLY job as APPLIED after user confirmation."""
        job = self.store.get_job(job_id)
        if job.status != JobStatus.MANUAL_APPLY:
            raise StateTransitionError(
                f"Cannot mark-applied from {job.status.value} state"
            )
        job.apply.applied_at = datetime.now(timezone.utc)
        job = self._transition(job, JobStatus.APPLIED)
        logger.info("Job %s marked as applied (manual)", job_id)
        await self._emit_event("job_updated", _job_summary(job))
        return job

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
    """Create a summary dict for SSE events.

    Enriched in Wave 4: includes generation paths, score, and JD text
    so the frontend can update without re-fetching the full job.
    """
    summary: dict = {
        "job_id": job.job_id,
        "source_url": job.source_url,
        "status": job.status.value,
        "role_title": job.extraction.role_title,
        "company_name": job.extraction.company_name,
        "location": job.extraction.location,
        "salary": job.extraction.salary,
        "apply_method": job.extraction.apply_method,
        "error": job.error.last_error,
    }

    # Include JD text once available (SCRAPED+)
    if job.extraction.job_description:
        summary["jd_text"] = job.extraction.job_description

    # Include generation data when available (GENERATED+)
    if job.generation.completed_at:
        summary["generation"] = {
            "output_folder": job.generation.output_folder,
            "resume_path": job.generation.resume_path,
            "cover_letter_path": job.generation.cover_letter_path,
            "selection_log_path": job.generation.selection_log_path,
            "completed_at": job.generation.completed_at.isoformat() if job.generation.completed_at else None,
        }

    # Include requirements count (GENERATED+)
    if job.requirements:
        summary["requirements_count"] = len(job.requirements)

    # Include score when available (SCORED+)
    if job.score.overall_score is not None:
        summary["score"] = {
            "overall_score": job.score.overall_score,
            "keyword_score": job.score.keyword_score,
            "semantic_score": job.score.semantic_score,
            "algorithm": job.score.algorithm,
        }

    return summary
