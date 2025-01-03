import pytest

import os
from pathlib import Path

@pytest.fixture
def code_path():
    return Path(os.environ["CODE"])

@pytest.fixture
def data_path():
    return Path(os.environ["DATA"])
