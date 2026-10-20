"""Exercise the shared fixtures in isolated pytest runs without private data."""
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@pytest.fixture
def fixture_project(pytester, monkeypatch):
    monkeypatch.delenv("CODE", raising=False)
    monkeypatch.delenv("DATA", raising=False)
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    return pytester


@pytest.mark.parametrize("source", ["cli", "environment", "default"])
def test_path_precedence(fixture_project, monkeypatch, source, request):
    project = fixture_project
    args = []
    if source == "cli":
        monkeypatch.setenv("CODE", "/unused-code")
        monkeypatch.setenv("DATA", "/unused-data")
        code = project.path / "cli-code"
        data = project.path / "cli-data"
        args = ["--code-path", str(code), "--data-path", str(data)]
    elif source == "environment":
        code = project.path / "env-code"
        data = project.path / "env-data"
        monkeypatch.setenv("CODE", str(code))
        monkeypatch.setenv("DATA", str(data))
    else:
        code = project.path.parents[1]
        data = project.path.parent / ".testdata"
        data.mkdir()
        request.addfinalizer(data.rmdir)
    project.makepyfile(f'''
from pathlib import Path

def test_roots(code_path, data_path):
    assert code_path == Path({str(code)!r})
    assert data_path == Path({str(data)!r})
''')
    project.runpytest_subprocess("-q", *args).assert_outcomes(passed=1)


def test_partial_data_skips_only_missing_file(fixture_project, monkeypatch):
    project = fixture_project
    data = project.path / "data"
    data.mkdir()
    (data / "present").write_text("reference")
    monkeypatch.setenv("DATA", str(data))
    project.makepyfile('''
def test_present(require_data):
    (path,) = require_data("present")
    assert path.read_text() == "reference"

def test_missing(require_data):
    require_data("missing")
    raise AssertionError("must skip")
''')
    project.runpytest_subprocess("-q").assert_outcomes(passed=1, skipped=1)


def test_missing_data_skips(fixture_project):
    fixture_project.makepyfile('''
def test_no_data(data_path):
    raise AssertionError("must skip")
''')
    fixture_project.runpytest_subprocess("-q").assert_outcomes(skipped=1)
