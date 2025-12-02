"""Tests for error_websearch_hook.py - Post-tool hook that detects errors."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
from error_websearch_hook import detect_errors, log_error_for_resolution, main, ERROR_PATTERNS


class TestDetectErrors:
    """Tests for detect_errors function."""

    def test_empty_output_returns_false(self):
        """Empty output should return (False, None)."""
        result = detect_errors("")
        assert result == (False, None)

    def test_none_output_returns_false(self):
        """None output should return (False, None)."""
        result = detect_errors(None)
        assert result == (False, None)

    def test_clean_output_no_errors(self):
        """Normal output without errors should return False."""
        output = "Build successful\nAll tests passed\nDeployment complete"
        has_error, error_line = detect_errors(output)
        assert has_error is False
        assert error_line is None

    @pytest.mark.parametrize("error_pattern", [
        "error: something went wrong",
        "Error: file not found",
        "ERROR in webpack",
        "FAILED test_something",
        "TypeError: undefined is not a function",
        "ModuleNotFoundError: No module named 'foo'",
        "npm ERR! code ELIFECYCLE",
        "command not found: git",
        "Permission denied",
        "fatal: not a git repository",
        "panic: runtime error",
    ])
    def test_detects_various_error_patterns(self, error_pattern):
        """Should detect various error patterns."""
        output = f"Some output\n{error_pattern}\nMore output"
        has_error, error_line = detect_errors(output)
        assert has_error is True
        assert error_line is not None

    def test_case_insensitive_detection(self):
        """Error detection should be case insensitive."""
        outputs = [
            "ERROR: something failed",
            "error: something failed",
            "Error: something failed",
        ]
        for output in outputs:
            has_error, _ = detect_errors(output)
            assert has_error is True

    def test_extracts_error_line(self):
        """Should extract the specific line containing the error."""
        output = "Starting build\nCompiling...\nerror: compilation failed\nBuild aborted"
        has_error, error_line = detect_errors(output)
        assert has_error is True
        assert "compilation failed" in error_line

    def test_truncates_long_error_lines(self):
        """Error lines should be truncated to 200 characters."""
        long_error = "error: " + "x" * 300
        output = f"Start\n{long_error}\nEnd"
        has_error, error_line = detect_errors(output)
        assert has_error is True
        assert len(error_line) <= 200

    def test_error_patterns_list_not_empty(self):
        """ERROR_PATTERNS should contain patterns."""
        assert len(ERROR_PATTERNS) > 0
        assert "error:" in ERROR_PATTERNS


class TestLogErrorForResolution:
    """Tests for log_error_for_resolution function."""

    def test_creates_log_directory(self, tmp_path, monkeypatch):
        """Should create log directory if it doesn't exist."""
        monkeypatch.setenv("HOME", str(tmp_path))

        log_error_for_resolution("test error", "npm test", "full output")

        log_dir = tmp_path / ".claude" / "logs"
        assert log_dir.exists()

    def test_writes_jsonl_entry(self, tmp_path, monkeypatch):
        """Should write valid JSONL entry to log file."""
        monkeypatch.setenv("HOME", str(tmp_path))

        log_error_for_resolution("error: test", "npm test", "full output here")

        log_file = tmp_path / ".claude" / "logs" / "pending_errors.jsonl"
        assert log_file.exists()

        with open(log_file) as f:
            logged = json.loads(f.read().strip())

        assert logged["error_line"] == "error: test"
        assert logged["command"] == "npm test"
        assert logged["status"] == "pending"

    def test_entry_contains_required_fields(self, tmp_path, monkeypatch):
        """Entry should contain all required fields."""
        monkeypatch.setenv("HOME", str(tmp_path))

        entry = log_error_for_resolution("error", "cmd", "output")

        assert "timestamp" in entry
        assert "command" in entry
        assert "error_line" in entry
        assert "full_output" in entry
        assert "status" in entry
        assert "search_suggestions" in entry

    def test_truncates_full_output(self, tmp_path, monkeypatch):
        """Full output should be truncated to 2000 characters."""
        monkeypatch.setenv("HOME", str(tmp_path))

        long_output = "x" * 5000
        entry = log_error_for_resolution("error", "cmd", long_output)

        assert len(entry["full_output"]) <= 2000

    def test_search_suggestions_format(self, tmp_path, monkeypatch):
        """Should generate helpful search suggestions."""
        monkeypatch.setenv("HOME", str(tmp_path))

        entry = log_error_for_resolution("ModuleNotFoundError", "cmd", "output")

        suggestions = entry["search_suggestions"]
        assert len(suggestions) == 3
        assert any("fix" in s for s in suggestions)
        assert any("stackoverflow" in s for s in suggestions)


class TestMain:
    """Tests for main() function - the hook entry point."""

    def test_non_bash_tool_approved(self, mock_stdin, capsys):
        """Non-Bash tools should be approved without processing."""
        input_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_bash_without_errors_approved(self, mock_stdin, capsys, tmp_path, monkeypatch):
        """Bash without errors should be approved."""
        monkeypatch.setenv("HOME", str(tmp_path))

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "tool_result": {
                "stdout": "file1.txt\nfile2.txt",
                "stderr": ""
            }
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"
        assert "metadata" not in result

    def test_bash_with_errors_includes_metadata(self, mock_stdin, capsys, tmp_path, monkeypatch):
        """Bash with errors should include error metadata."""
        monkeypatch.setenv("HOME", str(tmp_path))

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "npm test"},
            "tool_result": {
                "stdout": "",
                "stderr": "npm ERR! Test failed"
            }
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)

        assert result["decision"] == "approve"  # Doesn't block
        assert "metadata" in result
        assert result["metadata"]["error_detected"] is True
        assert "suggested_searches" in result["metadata"]

    def test_combines_stdout_and_stderr(self, mock_stdin, capsys, tmp_path, monkeypatch):
        """Should check both stdout and stderr for errors."""
        monkeypatch.setenv("HOME", str(tmp_path))

        # Error in stderr only
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "some_cmd"},
            "tool_result": {
                "stdout": "Normal output",
                "stderr": "error: something failed"
            }
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result.get("metadata", {}).get("error_detected") is True

    def test_handles_missing_tool_result(self, mock_stdin, capsys):
        """Should handle missing tool_result gracefully."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"}
            # No tool_result
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"
