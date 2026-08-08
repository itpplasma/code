import os
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--regenerate-golden",
        action="store_true",
        default=False,
        help="Regenerate (overwrite) golden records instead of only verifying them",
    )


@pytest.fixture
def code_path():
    # Default to the workspace/repository root when $CODE is unset (e.g. CI).
    root = Path(os.environ.get("CODE", Path(__file__).resolve().parents[1]))
    return Path(root)


@pytest.fixture
def data_path():
    """Return the shared data root, skipping when no DATA is available.

    Resolves $DATA, else a local .testdata directory next to the repository
    (populated by scripts/fetch_data.sh), else skips.
    """
    data = os.environ.get("DATA")
    if data:
        return Path(data)
    local = Path(__file__).resolve().parents[1] / ".testdata"
    if local.exists():
        return local
    pytest.skip("DATA not available: set $DATA or run scripts/fetch_data.sh")


@pytest.fixture
def require_data(data_path):
    """Return a callable asserting the given relative paths exist in DATA.

    Skips the requesting test when any path is missing so a partial fetch
    only skips the affected tests instead of erroring in fixture setup.
    """

    def _require_data(*relpaths):
        missing = []
        for rel in relpaths:
            if not (data_path / rel).exists():
                missing.append(rel)
        if missing:
            pytest.skip(
                "DATA not available: missing "
                + ", ".join(missing)
                + "; run scripts/fetch_data.sh"
            )
        return [data_path / rel for rel in relpaths]

    return _require_data
