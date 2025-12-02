"""Shared pytest fixtures for hook tests."""
import json
import pytest
from io import StringIO


@pytest.fixture
def mock_stdin():
    """Factory fixture to mock stdin with JSON data."""
    def _mock_stdin(data: dict):
        return StringIO(json.dumps(data))
    return _mock_stdin


@pytest.fixture
def capture_stdout():
    """Capture stdout and return parsed JSON."""
    class StdoutCapture:
        def __init__(self):
            self.captured = StringIO()

        def get_json(self):
            self.captured.seek(0)
            return json.loads(self.captured.read())

    return StdoutCapture


@pytest.fixture
def temp_log_dir(tmp_path):
    """Create a temporary log directory."""
    log_dir = tmp_path / ".claude" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


@pytest.fixture
def mock_home_dir(tmp_path, monkeypatch):
    """Mock home directory for file operations."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path
