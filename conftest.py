import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "invariant: invariants that must never regress",
    )
