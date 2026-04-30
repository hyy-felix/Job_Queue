"""Tests for Wave 3: structured JD requirements.

Covers:
- RequirementItem model
- String[] to structured conversion (_extract_requirements)
- Structured requirements passed through from selection_log
- Persistence roundtrip
- Backward compatibility with jobs missing requirements field
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

import config
from models import Job, JobStatus, RequirementItem, SCHEMA_VERSION
from orchestrator import Orchestrator
from persistence import JsonJobStore


class TestRequirementItemModel:

    def test_minimal_creation(self):
        req = RequirementItem(text="5+ years Python experience")
        assert req.text == "5+ years Python experience"
        assert req.id == ""
        assert req.type == "other"
        assert req.priority == "must"
        assert req.keywords == []
        assert req.weight == 1.0
        assert req.evidence_hint is None
        assert req.years_required is None

    def test_full_creation(self):
        req = RequirementItem(
            id="req_001",
            text="5+ years Python",
            type="experience",
            priority="must",
            keywords=["python", "experience"],
            weight=1.25,
            evidence_hint="Python project with duration",
            years_required=5,
        )
        assert req.id == "req_001"
        assert req.type == "experience"
        assert req.weight == 1.25
        assert req.years_required == 5

    def test_serialization_roundtrip(self):
        req = RequirementItem(
            id="req_003",
            text="Kubernetes experience",
            type="tool",
            priority="preferred",
            keywords=["kubernetes", "k8s"],
            weight=0.60,
        )
        data = req.model_dump()
        restored = RequirementItem.model_validate(data)
        assert restored.text == req.text
        assert restored.type == "tool"
        assert restored.weight == 0.60


class TestJobWithRequirements:

    def test_job_default_empty_requirements(self):
        job = Job(source_url="https://example.com")
        assert job.requirements == []

    def test_job_with_requirements_roundtrip(self):
        job = Job(source_url="https://example.com")
        job.requirements = [
            RequirementItem(id="req_001", text="Python"),
            RequirementItem(id="req_002", text="CAD", type="tool"),
        ]
        json_str = job.model_dump_json()
        restored = Job.model_validate_json(json_str)
        assert len(restored.requirements) == 2
        assert restored.requirements[0].text == "Python"
        assert restored.requirements[1].type == "tool"

    def test_backward_compat_missing_requirements(self, tmp_path):
        """Old v1 jobs without requirements field should get empty list."""
        store = JsonJobStore(jobs_dir=tmp_path / "jobs")
        job = store.create_job("https://example.com/old-job")
        # Simulate v1 job: remove requirements from persisted JSON
        path = store._status_path(job.job_id)
        raw = json.loads(path.read_text())
        raw.pop("requirements", None)
        raw["schema_version"] = 1
        path.write_text(json.dumps(raw))
        # Read back — should get empty list default
        loaded = store.get_job(job.job_id)
        assert loaded.requirements == []

    def test_requirements_persisted_to_disk(self, tmp_path):
        store = JsonJobStore(jobs_dir=tmp_path / "jobs")
        job = store.create_job("https://example.com/req-test")
        job.requirements = [
            RequirementItem(id="req_001", text="Python 5+ years", type="experience",
                            priority="must", keywords=["python"], weight=1.25),
        ]
        store.update_job(job)
        reloaded = store.get_job(job.job_id)
        assert len(reloaded.requirements) == 1
        assert reloaded.requirements[0].text == "Python 5+ years"
        assert reloaded.requirements[0].type == "experience"
        assert reloaded.requirements[0].weight == 1.25


class TestExtractRequirements:

    @pytest.fixture
    def orch(self):
        return Orchestrator.__new__(Orchestrator)

    def test_converts_string_array(self, orch, tmp_path):
        sl = tmp_path / "selection_log.json"
        sl.write_text(json.dumps({
            "jd_requirements": [
                "5+ years Python experience",
                "Knowledge of Kubernetes",
                "Bachelor's degree in CS",
            ],
            "selected_projects": [],
        }))
        reqs = orch._extract_requirements(sl)
        assert len(reqs) == 3
        assert reqs[0].id == "req_001"
        assert reqs[0].text == "5+ years Python experience"
        assert reqs[0].type == "other"
        assert reqs[0].priority == "must"
        assert reqs[0].weight == 1.0
        assert reqs[1].id == "req_002"
        assert reqs[2].id == "req_003"

    def test_prefers_structured_requirements(self, orch, tmp_path):
        """If selection_log has structured_requirements, use those instead."""
        sl = tmp_path / "selection_log.json"
        sl.write_text(json.dumps({
            "jd_requirements": ["Python", "CAD"],
            "structured_requirements": [
                {"text": "Python 5+", "type": "experience", "priority": "must",
                 "keywords": ["python"], "weight": 1.25},
                {"text": "SolidWorks CAD", "type": "tool", "priority": "preferred",
                 "keywords": ["solidworks", "cad"], "weight": 0.60},
            ],
            "selected_projects": [],
        }))
        reqs = orch._extract_requirements(sl)
        assert len(reqs) == 2
        assert reqs[0].type == "experience"
        assert reqs[0].weight == 1.25
        assert reqs[1].type == "tool"
        assert reqs[1].weight == 0.60

    def test_empty_selection_log(self, orch, tmp_path):
        sl = tmp_path / "selection_log.json"
        sl.write_text(json.dumps({"jd_requirements": [], "selected_projects": []}))
        reqs = orch._extract_requirements(sl)
        assert reqs == []

    def test_missing_file(self, orch, tmp_path):
        sl = tmp_path / "nonexistent.json"
        reqs = orch._extract_requirements(sl)
        assert reqs == []

    def test_invalid_json(self, orch, tmp_path):
        sl = tmp_path / "bad.json"
        sl.write_text("not json{{{")
        reqs = orch._extract_requirements(sl)
        assert reqs == []

    def test_strips_empty_strings(self, orch, tmp_path):
        sl = tmp_path / "selection_log.json"
        sl.write_text(json.dumps({
            "jd_requirements": ["Valid requirement", "", "   ", "Another valid"],
            "selected_projects": [],
        }))
        reqs = orch._extract_requirements(sl)
        assert len(reqs) == 2
        assert reqs[0].text == "Valid requirement"
        assert reqs[1].text == "Another valid"

    def test_structured_with_missing_text_skipped(self, orch, tmp_path):
        sl = tmp_path / "selection_log.json"
        sl.write_text(json.dumps({
            "jd_requirements": [],
            "structured_requirements": [
                {"text": "Valid", "type": "hard_skill"},
                {"type": "tool"},  # missing text — should be skipped
                {"text": "", "type": "other"},  # empty text — should be skipped
            ],
            "selected_projects": [],
        }))
        reqs = orch._extract_requirements(sl)
        assert len(reqs) == 1
        assert reqs[0].text == "Valid"


class TestBackwardCompatRealOutput:
    """Test against real generation outputs on this machine."""

    @pytest.fixture
    def real_selection_log(self):
        candidates = [
            config.RESUME_GENERATOR_DIR
            / "applications"
            / "03272026"
            / "Teradyne_Mechanical_Engineer"
            / "note"
            / "selection_log.json",
            config.RESUME_GENERATOR_DIR
            / "applications"
            / "03272026"
            / "Olio_Labs_Mechanical_Engineer"
            / "note"
            / "selection_log.json",
        ]
        for c in candidates:
            if c.exists():
                return c
        pytest.skip("No real selection_log.json found")

    def test_real_output_converts_to_structured(self, real_selection_log):
        orch = Orchestrator.__new__(Orchestrator)
        reqs = orch._extract_requirements(real_selection_log)
        assert len(reqs) > 0
        for req in reqs:
            assert req.id  # non-empty
            assert req.text  # non-empty
            assert req.type
            assert req.priority
            assert req.weight > 0
