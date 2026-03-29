"""
Confidence-gated review agent.

Reads apply artifacts (apply_result.json + apply_evidence.png + profile.json),
sends them to Claude via the Anthropic vision API, and returns structured
per-field confidence scores.

IMPORT BOUNDARY: This module imports `anthropic` (AsyncAnthropic) which is
isolated from the rest of the project. The orchestrator calls this module's
async function and converts the returned dict into a ReviewData model.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("job_queue")

REVIEW_PROMPT = """\
You are a job application review agent. You are given:
1. A screenshot of a job application form after it was auto-filled
2. A list of DOM form fields with their current values
3. The candidate's profile information

Compare every filled field against the candidate profile. For each field, assess:
- Whether the value matches the profile (high confidence)
- Whether the value was inferred but plausible (medium confidence)
- Whether the value looks wrong or suspicious (low confidence)

Return a JSON object with these exact keys:
{
  "overall_confidence": <float 0.0-1.0>,
  "fields": [
    {
      "field_name": "<label or name of the field>",
      "filled_value": "<what was filled in>",
      "source": "<where the value came from: profile.field_name, inferred, generated, unknown>",
      "confidence": <float 0.0-1.0>,
      "issue": "<null or description of the problem>"
    }
  ],
  "flags": ["<any concerns worth highlighting>"],
  "recommendation": "auto_approve" or "review_required"
}

Be conservative: if you're unsure, give lower confidence.
No markdown. No explanation. Just the JSON.
"""


async def run_review(
    apply_result_path: Path,
    screenshot_path: Path,
    profile_path: Path,
) -> dict[str, Any]:
    """Run the confidence-gated review on apply artifacts.

    Returns a plain dict matching the ReviewData schema.
    The orchestrator converts this to a ReviewData model.

    Graceful fallback: returns auto_approve if anything fails.
    """
    # Guard: check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("Review agent skipped: ANTHROPIC_API_KEY not set")
        return _fallback_result("ANTHROPIC_API_KEY not set")

    try:
        import anthropic
    except ImportError:
        logger.warning("Review agent skipped: anthropic package not installed")
        return _fallback_result("anthropic package not installed")

    # Read apply result
    try:
        apply_result = json.loads(apply_result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Review agent: failed to read apply result: %s", exc)
        return _fallback_result(f"Failed to read apply result: {exc}")

    # Read screenshot
    screenshot_b64 = None
    if screenshot_path.exists():
        try:
            screenshot_bytes = screenshot_path.read_bytes()
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        except Exception as exc:
            logger.warning("Review agent: failed to read screenshot: %s", exc)

    # Read profile
    profile = {}
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Review agent: failed to read profile: %s", exc)

    # Build the prompt content
    dom_fields = apply_result.get("dom_fields", [])
    fields_filled = apply_result.get("fields_filled", [])

    text_context = (
        f"Candidate profile:\n{json.dumps(profile, indent=2)}\n\n"
        f"Fields the agent reported filling: {json.dumps(fields_filled)}\n\n"
        f"DOM form fields with current values:\n{json.dumps(dom_fields, indent=2)}"
    )

    # Build message content
    content: list[dict] = []
    if screenshot_b64:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": screenshot_b64,
            },
        })
    content.append({"type": "text", "text": f"{REVIEW_PROMPT}\n\n{text_context}"})

    # Call the Anthropic API (async)
    try:
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:
        logger.warning("Review agent API call failed: %s", exc)
        return _fallback_result(f"API call failed: {exc}")

    # Parse response
    try:
        response_text = response.content[0].text.strip()
        # Strip markdown fences if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            response_text = "\n".join(lines).strip()

        result = json.loads(response_text)

        # Validate expected keys
        return {
            "overall_confidence": float(result.get("overall_confidence", 0.0)),
            "fields": result.get("fields", []),
            "flags": result.get("flags", []),
            "recommendation": result.get("recommendation", "review_required"),
            "screenshot_path": str(screenshot_path) if screenshot_path.exists() else None,
        }
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        logger.warning("Review agent: failed to parse LLM response: %s", exc)
        return _fallback_result(f"Failed to parse LLM response: {exc}")


def _fallback_result(reason: str) -> dict[str, Any]:
    """Return a safe fallback that skips review."""
    return {
        "overall_confidence": 0.0,
        "fields": [],
        "flags": [f"Review skipped: {reason}"],
        "recommendation": "auto_approve",
        "screenshot_path": None,
    }
