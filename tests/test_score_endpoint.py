"""Tests for the score reception endpoint (Wave 2).

Covers:
- GENERATED → SCORED transition via receive_score()
- Score data validation (range, required fields)
- Rejection from wrong states
- Score data persistence in job model
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from models import Job, JobStatus, ScoreData
from orchestrator import Orchestrator, StateTransitionError
from persistence import JsonJobStore


@pytest.fixture
def event_loop():
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
    asyncio.set_event_loop(event_loop)
    with patch('orchestrator.config') as mock_config:
        mock_config.JOBS_DIR = tmp_jobs_dir
        mock_config.GENERATION_LOCK_FILE = tmp_jobs_dir / ".generation_lock"
        o = Orchestrator(store)
        o.set_event_queue(asyncio.Queue())
        o._mock_config = mock_config
        yield o


def _run(coro):
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


def _make_generated_job(store, tmp_jobs_dir):
    job = store.create_job("https://example.com/job/score-test")
    for status in [
        JobStatus.EXTRACTING, JobStatus.SCRAPED,
        JobStatus.QUEUED, JobStatus.GENERATING, JobStatus.GENERATED,
    ]:
        job.status = status
        store.update_job(job)
    return job


class TestReceiveScore:

    def test_generated_to_scored(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        result = _run(orch.receive_score(job.job_id, {
            "overall_score": 72.5,
            "keyword_score": 65.0,
            "semantic_score": 80.0,
            "algorithm": "jd-match-resume",
            "requirement_scores": [{"req": "Python", "score": 0.9}],
        }))
        assert result.status == JobStatus.SCORED
        assert result.score.overall_score == 72.5
        assert result.score.keyword_score == 65.0
        assert result.score.semantic_score == 80.0
        assert result.score.algorithm == "jd-match-resume"
        assert len(result.score.requirement_scores) == 1
        assert result.score.computed_at is not None

    def test_score_persisted_on_disk(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        _run(orch.receive_score(job.job_id, {
            "overall_score": 85.0,
            "keyword_score": 80.0,
            "semantic_score": 90.0,
        }))
        reloaded = store.get_job(job.job_id)
        assert reloaded.status == JobStatus.SCORED
        assert reloaded.score.overall_score == 85.0

    def test_rejects_from_wrong_state(self, orch, store, tmp_jobs_dir):
        job = store.create_job("https://example.com/job/wrong-state")
        # Still in SUBMITTED
        with pytest.raises(StateTransitionError, match="expected generated"):
            _run(orch.receive_score(job.job_id, {"overall_score": 50.0}))

    @pytest.mark.invariant
    def test_invariant_score_blocked_from_needs_bullet_approval(self, orch, store, tmp_jobs_dir):
        job = store.create_job("https://example.com/job/needs-bullets-score")
        job.status = JobStatus.NEEDS_BULLET_APPROVAL
        store.update_job(job)

        with pytest.raises(StateTransitionError, match="expected generated"):
            _run(orch.receive_score(job.job_id, {"overall_score": 50.0}))

        reloaded = store.get_job(job.job_id)
        assert reloaded.status == JobStatus.NEEDS_BULLET_APPROVAL

    def test_idempotent_from_scored(self, orch, store, tmp_jobs_dir):
        """Second score push is idempotent — returns existing job, no error."""
        job = _make_generated_job(store, tmp_jobs_dir)
        _run(orch.receive_score(job.job_id, {"overall_score": 50.0}))
        # Already SCORED — second push returns existing job without error
        result = _run(orch.receive_score(job.job_id, {"overall_score": 60.0}))
        assert result.status == JobStatus.SCORED
        assert result.score.overall_score == 50.0  # original score preserved

    def test_rejects_missing_overall_score(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        with pytest.raises(StateTransitionError, match="overall_score is required"):
            _run(orch.receive_score(job.job_id, {"keyword_score": 50.0}))

    def test_rejects_out_of_range_score(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        with pytest.raises(StateTransitionError, match="between 0 and 100"):
            _run(orch.receive_score(job.job_id, {"overall_score": 150.0}))

    def test_rejects_negative_score(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        with pytest.raises(StateTransitionError, match="between 0 and 100"):
            _run(orch.receive_score(job.job_id, {"overall_score": -5.0}))

    def test_zero_score_accepted(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        result = _run(orch.receive_score(job.job_id, {"overall_score": 0.0}))
        assert result.status == JobStatus.SCORED
        assert result.score.overall_score == 0.0

    def test_perfect_score_accepted(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        result = _run(orch.receive_score(job.job_id, {"overall_score": 100.0}))
        assert result.status == JobStatus.SCORED
        assert result.score.overall_score == 100.0

    def test_default_algorithm(self, orch, store, tmp_jobs_dir):
        job = _make_generated_job(store, tmp_jobs_dir)
        result = _run(orch.receive_score(job.job_id, {"overall_score": 50.0}))
        assert result.score.algorithm == "jd-match-resume"

    def test_scored_then_apply(self, orch, store, tmp_jobs_dir):
        """After scoring, apply should work from SCORED state."""
        job = _make_generated_job(store, tmp_jobs_dir)
        _run(orch.receive_score(job.job_id, {"overall_score": 80.0}))
        scored = store.get_job(job.job_id)
        assert scored.status == JobStatus.SCORED
        # SCORED → APPLYING is a legal transition
        from models import is_legal_transition
        assert is_legal_transition(JobStatus.SCORED, JobStatus.APPLYING)
