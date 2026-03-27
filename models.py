"""
Job_Queue data models.
Single source of truth for job schema, shared by all layers.

State machine (ASCII diagram):
  SUBMITTED → EXTRACTING → READY_FOR_REVIEW → QUEUED → GENERATING → COMPLETED
                  ↓                                        ↓            ↓
           EXTRACTION_FAILED                        GENERATION_FAILED   ↓
                  ↑ (retry)                              ↑ (retry)      ↓
                  └────────────────────────────────────────           APPLY_QUEUED → APPLYING → APPLIED
                                                                         ↑              ↓
                                                                         └── APPLY_FAILED
  Any non-workflow-terminal state → CANCELLED
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


SCHEMA_VERSION = 1


class JobStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    EXTRACTING = "extracting"
    EXTRACTION_FAILED = "extraction_failed"
    READY_FOR_REVIEW = "ready_for_review"
    QUEUED = "queued"
    GENERATING = "generating"
    GENERATION_FAILED = "generation_failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    # ── Apply phase (manual trigger only) ──
    APPLY_QUEUED = "apply_queued"
    APPLYING = "applying"
    APPLIED = "applied"
    APPLY_FAILED = "apply_failed"


# ── Terminal / in-flight classification ────────────────────
# Two predicates replace the old TERMINAL_STATES set to avoid semantic confusion.
# COMPLETED is "generation-terminal" but allows apply transitions.
# See eng review decision: predicate functions > raw sets.

WORKFLOW_TERMINAL = {JobStatus.APPLIED, JobStatus.CANCELLED}
"""States where no further state changes are possible."""

URL_CLOSED = {JobStatus.COMPLETED, JobStatus.APPLIED, JobStatus.CANCELLED}
"""States where the URL slot is released — same URL can be re-submitted."""

# Keep TERMINAL_STATES as alias for backward compat with persistence.py import
# but point it at WORKFLOW_TERMINAL for correct semantics.
TERMINAL_STATES = WORKFLOW_TERMINAL

IN_FLIGHT_STATES = {JobStatus.EXTRACTING, JobStatus.GENERATING, JobStatus.APPLYING}

EDITABLE_STATES = {JobStatus.READY_FOR_REVIEW, JobStatus.EXTRACTION_FAILED}


def is_workflow_terminal(status: JobStatus) -> bool:
    """No more state changes possible."""
    return status in WORKFLOW_TERMINAL


def is_url_closed(status: JobStatus) -> bool:
    """URL slot is released — same URL can be re-submitted."""
    return status in URL_CLOSED


# ── Legal state transitions ───────────────────────────────────
# Key = from_state, Value = set of allowed to_states
LEGAL_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.SUBMITTED: {JobStatus.EXTRACTING, JobStatus.CANCELLED},
    JobStatus.EXTRACTING: {
        JobStatus.READY_FOR_REVIEW,
        JobStatus.EXTRACTION_FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.EXTRACTION_FAILED: {
        JobStatus.EXTRACTING,        # retry
        JobStatus.READY_FOR_REVIEW,  # manual fill + approve
        JobStatus.CANCELLED,
    },
    JobStatus.READY_FOR_REVIEW: {
        JobStatus.QUEUED,            # approve
        JobStatus.CANCELLED,
    },
    JobStatus.QUEUED: {
        JobStatus.GENERATING,
        JobStatus.CANCELLED,
    },
    JobStatus.GENERATING: {
        JobStatus.COMPLETED,
        JobStatus.GENERATION_FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.GENERATION_FAILED: {
        JobStatus.GENERATING,        # retry (direct)
        JobStatus.QUEUED,            # retry (via queue)
        JobStatus.CANCELLED,
    },
    # COMPLETED: generation-terminal, but allows manual apply trigger
    JobStatus.COMPLETED: {JobStatus.APPLY_QUEUED},
    JobStatus.CANCELLED: set(),      # workflow-terminal
    # ── Apply phase ──
    JobStatus.APPLY_QUEUED: {JobStatus.APPLYING, JobStatus.CANCELLED},
    JobStatus.APPLYING: {
        JobStatus.APPLIED,
        JobStatus.APPLY_FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.APPLY_FAILED: {
        JobStatus.APPLY_QUEUED,      # retry
        JobStatus.CANCELLED,
    },
    JobStatus.APPLIED: set(),        # workflow-terminal
}


def is_legal_transition(from_state: JobStatus, to_state: JobStatus) -> bool:
    return to_state in LEGAL_TRANSITIONS.get(from_state, set())


class ExtractionData(BaseModel):
    role_title: Optional[str] = None
    company_name: Optional[str] = None
    salary: Optional[str] = None
    location: Optional[str] = None
    job_description: Optional[str] = None
    scraped_at: Optional[datetime] = None


class ExtractionEditRequest(BaseModel):
    """Editable extraction fields — used as the PUT /api/jobs/{id} request body."""
    role_title: Optional[str] = None
    company_name: Optional[str] = None
    salary: Optional[str] = None
    location: Optional[str] = None
    job_description: Optional[str] = None


class GenerationData(BaseModel):
    output_folder: Optional[str] = None
    completed_at: Optional[datetime] = None


class ApplyData(BaseModel):
    """Tracks the apply phase. Populated by dispatch_apply() from apply_result.json."""
    screenshot_path: Optional[str] = None       # path to apply_evidence.png
    result_path: Optional[str] = None           # path to apply_result.json
    form_filled: bool = False                   # True if runner reported form_filled or partial_fill
    resume_uploaded: bool = False               # True if resume upload was confirmed
    applied_at: Optional[datetime] = None       # set by orchestrator at APPLIED transition, not runner
    fields_filled: list[str] = Field(default_factory=list)
    notification_sent_at: Optional[datetime] = None  # set after email notification sent


class ErrorInfo(BaseModel):
    last_error: Optional[str] = None
    retry_count: int = 0


class WorkerInfo(BaseModel):
    pid: Optional[int] = None
    pgid: Optional[int] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    schema_version: int = SCHEMA_VERSION
    job_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    source_url: str
    status: JobStatus = JobStatus.SUBMITTED
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    version: int = 1

    extraction: ExtractionData = Field(default_factory=ExtractionData)
    generation: GenerationData = Field(default_factory=GenerationData)
    apply: ApplyData = Field(default_factory=ApplyData)
    error: ErrorInfo = Field(default_factory=ErrorInfo)
    worker: WorkerInfo = Field(default_factory=WorkerInfo)
