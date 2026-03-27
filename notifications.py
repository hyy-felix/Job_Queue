"""
Email notification after successful job application.
Sends via Gmail SMTP with App Password. Best-effort: failures are logged, never block.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import config

logger = logging.getLogger("job_queue")


def _load_email_from_profile() -> str:
    """Read email from profile.json at call time (not import time)."""
    try:
        profile = json.loads(config.PROFILE_PATH.read_text(encoding="utf-8"))
        return profile.get("email", "")
    except Exception:
        return ""


async def send_apply_notification(job) -> bool:
    """Send email notification after successful apply. Returns True if sent.

    Runs blocking smtplib in asyncio.to_thread() to avoid blocking the event loop.
    Gracefully degrades: if JQ_GMAIL_APP_PASSWORD is unset, logs warning and returns False.

    PDF paths are resolved from metadata.json in the job's output directory.
    """
    if not config.GMAIL_APP_PASSWORD:
        logger.warning(
            "JQ_GMAIL_APP_PASSWORD not set — skipping email notification for job %s",
            job.job_id,
        )
        return False

    gmail_user = os.environ.get("JQ_GMAIL_USER", "") or _load_email_from_profile()
    recipient = os.environ.get("JQ_NOTIFICATION_EMAIL", "") or gmail_user
    if not gmail_user or not recipient:
        logger.warning(
            "No sender/recipient email found — skipping notification for job %s",
            job.job_id,
        )
        return False

    # Resolve PDF paths from metadata.json (written by generation phase)
    metadata_path = config.JOBS_DIR / job.job_id / "output" / "metadata.json"
    if not metadata_path.exists():
        logger.warning("No metadata.json for job %s — skipping notification", job.job_id)
        return False

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    resume_pdf = metadata.get("resume_pdf", "")
    cover_letter_pdf = metadata.get("cover_letter_pdf", "")
    resume_path = Path(resume_pdf) if resume_pdf else None
    cover_letter_path = Path(cover_letter_pdf) if cover_letter_pdf else None

    company = job.extraction.company_name or "Unknown Company"
    role = job.extraction.role_title or "Unknown Role"
    resume_name = resume_path.name if resume_path and resume_path.exists() else "N/A"
    cl_name = cover_letter_path.name if cover_letter_path and cover_letter_path.exists() else "N/A (not required)"

    subject = f"Application Submitted: {company} - {role}"
    body = f"""\
Job Application Submitted Successfully

Company: {company}
Role: {role}
URL: {job.source_url}

Files uploaded:
- Resume: {resume_name}
- Cover Letter: {cl_name}

Applied at: {job.apply.applied_at.strftime('%Y-%m-%d %H:%M UTC') if job.apply.applied_at else 'N/A'}
Job ID: {job.job_id}
"""

    def _send_blocking() -> bool:
        msg = MIMEMultipart()
        msg["From"] = gmail_user
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        # Attach resume PDF
        if resume_path and resume_path.exists():
            with open(resume_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=resume_path.name)
                part["Content-Disposition"] = f'attachment; filename="{resume_path.name}"'
                msg.attach(part)

        # Attach cover letter PDF (only if exists)
        if cover_letter_path and cover_letter_path.exists():
            with open(cover_letter_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=cover_letter_path.name)
                part["Content-Disposition"] = f'attachment; filename="{cover_letter_path.name}"'
                msg.attach(part)

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(gmail_user, config.GMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True

    return await asyncio.to_thread(_send_blocking)
