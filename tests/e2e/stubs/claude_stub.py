#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
CLAIM_TEXT = "Implemented closed-loop controls for a robotics platform."
SAFE_COVER_TEXT = "Prepared a focused cover letter for robotics controls work."


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _scenario() -> str:
    scenario = os.environ.get("JQ_E2E_SCENARIO", "sufficient")
    state_file = os.environ.get("JQ_E2E_STATE_FILE")
    if state_file:
        try:
            state = _load_json(Path(state_file))
        except (OSError, json.JSONDecodeError):
            state = {}
        scenario = state.get("scenario") or state.get("mode") or scenario
    return scenario


def _parse_prompt(prompt: str) -> tuple[str, str]:
    match = re.search(r"Company:\s*(.*?),\s*Role:\s*(.*?)\.", prompt)
    if not match:
        return "Unknown", "Unknown"
    return match.group(1).strip() or "Unknown", match.group(2).strip() or "Unknown"


def _safe_path_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return cleaned or "Unknown"


def _output_dir(company: str, role: str) -> Path:
    root = Path(os.environ.get("JQ_RESUME_GENERATOR_DIR", os.getcwd())).resolve()
    today = datetime.now().strftime("%m%d%Y")
    return root / "applications" / today / f"{_safe_path_component(company)}_{_safe_path_component(role)}"


def _active_approved_record() -> dict | None:
    ledger_path = os.environ.get("APPROVED_ACCPRO_BULLETS_PATH")
    if not ledger_path:
        return None
    try:
        ledger = _load_json(Path(ledger_path))
    except (OSError, json.JSONDecodeError):
        return None
    for record in ledger.get("records", []):
        if (
            isinstance(record, dict)
            and record.get("status") == "active"
            and record.get("approval_status") == "approved"
            and record.get("usable_in_resume") is True
            and str(record.get("source_record_id", "")).startswith("approved_accpro:")
        ):
            return record
    return None


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_basic_pdf(path: Path, lines: list[str]) -> None:
    content_lines = ["BT", "/F1 12 Tf", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            content_lines.append("0 -18 Td")
        content_lines.append(f"({_pdf_escape(line)}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_offset = sum(len(chunk) for chunk in chunks)
    chunks.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    chunks.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        chunks.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    chunks.append(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(chunks))


def _write_pdf(path: Path, lines: list[str]) -> None:
    try:
        from reportlab.pdfgen import canvas
    except Exception:
        _write_basic_pdf(path, lines)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pageCompression=0)
    text = pdf.beginText(72, 720)
    text.setFont("Helvetica", 12)
    for line in lines:
        text.textLine(line)
    pdf.drawText(text)
    pdf.save()


def _write_sufficient_package(out_dir: Path, company: str, role: str, record: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    note_dir = out_dir / "note"
    note_dir.mkdir(parents=True, exist_ok=True)

    approved_text = record.get("approved_text") or CLAIM_TEXT
    source_record_id = record["source_record_id"]
    selection_log = deepcopy(_load_json(FIXTURE_DIR / "selection_log_sufficient.json"))
    selection_log["selected_projects"] = [
        {
            "name": "AccPro approved robotics controls bullet",
            "source_type": record.get("source_type", "approved_accpro_bullet"),
            "source_record_id": source_record_id,
            "approved_text": approved_text,
            "matched_jd_requirements": selection_log["jd_requirements"],
        }
    ]
    _write_text(note_dir / "selection_log.json", json.dumps(selection_log, indent=2))
    _write_text(note_dir / "compile.log", "BUILD: SUCCESS\n")
    _write_text(note_dir / "notes.md", "Generated from active approved source bullets.\n")
    _write_text(out_dir / "resume.tex", f"{company} {role}\n{approved_text}\n")
    _write_text(out_dir / "cover_letter.tex", f"{company} {role}\n{SAFE_COVER_TEXT}\n")
    _write_pdf(
        out_dir / "resume.pdf",
        [
            f"{company} - {role}",
            approved_text,
            "Generated only after active approval was available.",
        ],
    )
    _write_pdf(
        out_dir / "cover_letter.pdf",
        [
            f"{company} - {role}",
            SAFE_COVER_TEXT,
            "Generated only after active approval was available.",
        ],
    )


def _write_insufficiency_package(out_dir: Path, prompt: str) -> None:
    for relative in (
        "resume.pdf",
        "cover_letter.pdf",
        "resume.tex",
        "cover_letter.tex",
        "note/compile.log",
        "note/compile_resume.log",
        "note/compile_cover_letter.log",
    ):
        (out_dir / relative).unlink(missing_ok=True)

    note_dir = out_dir / "note"
    note_dir.mkdir(parents=True, exist_ok=True)
    selection_log = deepcopy(_load_json(FIXTURE_DIR / "selection_log_insufficient.json"))
    _write_text(note_dir / "selection_log.json", json.dumps(selection_log, indent=2))
    _write_text(note_dir / "notes.md", (FIXTURE_DIR / "notes.md").read_text(encoding="utf-8"))
    jd_match = re.search(r"file\s+([^ ]+)\s+", prompt)
    if jd_match:
        jd_path = Path(jd_match.group(1))
        try:
            jd_text = jd_path.read_text(encoding="utf-8")
        except OSError:
            jd_text = ""
        if jd_text:
            _write_text(note_dir / "job_description.txt", jd_text)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true")
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    parser.add_argument("--append-system-prompt-file")
    parser.add_argument("-p", "--prompt", required=True)
    args = parser.parse_args(argv)

    prompt = args.prompt
    company, role = _parse_prompt(prompt)
    out_dir = _output_dir(company, role)
    scenario = _scenario()

    record = _active_approved_record()
    if scenario in {"sufficient", "sufficient_after_approval"} and record is not None:
        _write_sufficient_package(out_dir, company, role, record)
    else:
        _write_insufficiency_package(out_dir, prompt)

    print(f"OUTPUT_PATH: {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
