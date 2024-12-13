import pytest

import os
from pathlib import Path

@pytest.fixture
def data_path():
    return Path(os.environ["DATA"])
