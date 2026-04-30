#!/usr/bin/env python3
"""Smoke-check Job_Queue's resume generator path and artifact resolution."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import config  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402


def _fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def _has_required_artifacts(output_dir: Path) -> bool:
    return all(
        [
            (output_dir / "resume.pdf").is_file(),
            (output_dir / "cover_letter.pdf").is_file(),
            (output_dir / "note" / "selection_log.json").is_file(),
        ]
    )


def _find_latest_artifact_dir(applications_dir: Path) -> Path | None:
    candidates: list[Path] = []
    if not applications_dir.is_dir():
        return None

    for date_dir in applications_dir.iterdir():
        if not date_dir.is_dir():
            continue
        for output_dir in date_dir.iterdir():
            if output_dir.is_dir() and _has_required_artifacts(output_dir):
                candidates.append(output_dir)

    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _validate_selection_log(output_dir: Path) -> str | None:
    selection_log = output_dir / "note" / "selection_log.json"
    try:
        data = json.loads(selection_log.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return f"{selection_log} is not readable JSON: {exc}"

    if "jd_requirements" not in data:
        return f"{selection_log} is missing jd_requirements"
    return None


def _resolve_output_folder(output_dir: Path) -> Path | None:
    with tempfile.TemporaryDirectory(prefix="jq-generator-smoke-") as tmp:
        log_path = Path(tmp) / "generation.log"
        log_path.write_text(f"noise\nOUTPUT_PATH: {output_dir}\n", encoding="utf-8")
        orch = Orchestrator.__new__(Orchestrator)
        return orch._resolve_output_folder(
            log_path,
            config.RESUME_GENERATOR_DIR / "applications",
            set(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify Job_Queue can locate Re-Generator and resolve output artifacts."
    )
    parser.add_argument(
        "--output-folder",
        type=Path,
        help="Optional generated application folder to verify instead of auto-detecting one.",
    )
    args = parser.parse_args()

    generator_dir = config.RESUME_GENERATOR_DIR
    applications_dir = generator_dir / "applications"

    if not generator_dir.is_dir():
        return _fail(
            f"{config.RESUME_GENERATOR_ENV_VAR} does not point to a directory: "
            f"{generator_dir}"
        )
    if not config.APPLY_JD_SKILL_FILE.is_file():
        return _fail(f"apply-jd skill missing: {config.APPLY_JD_SKILL_FILE}")

    output_dir = args.output_folder.expanduser() if args.output_folder else None
    if output_dir is None:
        output_dir = _find_latest_artifact_dir(applications_dir)
        if output_dir is None:
            return _fail(f"no generated artifacts found under {applications_dir}")

    output_dir = output_dir.resolve()
    if not output_dir.is_dir():
        return _fail(f"output folder does not exist: {output_dir}")
    if not _has_required_artifacts(output_dir):
        return _fail(
            "output folder must contain resume.pdf, cover_letter.pdf, "
            f"and note/selection_log.json: {output_dir}"
        )

    selection_log_error = _validate_selection_log(output_dir)
    if selection_log_error:
        return _fail(selection_log_error)

    resolved = _resolve_output_folder(output_dir)
    if resolved is None or resolved.resolve() != output_dir:
        return _fail(f"Job_Queue resolver returned {resolved}, expected {output_dir}")

    print(f"OK {config.RESUME_GENERATOR_ENV_VAR}={generator_dir}")
    print(f"OK APPLY_JD_SKILL_FILE={config.APPLY_JD_SKILL_FILE}")
    print(f"OK applications_dir={applications_dir}")
    print(f"OK artifact_output={output_dir}")
    print(f"OK resolver_output={resolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
