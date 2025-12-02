"""Tests for todo_sequential_hook.py - Hook that suggests sequential thinking for complex todos."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
from todo_sequential_hook import analyze_todos, main


class TestAnalyzeTodos:
    """Tests for analyze_todos function."""

    def test_empty_todos_returns_none(self):
        """Empty todo list should return None."""
        assert analyze_todos([]) is None
        assert analyze_todos(None) is None

    def test_simple_todos_returns_none(self):
        """Simple todos (< 5 pending, no complex keywords) should return None."""
        todos = [
            {"content": "Fix typo", "status": "pending"},
            {"content": "Update README", "status": "pending"},
            {"content": "Add tests", "status": "completed"},
        ]
        result = analyze_todos(todos)
        assert result is None

    def test_many_pending_todos_triggers_suggestion(self):
        """More than 5 pending todos should trigger suggestion."""
        todos = [
            {"content": "Task 1", "status": "pending"},
            {"content": "Task 2", "status": "pending"},
            {"content": "Task 3", "status": "pending"},
            {"content": "Task 4", "status": "pending"},
            {"content": "Task 5", "status": "pending"},
            {"content": "Task 6", "status": "pending"},
        ]
        result = analyze_todos(todos)
        assert result is not None
        assert "suggestion" in result

    def test_complex_keyword_implement_triggers_suggestion(self):
        """'implement' keyword should trigger suggestion."""
        todos = [
            {"content": "Implement new feature", "status": "pending"},
        ]
        result = analyze_todos(todos)
        assert result is not None
        assert "Implement new feature" in result.get("complex_tasks", [])

    def test_complex_keyword_refactor_triggers_suggestion(self):
        """'refactor' keyword should trigger suggestion."""
        todos = [
            {"content": "Refactor authentication module", "status": "pending"},
        ]
        result = analyze_todos(todos)
        assert result is not None

    def test_complex_keyword_migrate_triggers_suggestion(self):
        """'migrate' keyword should trigger suggestion."""
        todos = [
            {"content": "Migrate database schema", "status": "pending"},
        ]
        result = analyze_todos(todos)
        assert result is not None

    def test_complex_keyword_integrate_triggers_suggestion(self):
        """'integrate' keyword should trigger suggestion."""
        todos = [
            {"content": "Integrate third-party API", "status": "pending"},
        ]
        result = analyze_todos(todos)
        assert result is not None

    def test_complex_keyword_architect_triggers_suggestion(self):
        """'architect' keyword should trigger suggestion."""
        todos = [
            {"content": "Architect new service layer", "status": "pending"},
        ]
        result = analyze_todos(todos)
        assert result is not None

    def test_complex_keyword_security_triggers_suggestion(self):
        """'security' keyword should trigger suggestion."""
        todos = [
            {"content": "Security audit required", "status": "pending"},
        ]
        result = analyze_todos(todos)
        assert result is not None

    def test_keyword_detection_case_insensitive(self):
        """Complex keyword detection should be case insensitive."""
        todos = [
            {"content": "IMPLEMENT Feature", "status": "pending"},
        ]
        result = analyze_todos(todos)
        assert result is not None

    def test_only_counts_pending_todos(self):
        """Should only count pending todos, not completed ones."""
        todos = [
            {"content": "Task 1", "status": "completed"},
            {"content": "Task 2", "status": "completed"},
            {"content": "Task 3", "status": "completed"},
            {"content": "Task 4", "status": "completed"},
            {"content": "Task 5", "status": "completed"},
            {"content": "Task 6", "status": "completed"},
            {"content": "Only pending", "status": "pending"},
        ]
        result = analyze_todos(todos)
        # 6 completed + 1 pending = should NOT trigger (only 1 pending)
        assert result is None

    def test_suggestion_contains_recommendation(self):
        """Suggestion should include a recommendation."""
        todos = [
            {"content": "Implement complex feature", "status": "pending"},
        ]
        result = analyze_todos(todos)
        assert "recommendation" in result
        assert "/smart-plan" in result["recommendation"] or "task-planner" in result["recommendation"]

    def test_limits_complex_tasks_to_three(self):
        """Complex tasks list should be limited to top 3."""
        todos = [
            {"content": "Implement feature 1", "status": "pending"},
            {"content": "Refactor module 1", "status": "pending"},
            {"content": "Migrate database 1", "status": "pending"},
            {"content": "Design system 1", "status": "pending"},
            {"content": "Optimize queries", "status": "pending"},
        ]
        result = analyze_todos(todos)
        assert len(result.get("complex_tasks", [])) <= 3

    def test_handles_missing_content_field(self):
        """Should handle todos missing 'content' field gracefully."""
        todos = [
            {"status": "pending"},  # No content
            {"content": "Valid todo", "status": "pending"},
        ]
        # Should not raise an exception
        result = analyze_todos(todos)
        # Only 2 pending, no complex keywords in "Valid todo"
        assert result is None

    def test_handles_missing_status_field(self):
        """Should handle todos missing 'status' field gracefully."""
        todos = [
            {"content": "No status"},  # No status field
            {"content": "Has status", "status": "pending"},
        ]
        # Should not raise an exception
        result = analyze_todos(todos)
        assert result is None


class TestMain:
    """Tests for main() function - the hook entry point."""

    def test_non_todowrite_tool_approved(self, mock_stdin, capsys):
        """Non-TodoWrite tools should be approved without processing."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_todowrite_always_approved(self, mock_stdin, capsys, tmp_path, monkeypatch):
        """TodoWrite should always be approved (advisory only)."""
        monkeypatch.setenv("HOME", str(tmp_path))

        input_data = {
            "tool_name": "TodoWrite",
            "tool_input": {
                "todos": [
                    {"content": "Implement complex feature", "status": "pending"},
                    {"content": "Refactor everything", "status": "pending"},
                ]
            }
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_todowrite_creates_log_when_complex(self, mock_stdin, capsys, tmp_path, monkeypatch):
        """Should create log file when complex todos detected."""
        monkeypatch.setenv("HOME", str(tmp_path))

        input_data = {
            "tool_name": "TodoWrite",
            "tool_input": {
                "todos": [
                    {"content": "Implement complex feature", "status": "pending"},
                ]
            }
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit):
                main()

        log_file = tmp_path / ".claude" / "logs" / "todo_suggestions.log"
        assert log_file.exists()

        with open(log_file) as f:
            logged = json.loads(f.read().strip())

        assert "analysis" in logged
        assert "todo_count" in logged

    def test_todowrite_no_log_when_simple(self, mock_stdin, capsys, tmp_path, monkeypatch):
        """Should not create log for simple todos."""
        monkeypatch.setenv("HOME", str(tmp_path))

        input_data = {
            "tool_name": "TodoWrite",
            "tool_input": {
                "todos": [
                    {"content": "Fix typo", "status": "pending"},
                ]
            }
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit):
                main()

        log_file = tmp_path / ".claude" / "logs" / "todo_suggestions.log"
        # Log file should not exist or be empty for simple todos
        if log_file.exists():
            assert log_file.read_text().strip() == ""

    def test_handles_empty_todos_list(self, mock_stdin, capsys):
        """Should handle empty todos list gracefully."""
        input_data = {
            "tool_name": "TodoWrite",
            "tool_input": {
                "todos": []
            }
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_handles_missing_todos_key(self, mock_stdin, capsys):
        """Should handle missing 'todos' key gracefully."""
        input_data = {
            "tool_name": "TodoWrite",
            "tool_input": {}  # No 'todos' key
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"


class TestComplexKeywords:
    """Tests specifically for complex keyword detection."""

    @pytest.mark.parametrize("keyword", [
        "implement",
        "refactor",
        "migrate",
        "integrate",
        "design",
        "architect",
        "optimize",
        "security",
    ])
    def test_all_complex_keywords_detected(self, keyword):
        """All defined complex keywords should be detected."""
        todos = [
            {"content": f"Need to {keyword} the module", "status": "pending"},
        ]
        result = analyze_todos(todos)
        assert result is not None, f"Keyword '{keyword}' should trigger suggestion"

    def test_partial_keyword_match(self):
        """Keywords should match even as part of larger words."""
        todos = [
            {"content": "Implementation plan needed", "status": "pending"},
        ]
        result = analyze_todos(todos)
        # "implement" is in "Implementation"
        assert result is not None

    def test_completed_complex_todos_not_counted(self):
        """Completed complex todos should not trigger suggestion."""
        todos = [
            {"content": "Implement feature", "status": "completed"},
            {"content": "Refactor module", "status": "completed"},
        ]
        result = analyze_todos(todos)
        assert result is None
