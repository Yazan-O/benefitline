"""Pytest configuration for the evals package: marker registration only."""


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits Bedrock; needs .env credentials and model access")
