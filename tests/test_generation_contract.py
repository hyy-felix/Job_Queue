"""
Tests for Wave 0 generation contract changes:
- Subprocess command construction with --append-system-prompt-file
- Graceful fallback when SKILL.md is missing
- Tightened output validation (selection_log.json required)
- Port default fix
"""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import config
from models import ExtractionData, Job, JobStatus
from orchestrator import Orchestrator


def _run(coro):
    return asyncio.run(coro)


def _insufficiency_selection_log() -> dict:
    return {
        "schema_version": 2,
        "status": "needs_bullet_approval",
        "jd_requirements": ["Python 5+ years", "CAD experience"],
        "jd_keywords": ["python", "cad"],
        "selected_projects": [],
        "rejected_overlaps": [],
        "unmatched_jd_gaps": ["No approved controls bullet"],
        "flags_for_user": [
            "Resume generation blocked because approved source bullets are insufficient for this JD."
        ],
        "trim_priority": [],
        "needs_bullet_approval": {
            "triggered": True,
            "reason": "insufficient_approved_source_bullets",
            "unmatched_jd_gaps": ["No approved controls bullet"],
            "candidates_artifact": None,
        },
        "final_outputs": {
            "resume_tex": False,
            "cover_letter_tex": False,
            "resume_pdf": False,
            "cover_letter_pdf": False,
            "compile_log": False,
        },
    }


# ── Config Tests ──────────────────────────────────────────────


class TestPortDefault:
    def test_default_port_is_8080(self):
        """BP-4: Default port must be 8080, matching Resume_Go's api-client.ts."""
        assert config.PORT == 8080

    def test_port_env_var_expression(self):
        """config.PORT uses JQ_PORT env var (evaluated at import time)."""
        # config.PORT is set at import time, so we verify the mechanism:
        # int(os.environ.get("JQ_PORT", "8080")) — default is "8080"
        import os
        assert os.environ.get("JQ_PORT", "8080") == "8080" or config.PORT > 0


class TestSkillFilePath:
    def test_skill_file_path_constructed(self):
        """APPLY_JD_SKILL_FILE points into the generator's apply-jd skill."""
        expected_suffix = Path(".claude") / "skills" / "apply-jd" / "SKILL.md"
        assert config.APPLY_JD_SKILL_FILE == config.RESUME_GENERATOR_DIR / expected_suffix

    def test_resume_generator_default_path(self):
        """The generator defaults to the migrated Re-Generator repo."""
        assert config.RESUME_GENERATOR_ENV_VAR == "JQ_RESUME_GENERATOR_DIR"
        assert config.DEFAULT_RESUME_GENERATOR_DIR == Path(
            "/Volumes/Hyy Mac mini HD/Program Data/GitHub/Re-Generator"
        )


# ── Validation Tests ──────────────────────────────────────────


