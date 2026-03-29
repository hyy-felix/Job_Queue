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
GENERATION_TIMEOUT_SECONDS = int(os.environ.get("JQ_GENERATION_TIMEOUT", "3600"))  # 60 min
MAX_CONCURRENT_EXTRACTIONS = int(os.environ.get("JQ_MAX_EXTRACTIONS", "3"))

# ── Server settings ───────────────────────────────────────────
HOST = os.environ.get("JQ_HOST", "127.0.0.1")
PORT = int(os.environ.get("JQ_PORT", "8080"))

# ── Generation lock ───────────────────────────────────────────
GENERATION_LOCK_FILE = JOBS_DIR / ".generation_lock"

# ── Extraction script path ────────────────────────────────────
EXTRACT_SCRIPT = PROJECT_DIR / "extractors" / "extract_job.py"
APPLY_SCRIPT = PROJECT_DIR / "appliers" / "apply_job.py"

# ── Apply settings ───────────────────────────────────────────────
APPLY_TIMEOUT_SECONDS = int(os.environ.get("JQ_APPLY_TIMEOUT", "600"))  # 10 min
REVIEW_TIMEOUT_SECONDS = int(os.environ.get("JQ_REVIEW_TIMEOUT", "120"))  # 2 min
PROFILE_PATH = PROJECT_DIR / "profile.json"
BROWSER_USE_PROJECT_DIR = BROWSER_USE_REPO  # alias for clarity

# ── Persistent browser (CDP) ──────────────────────────────────
# Set to connect to an already-running Chrome instance instead of launching a new one.
# Example: JQ_CDP_URL=http://localhost:9222
# Launch Chrome with: /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
#   --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-job-queue"
#
# Auto-detection: if JQ_CDP_URL is not set, probe common ports (9222-9224)
# for a running Chrome with remote debugging. This avoids requiring the env
# var when Chrome is already running with --remote-debugging-port.
def _detect_cdp_url() -> str:
    """Return JQ_CDP_URL from env, or auto-detect a running CDP Chrome."""
    explicit = os.environ.get("JQ_CDP_URL", "")
    if explicit:
        return explicit

    import urllib.request
    for port in (9222, 9223, 9224):
        url = f"http://127.0.0.1:{port}/json/version"
        try:
            req = urllib.request.Request(url)
            # Bypass any system proxy for localhost
            handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(handler)
            resp = opener.open(req, timeout=0.5)
            if resp.status == 200:
                return f"http://localhost:{port}"
        except Exception:
            continue
    return ""

CDP_URL = _detect_cdp_url()
if CDP_URL:
    print(f"[config] CDP auto-detected: {CDP_URL}")
else:
    print("[config] No CDP Chrome detected — extractions will launch a new browser")

# ── Mock mode (for testing when LLM credentials are unavailable) ──
MOCK_EXTRACTION = os.environ.get("JQ_MOCK_EXTRACTION", "").lower() in ("1", "true", "yes")

# ── Temp file paths ───────────────────────────────────────
import tempfile
TEMP_DIR = Path(tempfile.gettempdir())


def jd_temp_path(job_id: str) -> Path:
    """Temp file path for job description during generation."""
    return TEMP_DIR / f"jq_{job_id}_jd.txt"


# ── Generation contract ──────────────────────────────────
# Path to apply-jd skill in the work project.  When present, injected via
# --append-system-prompt-file for deterministic workflow invocation.
APPLY_JD_SKILL_FILE = WORK_DIR / ".claude" / "skills" / "apply-jd" / "SKILL.md"
