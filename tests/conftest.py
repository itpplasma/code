import os
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--data-path",
        action="store",
        default=None,
        help="Path to the shared data directory (defaults to the DATA environment variable)",
    )
    parser.addoption(
        "--code-path",
        action="store",
        default=None,
        help="Path to the CODE workspace root (defaults to the CODE environment variable)",
    )


def _resolve_path(option_value, env_var, option_name):
    if option_value:
        return Path(option_value)
    if env_var in os.environ:
        return Path(os.environ[env_var])
    raise pytest.UsageError(
        f"Neither --{option_name} nor the {env_var} environment variable is set. "
        f"Pass --{option_name} on the command line or export {env_var}."
    )


@pytest.fixture
def code_path(request):
    return _resolve_path(request.config.getoption("--code-path"), "CODE", "code-path")


@pytest.fixture
def data_path(request):
    return _resolve_path(request.config.getoption("--data-path"), "DATA", "data-path")
