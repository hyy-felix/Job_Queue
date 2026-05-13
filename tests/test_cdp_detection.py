"""Tests for lazy CDP probe + auto-launch in config.get_cdp_url().

Covers the contract change from `config.CDP_URL` (import-time snapshot) to
`config.get_cdp_url()` (call-time function with optional auto-launch).

These tests must NOT spawn a real Chrome process — _subprocess and
_probe_cdp_port are mocked.
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

import config


@pytest.fixture(autouse=True)
def _reset_cdp_cache():
    """Reset module-level state between tests so they don't leak via the cache."""
    config._cdp_cache = ""
    yield
    config._cdp_cache = ""


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv("JQ_CDP_URL", raising=False)
    return monkeypatch


# ── _detect_cdp_url ───────────────────────────────────────────────────


def test_detect_returns_env_when_set(monkeypatch):
    monkeypatch.setenv("JQ_CDP_URL", "http://localhost:5555")
    assert config._detect_cdp_url() == "http://localhost:5555"


def test_detect_returns_empty_when_no_chrome(no_env):
    with mock.patch.object(config, "_probe_cdp_port", return_value=False):
        assert config._detect_cdp_url() == ""


def test_detect_returns_url_when_first_port_alive(no_env):
    def probe(port, timeout=0.5):
        return port == config.CDP_PRIMARY_PORT
    with mock.patch.object(config, "_probe_cdp_port", side_effect=probe):
        assert config._detect_cdp_url() == f"http://localhost:{config.CDP_PRIMARY_PORT}"


def test_detect_falls_through_to_secondary_port(no_env):
    """When 9222 is dead but 9223 answers, return that."""
    def probe(port, timeout=0.5):
        return port == 9223
    with mock.patch.object(config, "_probe_cdp_port", side_effect=probe):
        assert config._detect_cdp_url() == "http://localhost:9223"


# ── get_cdp_url: no auto-launch ───────────────────────────────────────


def test_get_cdp_url_no_chrome_no_autolaunch(no_env):
    """auto_launch=False + no Chrome -> empty string, no spawn."""
    with mock.patch.object(config, "_probe_cdp_port", return_value=False), \
         mock.patch.object(config, "_launch_persistent_chrome") as launch:
        assert config.get_cdp_url(auto_launch=False) == ""
        launch.assert_not_called()


def test_get_cdp_url_picks_up_manually_launched_chrome(no_env):
    """If the user launched Chrome themselves, detect it and skip auto-launch."""
    def probe(port, timeout=0.5):
        return port == config.CDP_PRIMARY_PORT
    with mock.patch.object(config, "_probe_cdp_port", side_effect=probe), \
         mock.patch.object(config, "_launch_persistent_chrome") as launch:
        assert config.get_cdp_url(auto_launch=True) == f"http://localhost:{config.CDP_PRIMARY_PORT}"
        launch.assert_not_called()


# ── get_cdp_url: auto-launch + caching ────────────────────────────────


def test_get_cdp_url_autolaunch_spawns_when_no_chrome(no_env):
    with mock.patch.object(config, "_probe_cdp_port", return_value=False), \
         mock.patch.object(config, "_launch_persistent_chrome", return_value=True) as launch:
        result = config.get_cdp_url(auto_launch=True)
        assert result == f"http://localhost:{config.CDP_PRIMARY_PORT}"
        launch.assert_called_once()


def test_get_cdp_url_caches_alive_url_no_full_sweep(no_env):
    """Second call hits the fast cache path — only the cached port is probed."""
    config._cdp_cache = "http://localhost:9222"
    probe_calls: list[int] = []

    def probe(port, timeout=0.5):
        probe_calls.append(port)
        return port == 9222  # cached port is alive

    with mock.patch.object(config, "_probe_cdp_port", side_effect=probe), \
         mock.patch.object(config, "_launch_persistent_chrome") as launch:
        assert config.get_cdp_url() == "http://localhost:9222"
        # Only the cache liveness probe — not the full PROBE_PORTS sweep
        assert probe_calls == [9222]
        launch.assert_not_called()


