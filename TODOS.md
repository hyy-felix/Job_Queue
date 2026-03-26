# TODOS

## V2: Cancel-race protection for multi-process
**What:** Add compare-and-set guard in `_transition()` to prevent cancel_job and dispatch_apply from racing on state writes.
**Why:** Currently, single-writer + asyncio makes this near-impossible. But if the system ever goes multi-process (e.g., separate worker processes for apply), cancel and completion could race. The underlying update path has no optimistic locking.
**Depends on:** Only relevant if moving away from single-writer architecture.
**Context:** Identified during eng review of apply feature (2026-03-26). Codex flagged cancellation races as a theoretical concern. V1 risk is near-zero due to asyncio cooperative scheduling.

## V2: Idempotency for generation retries
**What:** Add idempotency check before generation retry — check if output folder already exists for this company/role before re-invoking Claude.
**Why:** Prevents duplicate artifacts on retry after false-negative validation (e.g., crash after output was created but before validation completed).
**Depends on:** OUTPUT_PATH prompt approach must be working first.
**Context:** Codex flagged during eng review (2026-03-25). V1 risk is low because sequential processing + fail-closed limits the window. Becomes important if generation is ever parallelized.

## ~~V2: Extraction should open a new tab~~ — DONE (2026-03-26)
Fixed: Both extract_job.py and apply_job.py now use `initial_actions=[{'navigate': {'url': url, 'new_tab': True}}]` + `directly_open_url=False`. New tabs open in CDP Chrome, existing tabs stay untouched.

## V2: Job retention/archival policy
**What:** Add ability to archive or purge completed/cancelled jobs older than N days.
**Why:** Prevents unbounded disk growth in `jobs/` directory over months of daily use.
**Depends on:** Nothing. Pure enhancement.
**Context:** Codex flagged during eng review (2026-03-25). Manual cleanup is acceptable for V1. Consider a simple `python cleanup.py --older-than 30d` script as the first implementation.