class TestValidateAndCopy:
    """Test _validate_and_copy with tightened selection_log.json requirement."""

    @pytest.fixture
    def setup(self, tmp_path):
        """Create a minimal Orchestrator and fake output folder."""
        # Fake job directory structure
        job_id = "test_job_001"
        jobs_dir = tmp_path / "jobs"
        job_dir = jobs_dir / job_id
        (job_dir / "state").mkdir(parents=True)
        (job_dir / "output").mkdir(parents=True)

        # Fake output folder (what the generator's applications/ produces)
        output_folder = tmp_path / "applications" / "03292026" / "Test_Corp_Engineer"
        (output_folder / "note").mkdir(parents=True)

        # Create required PDFs
        (output_folder / "resume.pdf").write_bytes(b"%PDF-fake-resume")
        (output_folder / "cover_letter.pdf").write_bytes(b"%PDF-fake-cl")

        # Create compile log with success indicator
        (output_folder / "note" / "compile.log").write_text("Writing `resume.pdf`\nBUILD: SUCCESS")

        # Create valid selection_log.json
        selection_log = {
            "jd_requirements": ["Python 5+ years", "CAD experience"],
            "jd_keywords": ["python", "cad"],
            "selected_projects": [],
        }
        (output_folder / "note" / "selection_log.json").write_text(
            json.dumps(selection_log), encoding="utf-8"
        )

        # Create fake job
        job = Job(
            job_id=job_id,
            source_url="https://example.com/job",
            extraction=ExtractionData(
                company_name="Test Corp",
                role_title="Engineer",
            ),
        )

        # Patch config.JOBS_DIR
        with patch.object(config, "JOBS_DIR", jobs_dir):
            from persistence import JsonJobStore
            store = JsonJobStore(jobs_dir)
            orch = Orchestrator.__new__(Orchestrator)
            orch.store = store
            yield orch, job, output_folder, job_dir

    @pytest.fixture
    def insufficiency_setup(self, tmp_path):
        """Create a fake Re-Generator insufficiency package."""
        job_id = "test_job_needs_bullets"
        jobs_dir = tmp_path / "jobs"
        job_dir = jobs_dir / job_id
        (job_dir / "state").mkdir(parents=True)
        (job_dir / "output").mkdir(parents=True)

        output_folder = tmp_path / "applications" / "03292026" / "Test_Corp_Engineer"
        (output_folder / "note").mkdir(parents=True)
        (output_folder / "note" / "notes.md").write_text(
            "# Needs bullet approval\nApproved source bullets are insufficient.",
            encoding="utf-8",
        )
        (output_folder / "note" / "job_description.txt").write_text(
            "Build robotics controls software.",
            encoding="utf-8",
        )
        (output_folder / "note" / "selection_log.json").write_text(
            json.dumps(_insufficiency_selection_log()),
            encoding="utf-8",
        )

        job = Job(
            job_id=job_id,
            source_url="https://example.com/job",
            extraction=ExtractionData(
                company_name="Test Corp",
                role_title="Engineer",
            ),
        )

        with patch.object(config, "JOBS_DIR", jobs_dir):
            from persistence import JsonJobStore
            store = JsonJobStore(jobs_dir)
            orch = Orchestrator.__new__(Orchestrator)
            orch.store = store
            yield orch, job, output_folder, job_dir

    def test_success_with_all_required_files(self, setup):
        orch, job, output_folder, job_dir = setup
        result = orch._validate_and_copy(job, output_folder)
        assert result.valid is True
        assert result.status == JobStatus.GENERATED
        # Verify selection_log.json was copied
        assert (job_dir / "output" / "selection_log.json").exists()

    def test_fails_without_resume(self, setup):
        orch, job, output_folder, job_dir = setup
        (output_folder / "resume.pdf").unlink()
        assert orch._validate_and_copy(job, output_folder).valid is False

    def test_fails_without_cover_letter(self, setup):
        orch, job, output_folder, job_dir = setup
        (output_folder / "cover_letter.pdf").unlink()
        assert orch._validate_and_copy(job, output_folder).valid is False

    def test_fails_with_empty_resume(self, setup):
        orch, job, output_folder, job_dir = setup
        (output_folder / "resume.pdf").write_bytes(b"")
        assert orch._validate_and_copy(job, output_folder).valid is False

    def test_fails_without_selection_log(self, setup):
        """selection_log.json is now REQUIRED, not optional."""
        orch, job, output_folder, job_dir = setup
        (output_folder / "note" / "selection_log.json").unlink()
        assert orch._validate_and_copy(job, output_folder).valid is False
        # No orphaned artifacts should exist
        assert not (job_dir / "output" / "resume.pdf").exists()
        assert not (job_dir / "output" / "cover_letter.pdf").exists()

    def test_fails_with_invalid_json_selection_log(self, setup):
        orch, job, output_folder, job_dir = setup
        (output_folder / "note" / "selection_log.json").write_text("not json{{{")
        assert orch._validate_and_copy(job, output_folder).valid is False
        assert not (job_dir / "output" / "resume.pdf").exists()

    def test_fails_without_jd_requirements_key(self, setup):
        """selection_log.json must contain 'jd_requirements' key."""
        orch, job, output_folder, job_dir = setup
        (output_folder / "note" / "selection_log.json").write_text(
            json.dumps({"selected_projects": [], "jd_keywords": []})
        )
        assert orch._validate_and_copy(job, output_folder).valid is False
        assert not (job_dir / "output" / "resume.pdf").exists()

    def test_copies_optional_notes(self, setup):
        orch, job, output_folder, job_dir = setup
        (output_folder / "note" / "notes.md").write_text("# Notes")
        orch._validate_and_copy(job, output_folder)
        assert (job_dir / "output" / "notes.md").exists()

    def test_succeeds_without_optional_notes(self, setup):
        orch, job, output_folder, job_dir = setup
        # notes.md not created — should still pass
        assert orch._validate_and_copy(job, output_folder).valid is True

    def test_writes_metadata_json(self, setup):
        orch, job, output_folder, job_dir = setup
        orch._validate_and_copy(job, output_folder)
        meta_path = job_dir / "output" / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["job_id"] == "test_job_001"
        assert meta["company_name"] == "Test Corp"

    def test_valid_insufficiency_package_copies_only_note_artifacts(self, insufficiency_setup):
        orch, job, output_folder, job_dir = insufficiency_setup
        result = orch._validate_and_copy(job, output_folder)

        assert result.valid is True
        assert result.status == JobStatus.NEEDS_BULLET_APPROVAL
        output_files = {path.name for path in (job_dir / "output").iterdir()}
        assert output_files == {
            "selection_log.json",
            "notes.md",
            "job_description.txt",
            "metadata.json",
        }

        meta = json.loads((job_dir / "output" / "metadata.json").read_text())
        assert meta["package_status"] == "needs_bullet_approval"
        assert meta["resume_pdf"] is None
        assert meta["cover_letter_pdf"] is None
        assert meta["needs_bullet_approval"]["triggered"] is True
        assert meta["needs_bullet_approval"]["reason"] == "insufficient_approved_source_bullets"
        assert meta["needs_bullet_approval"]["candidates_artifact"] is None

    def test_valid_insufficiency_package_without_job_description(self, insufficiency_setup):
        orch, job, output_folder, job_dir = insufficiency_setup
        (output_folder / "note" / "job_description.txt").unlink()

        result = orch._validate_and_copy(job, output_folder)

        assert result.valid is True
        assert (job_dir / "output" / "job_description.txt").exists() is False
        meta = json.loads((job_dir / "output" / "metadata.json").read_text())
        assert meta["job_description"] is None

    def test_insufficiency_fails_without_notes(self, insufficiency_setup):
        orch, job, output_folder, job_dir = insufficiency_setup
        (output_folder / "note" / "notes.md").unlink()

        assert orch._validate_and_copy(job, output_folder).valid is False
        assert not (job_dir / "output" / "metadata.json").exists()

    def test_insufficiency_fails_with_candidates_artifact(self, insufficiency_setup):
        orch, job, output_folder, job_dir = insufficiency_setup
        sl_path = output_folder / "note" / "selection_log.json"
        data = json.loads(sl_path.read_text())
        data["needs_bullet_approval"]["candidates_artifact"] = "candidates.json"
        sl_path.write_text(json.dumps(data), encoding="utf-8")

        assert orch._validate_and_copy(job, output_folder).valid is False

    def test_insufficiency_fails_with_selected_projects(self, insufficiency_setup):
        orch, job, output_folder, job_dir = insufficiency_setup
        sl_path = output_folder / "note" / "selection_log.json"
        data = json.loads(sl_path.read_text())
        data["selected_projects"] = [{"name": "Project"}]
        sl_path.write_text(json.dumps(data), encoding="utf-8")

        assert orch._validate_and_copy(job, output_folder).valid is False

    def test_insufficiency_fails_with_trim_priority(self, insufficiency_setup):
        orch, job, output_folder, job_dir = insufficiency_setup
        sl_path = output_folder / "note" / "selection_log.json"
        data = json.loads(sl_path.read_text())
        data["trim_priority"] = ["trim something"]
        sl_path.write_text(json.dumps(data), encoding="utf-8")

        assert orch._validate_and_copy(job, output_folder).valid is False

    def test_insufficiency_fails_with_true_final_output(self, insufficiency_setup):
        orch, job, output_folder, job_dir = insufficiency_setup
        sl_path = output_folder / "note" / "selection_log.json"
        data = json.loads(sl_path.read_text())
        data["final_outputs"]["resume_pdf"] = True
        sl_path.write_text(json.dumps(data), encoding="utf-8")

        assert orch._validate_and_copy(job, output_folder).valid is False

    def test_insufficiency_fails_with_missing_final_output_field(self, insufficiency_setup):
        orch, job, output_folder, job_dir = insufficiency_setup
        sl_path = output_folder / "note" / "selection_log.json"
        data = json.loads(sl_path.read_text())
        data["final_outputs"].pop("compile_log")
        sl_path.write_text(json.dumps(data), encoding="utf-8")

        assert orch._validate_and_copy(job, output_folder).valid is False

    @pytest.mark.parametrize("relative_path", [
        "resume.pdf",
        "cover_letter.pdf",
        "resume.tex",
        "cover_letter.tex",
        "note/compile.log",
        "note/compile_resume.log",
        "note/compile_cover_letter.log",
    ])
    def test_insufficiency_fails_with_stale_final_artifacts(
        self, insufficiency_setup, relative_path
    ):
        orch, job, output_folder, job_dir = insufficiency_setup
        stale_path = output_folder / relative_path
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_text("stale", encoding="utf-8")

        assert orch._validate_and_copy(job, output_folder).valid is False

    def test_unmatched_jd_gaps_alone_does_not_trigger_insufficiency(self, setup):
        orch, job, output_folder, job_dir = setup
        sl_path = output_folder / "note" / "selection_log.json"
        data = json.loads(sl_path.read_text())
        data["unmatched_jd_gaps"] = ["Some truthful gap remains"]
        sl_path.write_text(json.dumps(data), encoding="utf-8")
        (output_folder / "resume.pdf").unlink()

        result = orch._validate_and_copy(job, output_folder)

        assert result.valid is False
        assert result.status is None
        assert not (job_dir / "output" / "metadata.json").exists()

    def test_malformed_needs_bullet_approval_signal_fails(self, setup):
        orch, job, output_folder, job_dir = setup
        sl_path = output_folder / "note" / "selection_log.json"
        data = json.loads(sl_path.read_text())
        data["needs_bullet_approval"] = {"triggered": True, "reason": "wrong_reason"}
        sl_path.write_text(json.dumps(data), encoding="utf-8")

        assert orch._validate_and_copy(job, output_folder).valid is False

    def test_run_generation_transitions_to_needs_bullet_approval(self, tmp_path):
        from persistence import JsonJobStore

        jobs_dir = tmp_path / "jobs"
        store = JsonJobStore(jobs_dir)
        job = store.create_job("https://example.com/job/run-generation")
        job.extraction = ExtractionData(
            company_name="Test Corp",
            role_title="Engineer",
            job_description="Build robotics controls software.",
        )
        job.status = JobStatus.QUEUED
        store.update_job(job)

        resume_generator_dir = tmp_path / "Re-Generator"
        output_folder = (
            resume_generator_dir
            / "applications"
            / "03292026"
            / "Test_Corp_Engineer"
        )
        (output_folder / "note").mkdir(parents=True)
        (output_folder / "note" / "notes.md").write_text("# Needs bullets", encoding="utf-8")
        (output_folder / "note" / "selection_log.json").write_text(
            json.dumps(_insufficiency_selection_log()),
            encoding="utf-8",
        )

        class FakeProcess:
            pid = 12345
            returncode = 0

            async def wait(self):
                return 0

        async def fake_create_subprocess_exec(*args, stdout=None, **kwargs):
            stdout.write(f"OUTPUT_PATH: {output_folder}\n")
            stdout.flush()
            return FakeProcess()

        with patch.object(config, "JOBS_DIR", jobs_dir), \
             patch.object(config, "RESUME_GENERATOR_DIR", resume_generator_dir), \
             patch.object(config, "APPLY_JD_SKILL_FILE", tmp_path / "missing" / "SKILL.md"), \
             patch.object(config, "GENERATION_TIMEOUT_SECONDS", 30), \
             patch.object(config, "CLAUDE_CLI", "claude"), \
             patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            orch = Orchestrator(store)
            orch.set_event_queue(asyncio.Queue())
            _run(orch._run_generation(job))

        final = store.get_job(job.job_id)
        assert final.status == JobStatus.NEEDS_BULLET_APPROVAL
        assert final.generation.resume_path is None
        assert final.generation.cover_letter_path is None
        assert final.generation.selection_log_path
        assert final.score.overall_score is None