def test_get_cdp_url_cache_invalidated_when_port_dies(no_env):
    """When the cached port stops responding, re-detect (and auto-launch if enabled)."""
    config._cdp_cache = "http://localhost:9222"
    with mock.patch.object(config, "_probe_cdp_port", return_value=False), \
         mock.patch.object(config, "_launch_persistent_chrome", return_value=True) as launch:
        result = config.get_cdp_url(auto_launch=True)
        assert result == f"http://localhost:{config.CDP_PRIMARY_PORT}"
        launch.assert_called_once()


def test_get_cdp_url_returns_empty_when_launch_fails(no_env):
    """Launch failure (e.g. Chrome missing) must degrade gracefully to empty."""
    with mock.patch.object(config, "_probe_cdp_port", return_value=False), \
         mock.patch.object(config, "_launch_persistent_chrome", return_value=False):
        assert config.get_cdp_url(auto_launch=True) == ""


# ── _launch_persistent_chrome: command shape ──────────────────────────


def test_launch_persistent_chrome_uses_correct_args(no_env, monkeypatch, tmp_path):
    """Verify the Chrome command line, without actually launching Chrome."""
    fake_binary = "/usr/bin/true"  # exists on macOS — satisfies os.path.exists()
    monkeypatch.setattr(config, "_sys", mock.MagicMock(platform="darwin"))
    monkeypatch.setattr(config, "CHROME_BINARY", fake_binary)
    monkeypatch.setattr(config, "CDP_USER_DATA_DIR", tmp_path / "chrome-test")

    with mock.patch.object(config, "_subprocess") as subp, \
         mock.patch.object(config, "_probe_cdp_port", return_value=True):
        subp.Popen.return_value = mock.MagicMock()
        assert config._launch_persistent_chrome() is True

        subp.Popen.assert_called_once()
        args, kwargs = subp.Popen.call_args
        cmd = args[0]
        assert cmd[0] == fake_binary
        assert f"--remote-debugging-port={config.CDP_PRIMARY_PORT}" in cmd
        assert any(arg.startswith("--user-data-dir=") for arg in cmd)
        assert any("chrome-test" in arg for arg in cmd)
        assert kwargs.get("start_new_session") is True  # detached lifecycle


def test_launch_persistent_chrome_timeout_returns_false(no_env, monkeypatch, tmp_path):
    """Chrome spawns but never answers /json/version -> False after timeout."""
    monkeypatch.setattr(config, "_sys", mock.MagicMock(platform="darwin"))
    monkeypatch.setattr(config, "CHROME_BINARY", "/usr/bin/true")
    monkeypatch.setattr(config, "CDP_USER_DATA_DIR", tmp_path / "chrome-test")
    monkeypatch.setattr(config, "CDP_LAUNCH_TIMEOUT", 0.05)  # fast-fail for tests

    with mock.patch.object(config, "_subprocess") as subp, \
         mock.patch.object(config, "_probe_cdp_port", return_value=False):
        subp.Popen.return_value = mock.MagicMock()
        assert config._launch_persistent_chrome() is False


def test_launch_persistent_chrome_skips_on_non_darwin(no_env, monkeypatch):
    monkeypatch.setattr(config, "_sys", mock.MagicMock(platform="linux"))
    with mock.patch.object(config, "_subprocess") as subp:
        assert config._launch_persistent_chrome() is False
        subp.Popen.assert_not_called()


def test_launch_persistent_chrome_skips_when_binary_missing(no_env, monkeypatch):
    monkeypatch.setattr(config, "_sys", mock.MagicMock(platform="darwin"))
    monkeypatch.setattr(config, "CHROME_BINARY", "/nonexistent/Chrome.app/.../Chrome")
    with mock.patch.object(config, "_subprocess") as subp:
        assert config._launch_persistent_chrome() is False
        subp.Popen.assert_not_called()


# ── Contract: old CDP_URL constant is gone ────────────────────────────


def test_module_no_longer_exports_cdp_url_constant():
    """Catches accidental re-introduction of the import-time snapshot."""
    assert not hasattr(config, "CDP_URL"), (
        "config.CDP_URL was the import-time snapshot that caused the "
        "re-login bug. Use config.get_cdp_url() instead."
    )
