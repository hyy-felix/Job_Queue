#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  . ".venv/bin/activate"
elif [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  . "venv/bin/activate"
fi

STATIC_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --static-only) STATIC_ONLY=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

GENERATOR_WORKTREE="$ROOT/../Re-Generator_phase5b_hardening"
FRONTEND_WORKTREE="$ROOT/../Relevel_frontend_phase5a_staleness"
VALIDATOR="$GENERATOR_WORKTREE/scripts/validate_approved_accpro_bullets.py"

if [ "$STATIC_ONLY" -eq 1 ]; then
  python3 - <<'PY'
import json
from pathlib import Path

fixture_dir = Path("tests/e2e/fixtures")
json_files = sorted(fixture_dir.rglob("*.json"))
assert json_files, "no JSON fixtures found"
for path in json_files:
    json.loads(path.read_text(encoding="utf-8"))
print(f"parsed {len(json_files)} JSON fixtures")
PY

  if [ ! -f "$VALIDATOR" ]; then
    echo "missing approved ledger validator: $VALIDATOR" >&2
    exit 1
  fi
  python3 "$VALIDATOR" tests/e2e/fixtures/approved_accpro_bullets.valid.json
  python3 "$VALIDATOR" tests/e2e/fixtures/approved_accpro_bullets.stale.json

  python3 - <<'PY'
from pathlib import Path

required = {
    "tests/e2e/test_scenario_6_provenance_leak.py": [
        "test_invariant_no_candidate_in_final_pdf",
        "test_invariant_provenance_tokens_not_in_final_pdf",
    ],
    "tests/e2e/test_scenario_7_apply_gate.py": [
        "test_invariant_apply_blocked_from_needs_bullet_approval",
    ],
    "tests/e2e/test_scenario_4_stale_record.py": [
        "test_invariant_stale_record_excluded_from_final",
    ],
}
for file_name, function_names in required.items():
    text = Path(file_name).read_text(encoding="utf-8")
    for function_name in function_names:
        assert "@pytest.mark.invariant" in text, f"{file_name} missing invariant marker"
        assert f"def {function_name}" in text, f"{file_name} missing {function_name}"

from tests.e2e.helpers import LEAK_TOKENS
canonical = [
    "source_record_id",
    "approved_accpro:",
    "approved_text_hash",
    "source_fingerprint",
    "casefile_id",
    "claim_id",
    "approval_scope",
]
assert LEAK_TOKENS == canonical, LEAK_TOKENS
print("invariant tests and leak-token list are present")
PY

  for token in \
    "source_record_id" \
    "approved_accpro:" \
    "approved_text_hash" \
    "source_fingerprint" \
    "casefile_id" \
    "claim_id" \
    "approval_scope"; do
    grep -R -F "$token" tests/e2e >/dev/null
  done

  if [ ! -x tests/e2e/stubs/claude_stub.py ]; then
    echo "claude_stub.py is not executable" >&2
    exit 1
  fi
  python3 - <<'PY'
import importlib.util
from pathlib import Path

path = Path("tests/e2e/stubs/claude_stub.py")
spec = importlib.util.spec_from_file_location("claude_stub", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
print("claude_stub.py imports cleanly")
PY
  exit 0
fi

if [ ! -d "$FRONTEND_WORKTREE" ]; then
  echo "missing Relevel_frontend worktree: $FRONTEND_WORKTREE" >&2
  exit 1
fi
if [ ! -f "$VALIDATOR" ]; then
  echo "missing approved ledger validator: $VALIDATOR" >&2
  exit 1
fi

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/jq_phase6_e2e.XXXXXX")"
PORT="$(python3 - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"

mkdir -p "$TMP_ROOT/sources" "$TMP_ROOT/casefiles" "$TMP_ROOT/job_queue_output"
cp tests/e2e/fixtures/approved_accpro_bullets.valid.json \
  "$TMP_ROOT/sources/approved_accpro_bullets.json"
cp tests/e2e/fixtures/casefiles/*.json "$TMP_ROOT/casefiles/"

export APPROVED_ACCPRO_BULLETS_PATH="$TMP_ROOT/sources/approved_accpro_bullets.json"
export APPROVED_ACCPRO_LEDGER_VALIDATOR="$VALIDATOR"
export ACCPRO_CASEFILES_DIR="$TMP_ROOT/casefiles"
export JOB_QUEUE_OUTPUT_ROOT="$TMP_ROOT/job_queue_output"
export EMBEDDING_PROVIDER="tfidf"
export RELEVEL_FRONTEND_URL="http://127.0.0.1:$PORT/api"

UVICORN_LOG="$TMP_ROOT/relevel_frontend_uvicorn.log"
PYTHONPATH="$FRONTEND_WORKTREE" python3 -m uvicorn \
  backend.main:app \
  --host 127.0.0.1 \
  --port "$PORT" \
  >"$UVICORN_LOG" 2>&1 &
UVICORN_PID=$!

cleanup() {
  status=$?
  if kill -0 "$UVICORN_PID" >/dev/null 2>&1; then
    kill "$UVICORN_PID" >/dev/null 2>&1 || true
    wait "$UVICORN_PID" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT

python3 - <<PY
import sys
import time
import urllib.request

url = "http://127.0.0.1:$PORT/docs"
deadline = time.time() + 20
last = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            if 200 <= response.status < 500:
                sys.exit(0)
    except Exception as exc:
        last = exc
        time.sleep(0.2)
raise SystemExit(f"uvicorn did not become ready at {url}: {last}")
PY

python3 -m pytest tests/e2e -x -q --json-report --json-report-file=tests.json