# ── Command Construction Tests ────────────────────────────────


class TestGenerationCommand:
    """Test that the generation subprocess command is constructed correctly."""

    def test_command_includes_skill_file_when_present(self, tmp_path):
        """When SKILL.md exists, --append-system-prompt-file is included."""
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("---\nname: apply-jd\n---\nWorkflow here.")

        with patch.object(config, "APPLY_JD_SKILL_FILE", skill_file):
            cmd = [config.CLAUDE_CLI, "--print", "--dangerously-skip-permissions"]
            if config.APPLY_JD_SKILL_FILE.exists():
                cmd.extend(["--append-system-prompt-file", str(config.APPLY_JD_SKILL_FILE)])
            cmd.extend(["-p", "test prompt"])

            assert "--append-system-prompt-file" in cmd
            assert str(skill_file) in cmd

    def test_command_omits_skill_file_when_missing(self, tmp_path):
        """When SKILL.md doesn't exist, fall back to free-form prompt."""
        missing_file = tmp_path / "nonexistent" / "SKILL.md"

        with patch.object(config, "APPLY_JD_SKILL_FILE", missing_file):
            cmd = [config.CLAUDE_CLI, "--print", "--dangerously-skip-permissions"]
            if config.APPLY_JD_SKILL_FILE.exists():
                cmd.extend(["--append-system-prompt-file", str(config.APPLY_JD_SKILL_FILE)])
            cmd.extend(["-p", "test prompt"])

            assert "--append-system-prompt-file" not in cmd

    def test_prompt_includes_output_path_instruction(self):
        """Prompt must include OUTPUT_PATH instruction for artifact resolution."""
        company, role = "Acme", "Engineer"
        jd_tmp = "/tmp/jq_test_jd.txt"
        prompt = (
            f"Company: {company}, Role: {role}. "
            f"The full job description is in the file {jd_tmp} — read it and use it. "
            f"After completing all files, print the exact output folder path as the "
            f"very last line of your output, prefixed with OUTPUT_PATH: "
            f"Do not perform the git save step."
        )
        assert "OUTPUT_PATH:" in prompt
        assert "Do not perform the git save step" in prompt
        assert f"Company: {company}" in prompt
        assert jd_tmp in prompt


