"""
Job_Queue data models.
Single source of truth for job schema, shared by all layers.

State machine (ASCII diagram):
  SUBMITTED → EXTRACTING → SCRAPED → QUEUED → GENERATING → GENERATED → SCORED
                  ↓                                ↓            ↓          ↓
           EXTRACTION_FAILED               GENERATION_FAILED   ↓        APPLYING → REVIEW_REQUIRED → APPLIED
                  ↑ (retry)                     ↑ (retry)      ↓          ↓                            ↓
                                                             APPLYING   APPLY_FAILED                APPLY_FAILED
                                                               ↓          ↑ retry
                                                           (see above)

  GENERATING → NEEDS_BULLET_APPROVAL → QUEUED   (retry after source-bullet approval)
  GENERATED/SCORED → MANUAL_APPLY → APPLIED   (Easy Apply handoff)
  Any non-terminal → CANCELLED
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


SCHEMA_VERSION = 2


class JobStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    EXTRACTING = "extracting"
    EXTRACTION_FAILED = "extraction_failed"
    SCRAPED = "scraped"
    QUEUED = "queued"
    GENERATING = "generating"
    GENERATION_FAILED = "generation_failed"
    NEEDS_BULLET_APPROVAL = "needs_bullet_approval"
    GENERATED = "generated"
    SCORED = "scored"
    CANCELLED = "cancelled"
    # ── Apply phase (manual trigger only) ──
    APPLYING = "applying"
    APPLIED = "applied"
    APPLY_FAILED = "apply_failed"
    # ── Confidence-gated apply ──
    MANUAL_APPLY = "manual_apply"       # Easy Apply handoff — user applies manually
    REVIEW_REQUIRED = "review_required" # Post-fill review — waiting for user approval


# ── Migration: map old persisted status values to new enum values ──
STATUS_MIGRATION: dict[str, str] = {
    "ready_for_review": "scraped",
    "completed": "generated",
    "apply_queued": "generated",  # APPLY_QUEUED removed — reset to GENERATED
}


# ── Terminal / in-flight classification ────────────────────

WORKFLOW_TERMINAL = {JobStatus.APPLIED, JobStatus.CANCELLED}
"""States where no further state changes are possible."""

URL_CLOSED = {
    JobStatus.GENERATED, JobStatus.SCORED,
    JobStatus.APPLIED, JobStatus.CANCELLED, JobStatus.MANUAL_APPLY,
}
"""States where the URL slot is released — same URL can be re-submitted."""

# Keep TERMINAL_STATES as alias for backward compat
TERMINAL_STATES = WORKFLOW_TERMINAL

IN_FLIGHT_STATES = {JobStatus.EXTRACTING, JobStatus.GENERATING, JobStatus.APPLYING}

EDITABLE_STATES = {JobStatus.SCRAPED, JobStatus.EXTRACTION_FAILED}


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
        JobStatus.SCRAPED,
        JobStatus.EXTRACTION_FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.EXTRACTION_FAILED: {
        JobStatus.EXTRACTING,   # retry
        JobStatus.SCRAPED,      # manual fill + approve
        JobStatus.CANCELLED,
    },
    JobStatus.SCRAPED: {
        JobStatus.QUEUED,       # approve
        JobStatus.CANCELLED,
    },
    JobStatus.QUEUED: {
        JobStatus.GENERATING,
        JobStatus.GENERATED,   # Easy Apply skip — no actual generation
        JobStatus.CANCELLED,
    },
    JobStatus.GENERATING: {
        JobStatus.GENERATED,
        JobStatus.GENERATION_FAILED,
        JobStatus.NEEDS_BULLET_APPROVAL,
        JobStatus.CANCELLED,
    },
    JobStatus.NEEDS_BULLET_APPROVAL: {
        JobStatus.QUEUED,       # retry after approved source bullets are available
        JobStatus.CANCELLED,
    },
    JobStatus.GENERATION_FAILED: {
        JobStatus.GENERATING,   # retry (direct)
        JobStatus.QUEUED,       # retry (via queue)
        JobStatus.CANCELLED,
    },
    # GENERATED: artifacts exist. Can score, apply directly, or manual handoff.
    JobStatus.GENERATED: {
        JobStatus.SCORED,
        JobStatus.APPLYING,
        JobStatus.MANUAL_APPLY,
        JobStatus.CANCELLED,
    },
    # SCORED: score computed. Can apply or manual handoff.
    JobStatus.SCORED: {
        JobStatus.APPLYING,
        JobStatus.MANUAL_APPLY,
        JobStatus.CANCELLED,
    },
    JobStatus.APPLYING: {
        JobStatus.REVIEW_REQUIRED,
        JobStatus.APPLIED,
        JobStatus.APPLY_FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.REVIEW_REQUIRED: {
        JobStatus.APPLIED,
        JobStatus.APPLY_FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.MANUAL_APPLY: {JobStatus.APPLIED, JobStatus.CANCELLED},
    JobStatus.APPLY_FAILED: {
        JobStatus.APPLYING,     # retry
        JobStatus.CANCELLED,
    },
    JobStatus.APPLIED: set(),        # workflow-terminal
    JobStatus.CANCELLED: set(),      # workflow-terminal
}


def is_legal_transition(from_state: JobStatus, to_state: JobStatus) -> bool:
    return to_state in LEGAL_TRANSITIONS.get(from_state, set())


class ExtractionData(BaseModel):
    role_title: Optional[str] = None
    company_name: Optional[str] = None
    salary: Optional[str] = None
    location: Optional[str] = None
    job_description: Optional[str] = None
    apply_method: Optional[str] = None  # "easy_apply", "apply", or "unknown"
    scraped_at: Optional[datetime] = None


class RequirementItem(BaseModel):
    """Structured JD requirement — canonical schema (extract-jd v1).

    Minimal fields are always present; richer fields populated when
    resume-builder or postprocess.py provides them.
    """
    id: str = ""                                # req_001, req_002, ...
    text: str                                   # the requirement sentence
    type: str = "other"                         # hard_skill, experience, education, domain, tool, soft_skill, certification, other
    priority: str = "must"                      # must, preferred, bonus
    keywords: list[str] = Field(default_factory=list)
    weight: float = 1.0                         # scoring weight from (priority, type) lookup
    evidence_hint: Optional[str] = None         # what kind of bullet would match
    years_required: Optional[int] = None        # only when explicitly stated in JD


class ExtractionEditRequest(BaseModel):
    """Editable extraction fields — used as the PUT /api/jobs/{id} request body."""
    role_title: Optional[str] = None
    company_name: Optional[str] = None
    salary: Optional[str] = None
    location: Optional[str] = None
    job_description: Optional[str] = None
    apply_method: Optional[str] = None


class GenerationData(BaseModel):
    output_folder: Optional[str] = None
    completed_at: Optional[datetime] = None
    # Wave 1: explicit artifact paths (populated by _validate_and_copy)
    resume_path: Optional[str] = None
    cover_letter_path: Optional[str] = None
    selection_log_path: Optional[str] = None


class ScoreData(BaseModel):
    """Match score computed by Resume_Go and pushed back to Job_Queue."""
    overall_score: Optional[float] = None     # 0-100
    keyword_score: Optional[float] = None     # 0-100
    semantic_score: Optional[float] = None    # 0-100
    algorithm: Optional[str] = None           # "jd-match-resume" or "jd-match-all"
    requirement_scores: list[dict] = Field(default_factory=list)
    computed_at: Optional[datetime] = None


class ApplyData(BaseModel):
    """Tracks the apply phase. Populated by dispatch_apply() from apply_result.json."""
    screenshot_path: Optional[str] = None       # path to apply_evidence.png
    result_path: Optional[str] = None           # path to apply_result.json
    form_filled: bool = False                   # True if runner reported form_filled or partial_fill
    resume_uploaded: bool = False               # True if resume upload was confirmed
    applied_at: Optional[datetime] = None       # set by orchestrator at APPLIED transition, not runner
    fields_filled: list[str] = Field(default_factory=list)


class FieldReview(BaseModel):
    """Per-field confidence from the review agent."""
    field_name: str
    filled_value: str
    source: str = "unknown"         # "profile.full_name", "inferred", "generated", "unknown"
    confidence: float = 0.0         # 0.0 to 1.0
    issue: Optional[str] = None


class ReviewData(BaseModel):
    """Structured review from the confidence-gated review agent."""
    overall_confidence: float = 0.0
    fields: list[FieldReview] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    recommendation: str = "review_required"  # "auto_approve" or "review_required"
    screenshot_path: Optional[str] = None
    reviewed_at: Optional[datetime] = None


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
    requirements: list[RequirementItem] = Field(default_factory=list)
    score: ScoreData = Field(default_factory=ScoreData)
    apply: ApplyData = Field(default_factory=ApplyData)
    review: ReviewData = Field(default_factory=ReviewData)
    error: ErrorInfo = Field(default_factory=ErrorInfo)
    worker: WorkerInfo = Field(default_factory=WorkerInfo)
    is_linkedin: bool = False
