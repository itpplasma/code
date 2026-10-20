import os
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--data-path", help="Shared DATA directory (overrides $DATA)")
    parser.addoption("--code-path", help="CODE workspace root (overrides $CODE)")
    parser.addoption(
        "--regenerate-golden",
        action="store_true",
        default=False,
        help="Regenerate golden records instead of only verifying them",
    )


@pytest.fixture
def code_path(request):
    value = request.config.getoption("--code-path") or os.environ.get("CODE")
    return Path(value).resolve() if value else Path(__file__).resolve().parents[2]


@pytest.fixture
def data_path(request):
    value = request.config.getoption("--data-path") or os.environ.get("DATA")
    if value:
        return Path(value).resolve()
    local = Path(__file__).resolve().parents[1] / ".testdata"
    if local.is_dir():
        return local
    pytest.skip("DATA not available: set --data-path, $DATA or run scripts/fetch_data.sh")


@pytest.fixture
def require_data(data_path):
    """Skip only the requesting test when its reference files are missing."""
    def _require_data(*relpaths):
        missing = [str(rel) for rel in relpaths if not (data_path / rel).exists()]
        if missing:
            pytest.skip(
                "DATA not available: missing " + ", ".join(missing)
                + "; run scripts/fetch_data.sh"
            )
        return [data_path / rel for rel in relpaths]

    return _require_data
