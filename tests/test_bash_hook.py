"""Tests for bash_hook.py - Pre-tool hook that blocks dangerous bash commands."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
from bash_hook import main, _get_check_functions


class TestGetCheckFunctions:
    """Tests for _get_check_functions lazy loading."""

    def test_returns_list_of_functions(self):
        """Should return a list of callable check functions."""
        checks = _get_check_functions()

        assert isinstance(checks, list)
        assert len(checks) == 5
        assert all(callable(func) for func in checks)

    def test_caches_check_functions(self):
        """Should return the same cached list on subsequent calls."""
        # Clear cache first
        import bash_hook
        bash_hook._check_functions = None

        first_call = _get_check_functions()
        second_call = _get_check_functions()

        # Should return the exact same object (cached)
        assert first_call is second_call

    def test_check_functions_have_correct_signature(self):
        """Each check function should accept a command and return (bool, str|None)."""
        checks = _get_check_functions()

        # Test with a safe command
        for check_func in checks:
            result = check_func("echo hello")
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert isinstance(result[0], bool)
            # Second element should be str or None
            assert result[1] is None or isinstance(result[1], str)


class TestMainNonBashTools:
    """Tests for non-Bash tool handling."""

    def test_non_bash_tool_approved_immediately(self, mock_stdin, capsys):
        """Non-Bash tools should be approved without any checks."""
        input_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file.txt"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"
        assert "reason" not in result

    @pytest.mark.parametrize("tool_name", [
        "Edit", "Write", "Glob", "Grep", "WebFetch", "NotebookEdit"
    ])
    def test_various_non_bash_tools_approved(self, mock_stdin, capsys, tool_name):
        """Various non-Bash tools should all be approved."""
        input_data = {
            "tool_name": tool_name,
            "tool_input": {"some_param": "value"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"


class TestMainSafeBashCommands:
    """Tests for safe Bash commands that should be approved."""

    @pytest.mark.parametrize("safe_command", [
        "ls -la",
        "pwd",
        "echo 'hello world'",
        "cat file.txt",
        "grep 'pattern' file.txt",
        "git status",
        "git diff",
        "git log",
        "npm test",
        "python script.py",
        "mkdir -p new_directory",
        "touch new_file.txt",
    ])
    def test_safe_commands_approved(self, mock_stdin, capsys, safe_command):
        """Safe commands should be approved."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": safe_command}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"
        assert "reason" not in result

    def test_empty_command_approved(self, mock_stdin, capsys):
        """Empty command should be approved."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": ""}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_whitespace_only_command_approved(self, mock_stdin, capsys):
        """Whitespace-only command should be approved."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "   \n\t   "}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"


class TestMainBlockedCommands:
    """Tests for dangerous commands that should be blocked."""

    def test_rm_command_blocked(self, mock_stdin, capsys):
        """rm command should be blocked."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "rm file.txt"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "block"
        assert "reason" in result
        assert "TRASH" in result["reason"]
        assert "mv" in result["reason"]

    @pytest.mark.parametrize("rm_command", [
        "rm -rf directory",
        "rm -f file.txt",
        "/bin/rm file.txt",
        "/usr/bin/rm -rf /tmp/data",
        "sudo rm -rf /var/log/*",
    ])
    def test_various_rm_commands_blocked(self, mock_stdin, capsys, rm_command):
        """Various forms of rm should be blocked."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": rm_command}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "block"

    def test_git_add_wildcard_blocked(self, mock_stdin, capsys):
        """git add with wildcard should be blocked."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git add *"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "block"
        assert "reason" in result
        assert "Wildcard" in result["reason"] or "wildcard" in result["reason"].lower()

    @pytest.mark.parametrize("git_command", [
        "git add *.py",
        "git add -A",
        "git add --all",
        "git add .",
        "git add ../",
    ])
    def test_various_git_add_patterns_blocked(self, mock_stdin, capsys, git_command):
        """Various dangerous git add patterns should be blocked."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": git_command}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "block"
        assert "reason" in result


