"""
Job_Queue configuration.
All external paths and settings in one place.
"""

import os
from pathlib import Path
from typing import Optional

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

RESUME_GENERATOR_ENV_VAR = "JQ_RESUME_GENERATOR_DIR"
DEFAULT_RESUME_GENERATOR_DIR = Path(
    "/Volumes/Hyy Mac mini HD/Program Data/GitHub/Re-Generator"
)
RESUME_GENERATOR_DIR = Path(
    os.environ.get(
        RESUME_GENERATOR_ENV_VAR,
        str(DEFAULT_RESUME_GENERATOR_DIR),
    )
).expanduser()

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
# Callers use `get_cdp_url()` (NOT the old `CDP_URL` module constant) to
# obtain a working CDP endpoint. The function:
#   1. Returns a cached URL if it is still serving /json/version.
#   2. Else uses JQ_CDP_URL from env if set.
#   3. Else probes common ports (9222-9224) for an already-running CDP Chrome.
#   4. Else, on macOS, auto-launches Chrome with --remote-debugging-port=9222
#      and --user-data-dir=~/.chrome-job-queue so logins (e.g. LinkedIn)
#      persist across extractions and survive server restarts.
#
# Contract change: this replaces the import-time `CDP_URL = _detect_cdp_url()`
# snapshot. Callers may now trigger a Chrome subprocess on first call.
import subprocess as _subprocess
import sys as _sys
import threading as _threading
import time as _time
import urllib.request as _urllib_request

