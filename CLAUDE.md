# Job_Queue

Local job application orchestration system. Paste job URLs, extract structured data via browser-use, review/edit, then generate tailored resume + cover letter PDFs via an existing Claude-based workflow.

## Architecture

```
server.py → orchestrator.py → persistence.py
                ↓                    ↓
         extractors/            models.py
         extract_job.py         (Pydantic v2, state machine, schema v2)
                ↓
         generators/            appliers/
         (Claude CLI subprocess)  apply_job.py (browser-use subprocess)
                                  review_agent.py (Anthropic API, inline)
```

- **server.py**: FastAPI routes + SSE. Reads from store, all mutations via orchestrator.
- **orchestrator.py**: Owns ALL state transitions. Dispatch extraction, generation queue loop, crash recovery, cancellation, score reception.
- **persistence.py**: JsonJobStore with atomic writes (temp+rename), per-job locks, status migration (v1→v2).
- **models.py**: Pydantic models, state machine (LEGAL_TRANSITIONS), single source of truth.
- **config.py**: All external paths, configurable via environment variables.

## State Machine (v2)

```
SUBMITTED → EXTRACTING → SCRAPED → QUEUED → GENERATING → GENERATED → SCORED
               ↓                                ↓            ↓          ↓
        EXTRACTION_FAILED               GENERATION_FAILED   ↓        APPLYING → REVIEW_REQUIRED → APPLIED
               ↑ retry                      ↑ retry         ↓          ↓
                                                          APPLYING   APPLY_FAILED
                                                                       ↑ retry

GENERATED/SCORED → MANUAL_APPLY → APPLIED   (LinkedIn)
Any non-terminal → CANCELLED
```

**Removed statuses (v1→v2):** `READY_FOR_REVIEW` → `SCRAPED`, `COMPLETED` → `GENERATED`, `APPLY_QUEUED` → removed (LinkedIn routing now inline in `queue_apply`). New: `SCORED`.

## SSE Event Payload

Events are emitted on every status change. Payload is progressive:

```json
{
  "job_id": "abc123",
  "source_url": "https://...",
  "status": "scored",
  "role_title": "Engineer",
  "company_name": "Acme",
  "location": "SF",
  "salary": "$150k",
  "error": null,
  "jd_text": "...",                    // SCRAPED+
  "generation": {                      // GENERATED+
    "output_folder": "...",
    "resume_path": "...",
    "cover_letter_path": "...",
    "selection_log_path": "...",
    "completed_at": "2026-03-29T..."
  },
  "requirements_count": 11,            // GENERATED+
  "score": {                           // SCORED+
    "overall_score": 72.5,
    "keyword_score": 65.0,
    "semantic_score": 80.0,
    "algorithm": "jd-match-resume"
  }
}
```

## Score Push Contract

Resume_Go computes scores and pushes them back:

```
PUT /api/jobs/{id}/score
{
  "overall_score": 72.5,       // required, 0-100
  "keyword_score": 65.0,       // optional
  "semantic_score": 80.0,      // optional
  "algorithm": "jd-match-resume",
  "requirement_scores": [...]  // optional
}
```

Transitions GENERATED → SCORED. Idempotent: second push from SCORED returns existing job.

## Generation Contract

Generation uses `--append-system-prompt-file` to inject the apply-jd skill workflow:

```python
cmd = ["claude", "--print", "--dangerously-skip-permissions",
       "--append-system-prompt-file", str(SKILL_FILE),
       "-p", prompt]
```

Required outputs: `resume.pdf`, `cover_letter.pdf`, `note/selection_log.json` (with `jd_requirements` key).

Graceful fallback: if SKILL.md is missing, uses free-form prompt.

## Canonical Job Schema (status.json)

```json
{
  "schema_version": 2,
  "job_id": "abc123",
  "source_url": "https://...",
  "status": "scored",
  "extraction": { "role_title", "company_name", "salary", "location", "job_description", "scraped_at" },
  "generation": { "output_folder", "completed_at", "resume_path", "cover_letter_path", "selection_log_path" },
  "requirements": [{ "id", "text", "type", "priority", "keywords", "weight", "evidence_hint", "years_required" }],
  "score": { "overall_score", "keyword_score", "semantic_score", "algorithm", "requirement_scores", "computed_at" },
  "apply": { ... },
  "review": { ... },
  "error": { "last_error", "retry_count" },
  "is_linkedin": false
}
```

## External Systems (DO NOT MODIFY)

1. **browser-use** at `$JQ_BROWSER_USE_REPO` — AI browser agent for extraction
2. **Claude workflow** at `$JQ_WORK_DIR` (`/Users/felixhyy/Desktop/work`) — resume/cover letter generator

## Commands

```bash
# Install deps
pip3 install -r requirements.txt

# Run server (port 8080 is the default)
python3 server.py

# Run with persistent Chrome (recommended for apply + review):
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-job-queue"
JQ_CDP_URL=http://localhost:9222 python3 server.py

# Run with mock extraction (no LLM needed)
JQ_MOCK_EXTRACTION=true python3 server.py

# Run tests
python3 -m pytest tests/ -v
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JQ_PORT` | 8080 | Server port |
| `JQ_CDP_URL` | (empty) | CDP URL for persistent Chrome (e.g., `http://localhost:9222`) |
| `JQ_MOCK_EXTRACTION` | false | Use mock extraction (no LLM needed) |
| `JQ_BROWSER_USE_REPO` | (hardcoded path) | Path to browser-use repo |
| `JQ_WORK_DIR` | (hardcoded path) | Path to Claude workflow directory |
| `JQ_EXTRACTION_TIMEOUT` | 600 | Extraction timeout in seconds |
| `JQ_GENERATION_TIMEOUT` | 3600 | Generation timeout in seconds |
| `JQ_APPLY_TIMEOUT` | 600 | Apply subprocess timeout in seconds |
| `JQ_REVIEW_TIMEOUT` | 120 | Review agent LLM call timeout in seconds |
| `ANTHROPIC_API_KEY` | (empty) | Required for confidence-gated review. If unset, review is skipped. |

## Testing

```bash
python3 -m pytest tests/ -v  # 174 tests: models, persistence, apply, generation, score, SSE, requirements
```
