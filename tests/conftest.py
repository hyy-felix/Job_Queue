def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "invariant: tests that guard the core safety invariant — "
        "no unapproved candidate bullets may reach a final resume.",
    )