class TestMainMultipleBlockingReasons:
    """Tests for commands that trigger multiple blocking checks."""

    def test_single_blocking_reason(self, mock_stdin, capsys):
        """Single blocking reason should be returned directly."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "rm file.txt"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "block"
        assert "reason" in result
        # Should not have "Multiple safety checks failed" prefix
        assert "Multiple safety checks failed" not in result["reason"]

    def test_multiple_blocking_reasons_combined(self, mock_stdin, capsys):
        """Multiple blocking reasons should be combined with numbering."""
        # Create a command that might trigger multiple checks
        # We need to mock the check functions to return multiple blocks

        mock_checks = [
            lambda cmd: (True, "First blocking reason"),
            lambda cmd: (True, "Second blocking reason"),
            lambda cmd: (False, None),
        ]

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "dangerous command"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with patch("bash_hook._get_check_functions", return_value=mock_checks):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "block"
        assert "Multiple safety checks failed" in result["reason"]
        assert "1. First blocking reason" in result["reason"]
        assert "2. Second blocking reason" in result["reason"]

    def test_all_checks_block_combines_all_reasons(self, mock_stdin, capsys):
        """When all checks block, all reasons should be included."""
        mock_checks = [
            lambda cmd: (True, "Check 1 failed"),
            lambda cmd: (True, "Check 2 failed"),
            lambda cmd: (True, "Check 3 failed"),
        ]

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "very dangerous"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with patch("bash_hook._get_check_functions", return_value=mock_checks):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "block"
        assert "Check 1 failed" in result["reason"]
        assert "Check 2 failed" in result["reason"]
        assert "Check 3 failed" in result["reason"]


class TestMainEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_missing_tool_input(self, mock_stdin, capsys):
        """Should handle missing tool_input gracefully."""
        input_data = {
            "tool_name": "Bash"
            # No tool_input
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        # Should approve empty command
        assert result["decision"] == "approve"

    def test_missing_command_in_tool_input(self, mock_stdin, capsys):
        """Should handle missing command field gracefully."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {}
            # No command field
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_command_with_unicode_characters(self, mock_stdin, capsys):
        """Should handle Unicode characters in commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo '你好世界 🌍'"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_very_long_command(self, mock_stdin, capsys):
        """Should handle very long commands."""
        long_command = "echo " + "x" * 10000
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": long_command}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_check_function_exception_doesnt_crash(self, mock_stdin, capsys):
        """If a check function raises an exception, hook should not crash."""
        def failing_check(cmd):
            raise RuntimeError("Check function error")

        mock_checks = [
            failing_check,
            lambda cmd: (False, None),
        ]

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with patch("bash_hook._get_check_functions", return_value=mock_checks):
                # Should raise RuntimeError, not SystemExit
                with pytest.raises(RuntimeError, match="Check function error"):
                    main()


class TestMainJSONOutput:
    """Tests for JSON output format."""

    def test_approve_output_format(self, mock_stdin, capsys):
        """Approve decision should have correct JSON format."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()

        # Should be valid JSON
        result = json.loads(captured.out)
        assert "decision" in result
        assert result["decision"] == "approve"
        # Should only have decision field for approve
        assert len(result) == 1

    def test_block_output_format(self, mock_stdin, capsys):
        """Block decision should have correct JSON format with reason."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "rm file.txt"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()

        # Should be valid JSON
        result = json.loads(captured.out)
        assert "decision" in result
        assert result["decision"] == "block"
        assert "reason" in result
        assert isinstance(result["reason"], str)
        assert len(result["reason"]) > 0

    def test_unicode_in_reason_preserved(self, mock_stdin, capsys):
        """Unicode characters in reason should be preserved."""
        mock_checks = [
            lambda cmd: (True, "Блокировано! 🚫"),
        ]

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "test"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with patch("bash_hook._get_check_functions", return_value=mock_checks):
                with pytest.raises(SystemExit):
                    main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert "Блокировано! 🚫" in result["reason"]


class TestIntegrationWithRealChecks:
    """Integration tests with real check functions (not mocked)."""

    def test_real_rm_check_blocks(self, mock_stdin, capsys):
        """Integration test: real rm check should block rm commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /tmp/test"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "block"
        assert "mv" in result["reason"]
        assert "TRASH" in result["reason"]

    def test_real_git_add_check_blocks_wildcard(self, mock_stdin, capsys):
        """Integration test: real git add check should block wildcards."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git add *.txt"}
        }

        with patch("sys.stdin", mock_stdin(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "block"
        assert "wildcard" in result["reason"].lower() or "Wildcard" in result["reason"]

    def test_real_checks_approve_safe_commands(self, mock_stdin, capsys):
        """Integration test: real checks should approve safe commands."""
        safe_commands = [
            "git status",
            "ls -la",
            "cat file.txt",
            "npm test",
            "python script.py",
        ]

        for cmd in safe_commands:
            input_data = {
                "tool_name": "Bash",
                "tool_input": {"command": cmd}
            }

            with patch("sys.stdin", mock_stdin(input_data)):
                with pytest.raises(SystemExit) as exc_info:
                    main()

            assert exc_info.value.code == 0
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result["decision"] == "approve", f"Command '{cmd}' should be approved"