CDP_USER_DATA_DIR = Path(
    os.environ.get("JQ_CDP_USER_DATA_DIR", os.path.expanduser("~/.chrome-job-queue"))
)
CDP_PRIMARY_PORT = int(os.environ.get("JQ_CDP_PORT", "9222"))
CDP_PROBE_PORTS = (CDP_PRIMARY_PORT, 9223, 9224)
CDP_AUTO_LAUNCH = os.environ.get("JQ_CDP_AUTO_LAUNCH", "1").lower() not in ("0", "false", "no")
CDP_LAUNCH_TIMEOUT = float(os.environ.get("JQ_CDP_LAUNCH_TIMEOUT", "15"))
CHROME_BINARY = os.environ.get(
    "JQ_CHROME_BINARY",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

_cdp_lock = _threading.Lock()
_cdp_cache: str = ""


def _probe_cdp_port(port: int, timeout: float = 0.5) -> bool:
    """Return True iff Chrome's DevTools /json/version answers 200 on `port`."""
    url = f"http://127.0.0.1:{port}/json/version"
    try:
        req = _urllib_request.Request(url)
        # Bypass any system proxy for localhost (corporate proxies break CDP)
        handler = _urllib_request.ProxyHandler({})
        opener = _urllib_request.build_opener(handler)
        resp = opener.open(req, timeout=timeout)
        return resp.status == 200
    except Exception:
        return False


def _detect_cdp_url() -> str:
    """Return JQ_CDP_URL from env, or probe for an already-running CDP Chrome."""
    explicit = os.environ.get("JQ_CDP_URL", "")
    if explicit:
        return explicit
    for port in CDP_PROBE_PORTS:
        if _probe_cdp_port(port):
            return f"http://localhost:{port}"
    return ""


def _launch_persistent_chrome() -> bool:
    """Spawn a detached Chrome on CDP_PRIMARY_PORT with a persistent user-data-dir.

    Returns True if /json/version answers 200 within CDP_LAUNCH_TIMEOUT seconds.
    macOS-only. On other platforms (or if the Chrome binary is missing) returns
    False and the caller falls back to the old behavior (ephemeral Chrome per
    extraction, no session persistence).
    """
    if _sys.platform != "darwin":
        print(f"[config] CDP auto-launch only supported on macOS (got {_sys.platform})")
        return False
    if not os.path.exists(CHROME_BINARY):
        print(f"[config] Chrome binary not found at {CHROME_BINARY} — skipping auto-launch")
        return False
    CDP_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        CHROME_BINARY,
        f"--remote-debugging-port={CDP_PRIMARY_PORT}",
        f"--user-data-dir={CDP_USER_DATA_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    print(
        f"[config] Launching persistent Chrome on port {CDP_PRIMARY_PORT} "
        f"(user-data-dir={CDP_USER_DATA_DIR})"
    )
    try:
        # start_new_session=True detaches Chrome from the server's process
        # group so it survives Ctrl-C / server shutdown. We intentionally do
        # NOT track this PID — the user owns the Chrome window's lifecycle.
        _subprocess.Popen(
            cmd,
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        print(f"[config] Failed to spawn Chrome: {exc}")
        return False
    deadline = _time.monotonic() + CDP_LAUNCH_TIMEOUT
    while _time.monotonic() < deadline:
        if _probe_cdp_port(CDP_PRIMARY_PORT, timeout=0.5):
            return True
        _time.sleep(0.25)
    print(
        f"[config] Chrome launched but /json/version did not respond within "
        f"{CDP_LAUNCH_TIMEOUT}s"
    )
    return False


def get_cdp_url(auto_launch: Optional[bool] = None) -> str:
    """Return a working CDP URL, auto-launching a persistent Chrome if needed.

    Args:
        auto_launch: If True, spawn Chrome when no running instance is detected.
            Defaults to module-level CDP_AUTO_LAUNCH (which honors the
            JQ_CDP_AUTO_LAUNCH env var). Pass False to gate behavior on
            Chrome's presence without side effects (e.g. the review-agent
            check that runs after apply).

    Returns:
        URL like "http://localhost:9222" if a CDP Chrome is reachable, else "".
        Empty string means callers should fall back to the old behavior
        (browser-use spawns an ephemeral Chrome with no session persistence).

    Side effects:
        May spawn a Chrome subprocess on first call. The subprocess is
        detached (start_new_session=True) and outlives the server.
    """
    global _cdp_cache
    if auto_launch is None:
        auto_launch = CDP_AUTO_LAUNCH
    with _cdp_lock:
        # Fast path: cached URL is still alive.
        if _cdp_cache:
            try:
                cached_port = int(_cdp_cache.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                cached_port = CDP_PRIMARY_PORT
            if _probe_cdp_port(cached_port):
                return _cdp_cache
            # Stale — user closed the browser. Fall through to redetect/launch.
            _cdp_cache = ""

        detected = _detect_cdp_url()
        if detected:
            _cdp_cache = detected
            print(f"[config] CDP detected: {detected}")
            return detected

        if auto_launch and _launch_persistent_chrome():
            _cdp_cache = f"http://localhost:{CDP_PRIMARY_PORT}"
            print(f"[config] CDP auto-launched: {_cdp_cache}")
            return _cdp_cache

        return ""


# Import-time log only — auto-launch is deferred to the first orchestrator
# call so a server that never runs extractions does not spawn a browser.
_initial_cdp = _detect_cdp_url()
if _initial_cdp:
    _cdp_cache = _initial_cdp
    print(f"[config] CDP auto-detected at import: {_initial_cdp}")
elif CDP_AUTO_LAUNCH and _sys.platform == "darwin":
    print(
        "[config] No CDP Chrome detected — will auto-launch persistent Chrome "
        "on first extraction"
    )
else:
    print(
        "[config] No CDP Chrome detected — extractions will launch an "
        "ephemeral browser (set JQ_CDP_AUTO_LAUNCH=1 on macOS to persist sessions)"
    )

# ── Mock mode (for testing when LLM credentials are unavailable) ──
MOCK_EXTRACTION = os.environ.get("JQ_MOCK_EXTRACTION", "").lower() in ("1", "true", "yes")

# ── Temp file paths ───────────────────────────────────────
import tempfile
TEMP_DIR = Path(tempfile.gettempdir())


def jd_temp_path(job_id: str) -> Path:
    """Temp file path for job description during generation."""
    return TEMP_DIR / f"jq_{job_id}_jd.txt"


# ── Generation contract ──────────────────────────────────
# Path to apply-jd skill in the resume generator.  When present, injected via
# --append-system-prompt-file for deterministic workflow invocation.
APPLY_JD_SKILL_FILE = (
    RESUME_GENERATOR_DIR / ".claude" / "skills" / "apply-jd" / "SKILL.md"
)