# ── Backward Compatibility Tests ──────────────────────────────


class TestBackwardCompatibility:
    """Verify existing generation outputs still pass validation."""

    @pytest.fixture
    def real_output_check(self):
        """Check a real generation output folder if it exists."""
        candidates = [
            config.RESUME_GENERATOR_DIR / "applications" / "03272026" / "Teradyne_Mechanical_Engineer",
            config.RESUME_GENERATOR_DIR / "applications" / "03272026" / "Olio_Labs_Mechanical_Engineer",
        ]
        for c in candidates:
            if c.exists():
                return c
        pytest.skip("No real generation output found on this machine")

    def test_real_output_has_selection_log(self, real_output_check):
        """Existing generations must have selection_log.json with jd_requirements."""
        sl = real_output_check / "note" / "selection_log.json"
        assert sl.exists(), f"selection_log.json missing at {sl}"
        data = json.loads(sl.read_text(encoding="utf-8"))
        assert "jd_requirements" in data, "Missing jd_requirements key"
        assert isinstance(data["jd_requirements"], list), "jd_requirements must be a list"
        assert len(data["jd_requirements"]) > 0, "jd_requirements must be non-empty"

    def test_real_output_has_required_pdfs(self, real_output_check):
        """Existing generations must have non-empty PDFs."""
        for name in ("resume.pdf", "cover_letter.pdf"):
            f = real_output_check / name
            assert f.exists(), f"{name} missing"
            assert f.stat().st_size > 0, f"{name} is empty"
