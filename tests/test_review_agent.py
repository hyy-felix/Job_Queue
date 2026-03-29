"""Tests for the confidence-gated review agent."""

import asyncio
import json
import sys
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _run(coro):
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


@pytest.fixture
def tmp_artifacts(tmp_path):
    """Create mock apply artifacts for review agent testing."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    result = {
        "status": "form_filled",
        "screenshot_path": "apply_evidence.png",
        "fields_filled": ["Full Name", "Email"],
        "fields_failed": [],
        "resume_uploaded": True,
        "dom_fields": [
            {"name": "fullName", "type": "text", "value": "Felix Hu",
             "placeholder": "Full Name", "visible": True},
            {"name": "email", "type": "email", "value": "felix@example.com",
             "placeholder": "Email", "visible": True},
        ],
    }
    result_path = output_dir / "apply_result.json"
    result_path.write_text(json.dumps(result))

    screenshot_path = output_dir / "apply_evidence.png"
    screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps({
        "full_name": "Felix Hu",
        "email": "felix@example.com",
        "phone": "555-1234",
    }))

    return result_path, screenshot_path, profile_path


@pytest.fixture
def mock_anthropic():
    """Inject a mock anthropic module into sys.modules so `import anthropic` works."""
    mock_mod = MagicMock()
    old = sys.modules.get('anthropic')
    sys.modules['anthropic'] = mock_mod
    yield mock_mod
    if old is not None:
        sys.modules['anthropic'] = old
    else:
        sys.modules.pop('anthropic', None)
    # Force reimport of review_agent so it picks up the real (or missing) module next time
    sys.modules.pop('appliers.review_agent', None)


class TestReviewAgentHappyPath:

    def test_returns_review_data(self, event_loop, tmp_artifacts, mock_anthropic):
        asyncio.set_event_loop(event_loop)
        result_path, screenshot_path, profile_path = tmp_artifacts

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "overall_confidence": 0.9,
            "fields": [
                {"field_name": "Full Name", "filled_value": "Felix Hu",
                 "source": "profile.full_name", "confidence": 1.0, "issue": None},
            ],
            "flags": [],
            "recommendation": "auto_approve",
        }))]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_anthropic.AsyncAnthropic.return_value = mock_client

        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'test-key'}):
            # Force reimport to pick up the mocked anthropic
            sys.modules.pop('appliers.review_agent', None)
            from appliers.review_agent import run_review
            result = _run(run_review(result_path, screenshot_path, profile_path))

        assert result["overall_confidence"] == 0.9
        assert len(result["fields"]) == 1
        assert result["recommendation"] == "auto_approve"


class TestReviewAgentFallbacks:

    def test_missing_api_key_returns_auto_approve(self, event_loop, tmp_artifacts):
        asyncio.set_event_loop(event_loop)
        result_path, screenshot_path, profile_path = tmp_artifacts

        with patch.dict('os.environ', {}, clear=True):
            import os
            os.environ.pop('ANTHROPIC_API_KEY', None)

            sys.modules.pop('appliers.review_agent', None)
            from appliers.review_agent import run_review
            result = _run(run_review(result_path, screenshot_path, profile_path))

        assert result["recommendation"] == "auto_approve"
        assert any("API_KEY" in f for f in result["flags"])

    def test_missing_screenshot_still_works(self, event_loop, tmp_artifacts, mock_anthropic):
        asyncio.set_event_loop(event_loop)
        result_path, screenshot_path, profile_path = tmp_artifacts
        screenshot_path.unlink()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "overall_confidence": 0.7,
            "fields": [],
            "flags": ["No screenshot available"],
            "recommendation": "review_required",
        }))]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_anthropic.AsyncAnthropic.return_value = mock_client

        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'test-key'}):
            sys.modules.pop('appliers.review_agent', None)
            from appliers.review_agent import run_review
            result = _run(run_review(result_path, screenshot_path, profile_path))

        assert result["overall_confidence"] == 0.7

    def test_malformed_llm_json_returns_fallback(self, event_loop, tmp_artifacts, mock_anthropic):
        asyncio.set_event_loop(event_loop)
        result_path, screenshot_path, profile_path = tmp_artifacts

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="This is not JSON at all")]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_anthropic.AsyncAnthropic.return_value = mock_client

        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'test-key'}):
            sys.modules.pop('appliers.review_agent', None)
            from appliers.review_agent import run_review
            result = _run(run_review(result_path, screenshot_path, profile_path))

        assert result["recommendation"] == "auto_approve"
        assert any("parse" in f.lower() for f in result["flags"])

    def test_api_call_failure_returns_fallback(self, event_loop, tmp_artifacts, mock_anthropic):
        asyncio.set_event_loop(event_loop)
        result_path, screenshot_path, profile_path = tmp_artifacts

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=Exception("API connection error")
        )
        mock_anthropic.AsyncAnthropic.return_value = mock_client

        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'test-key'}):
            sys.modules.pop('appliers.review_agent', None)
            from appliers.review_agent import run_review
            result = _run(run_review(result_path, screenshot_path, profile_path))

        assert result["recommendation"] == "auto_approve"
        assert any("API call failed" in f for f in result["flags"])
