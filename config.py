"""
Job_Queue configuration.
All external paths and settings in one place.
"""

import os
from pathlib import Path

# ── Project paths ──────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent.resolve()
JOBS_DIR = PROJECT_DIR / "jobs"

# ── External system paths ─────────────────────────────────────
BROWSER_USE_REPO = Path(
    os.environ.get(
        "JQ_BROWSER_USE_REPO",
        "/Volumes/Hyy Mac mini HD/Program Data/GitHub/browser-use",
    )
)

WORK_DIR = Path(
    os.environ.get(
        "JQ_WORK_DIR",
        "/Users/felixhyy/Desktop/work",
    )
)

CLAUDE_CLI = os.environ.get("JQ_CLAUDE_CLI", "claude")

# ── Subprocess settings ───────────────────────────────────────
EXTRACTION_TIMEOUT_SECONDS = int(os.environ.get("JQ_EXTRACTION_TIMEOUT", "600"))  # 10 min
GENERATION_TIMEOUT_SECONDS = int(os.environ.get("JQ_GENERATION_TIMEOUT", "1800"))  # 30 min
MAX_CONCURRENT_EXTRACTIONS = int(os.environ.get("JQ_MAX_EXTRACTIONS", "3"))

# ── Server settings ───────────────────────────────────────────
HOST = "127.0.0.1"
PORT = int(os.environ.get("JQ_PORT", "8000"))

# ── Generation lock ───────────────────────────────────────────
GENERATION_LOCK_FILE = JOBS_DIR / ".generation_lock"

# ── Extraction script path ────────────────────────────────────
EXTRACT_SCRIPT = PROJECT_DIR / "extractors" / "extract_job.py"
APPLY_SCRIPT = PROJECT_DIR / "appliers" / "apply_job.py"

# ── Apply settings ───────────────────────────────────────────────
APPLY_TIMEOUT_SECONDS = int(os.environ.get("JQ_APPLY_TIMEOUT", "600"))  # 10 min
PROFILE_PATH = PROJECT_DIR / "profile.json"
BROWSER_USE_PROJECT_DIR = BROWSER_USE_REPO  # alias for clarity

# ── Persistent browser (CDP) ──────────────────────────────────
# Set to connect to an already-running Chrome instance instead of launching a new one.
# Example: JQ_CDP_URL=http://localhost:9222
# Launch Chrome with: /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
#   --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-job-queue"
CDP_URL = os.environ.get("JQ_CDP_URL", "")

# ── Mock mode (for testing when LLM credentials are unavailable) ──
MOCK_EXTRACTION = os.environ.get("JQ_MOCK_EXTRACTION", "").lower() in ("1", "true", "yes")

# ── Auto-submit ──────────────────────────────────────────────
AUTO_SUBMIT = os.environ.get("JQ_AUTO_SUBMIT", "").lower() in ("1", "true", "yes")

# ── Email notification ───────────────────────────────────────
GMAIL_APP_PASSWORD = os.environ.get("JQ_GMAIL_APP_PASSWORD", "")
# Note: GMAIL_USER and NOTIFICATION_EMAIL are resolved at send time
# in notifications.py (from env vars with profile.json fallback),
# NOT at config import time.

# ── Temp file paths ───────────────────────────────────────
import tempfile
TEMP_DIR = Path(tempfile.gettempdir())


def jd_temp_path(job_id: str) -> Path:
    """Temp file path for job description during generation."""
    return TEMP_DIR / f"jq_{job_id}_jd.txt"
