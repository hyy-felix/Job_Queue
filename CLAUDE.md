# Job_Queue

Local job application orchestration system. Paste job URLs, extract structured data via browser-use, review/edit, then generate tailored resume + cover letter PDFs via an existing Claude-based workflow.

## Architecture

```
server.py → orchestrator.py → persistence.py
                ↓                    ↓
         extractors/            models.py
         extract_job.py         (Pydantic, state machine)
                ↓
         generators/
         (Claude CLI subprocess)
```

- **server.py**: FastAPI routes + SSE. Reads from store, all mutations via orchestrator.
- **orchestrator.py**: Owns ALL state transitions. Dispatch extraction, generation queue loop, crash recovery, cancellation.
- **persistence.py**: JsonJobStore with atomic writes (temp+rename), per-job locks.
- **models.py**: Pydantic models, state machine (LEGAL_TRANSITIONS), single source of truth.
- **config.py**: All external paths, configurable via environment variables.

## External Systems (DO NOT MODIFY)

1. **browser-use** at `$JQ_BROWSER_USE_REPO` — AI browser agent for extraction
2. **Claude workflow** at `$JQ_WORK_DIR` (`/Users/felixhyy/Desktop/work`) — resume/cover letter generator

## Commands

```bash
# Install deps
pip3 install -r requirements.txt

# Run server
JQ_PORT=8080 python3 server.py

# Run with persistent Chrome (for LinkedIn login):
# 1. Launch Chrome with remote debugging:
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-job-queue"
# 2. Log into LinkedIn in that Chrome window (once)
# 3. Start server with CDP URL:
JQ_CDP_URL=http://localhost:9222 JQ_PORT=8080 python3 server.py

# Run with mock extraction (no LLM needed)
JQ_MOCK_EXTRACTION=true JQ_PORT=8080 python3 server.py

# Run tests
python3 -m pytest tests/ -v
```

## Key Design Decisions

- JSON-first persistence (per-job `state/status.json`)
- `asyncio.create_subprocess_exec` with `start_new_session=True` for subprocesses
- Direct file redirect for subprocess logs (not PIPE — avoids deadlock)
- `asyncio.Semaphore(3)` for extraction concurrency (hardcoded)
- Global generation lock (`jobs/.generation_lock`)
- SSE with debounce + full-state-fetch on reconnect
- Two-tier output discovery: OUTPUT_PATH prompt (primary) + tree-diff (fallback)
- All state transitions go through `orchestrator._transition()`
- Platform: macOS only. Server binds to 127.0.0.1 only.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JQ_PORT` | 8000 | Server port |
| `JQ_CDP_URL` | (empty) | CDP URL for persistent Chrome (e.g., `http://localhost:9222`) |
| `JQ_MOCK_EXTRACTION` | false | Use mock extraction (no LLM needed) |
| `JQ_BROWSER_USE_REPO` | (hardcoded path) | Path to browser-use repo |
| `JQ_WORK_DIR` | (hardcoded path) | Path to Claude workflow directory |
| `JQ_EXTRACTION_TIMEOUT` | 600 | Extraction timeout in seconds |
| `JQ_GENERATION_TIMEOUT` | 1800 | Generation timeout in seconds |
| `JQ_AUTO_SUBMIT` | false | Auto-click submit after filling form |
| `JQ_GMAIL_APP_PASSWORD` | (empty) | Gmail App Password for email notifications. If unset, notifications are silently skipped. |
| `JQ_GMAIL_USER` | (from profile.json) | Gmail sender address. Falls back to profile.json email field. |
| `JQ_NOTIFICATION_EMAIL` | (from JQ_GMAIL_USER) | Recipient email for notifications. |

## Testing

```bash
python3 -m pytest tests/ -v  # 52 tests: models + persistence + apply
```
