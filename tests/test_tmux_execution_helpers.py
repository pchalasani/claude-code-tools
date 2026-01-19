"""Tests for tmux execution helpers."""
import pytest
from claude_code_tools.tmux_execution_helpers import (
    generate_execution_markers,
    wrap_command_with_markers,
    parse_marked_output,
    find_markers_in_output,
)


class TestGenerateExecutionMarkers:
    """Tests for generate_execution_markers function."""

    def test_returns_tuple_of_two_strings(self):
        """Returns tuple with start and end markers."""
        start, end = generate_execution_markers()

        assert isinstance(start, str)
        assert isinstance(end, str)
        assert start != end

    def test_markers_are_unique_across_calls(self):
        """Each call generates unique markers."""
        start1, end1 = generate_execution_markers()
        start2, end2 = generate_execution_markers()

        assert start1 != start2
        assert end1 != end2

    def test_markers_have_expected_prefix(self):
        """Markers have recognizable prefix pattern."""
        start, end = generate_execution_markers()

        assert start.startswith("__TMUX_EXEC_START_")
        assert end.startswith("__TMUX_EXEC_END_")


class TestWrapCommandWithMarkers:
    """Tests for wrap_command_with_markers function."""

    def test_wraps_simple_command(self):
        """Wraps a simple command with markers."""
        result = wrap_command_with_markers(
            "ls -la",
            "__START__",
            "__END__"
        )

        assert "echo __START__" in result
        assert "ls -la" in result
        assert "echo __END__:$?" in result

    def test_wrapped_command_structure(self):
        """Wrapped command has correct structure."""
        result = wrap_command_with_markers(
            "pwd",
            "__START__",
            "__END__"
        )

        # Should have: echo start; { command; } 2>&1; echo end:$?
        assert result.startswith("echo __START__")
        assert "{ pwd; }" in result
        assert "2>&1" in result
        assert result.endswith("echo __END__:$?")

    def test_preserves_command_with_special_characters(self):
        """Commands with special characters are preserved."""
        result = wrap_command_with_markers(
            "echo 'hello world' && ls",
            "__START__",
            "__END__"
        )

        assert "echo 'hello world' && ls" in result


class TestParseMarkedOutput:
    """Tests for parse_marked_output function."""

    def test_parses_successful_command(self):
        """Parses output from successful command."""
        captured = """__START__
hello world
__END__:0"""

        result = parse_marked_output(captured, "__START__", "__END__")

        assert result["output"] == "hello world"
        assert result["exit_code"] == 0

    def test_parses_failed_command(self):
        """Parses output from failed command."""
        captured = """__START__
ls: cannot access '/nonexistent': No such file or directory
__END__:2"""

        result = parse_marked_output(captured, "__START__", "__END__")

        assert "No such file or directory" in result["output"]
        assert result["exit_code"] == 2

    def test_handles_multiline_output(self):
        """Handles command output with multiple lines."""
        captured = """__START__
line 1
line 2
line 3
__END__:0"""

        result = parse_marked_output(captured, "__START__", "__END__")

        assert result["output"] == "line 1\nline 2\nline 3"
        assert result["exit_code"] == 0

    def test_handles_empty_output(self):
        """Handles command with no output."""
        captured = """__START__
__END__:0"""

        result = parse_marked_output(captured, "__START__", "__END__")

        assert result["output"] == ""
        assert result["exit_code"] == 0

    def test_returns_error_when_markers_not_found(self):
        """Returns exit_code=-1 when markers not found (timeout)."""
        captured = "some output without markers"

        result = parse_marked_output(captured, "__START__", "__END__")

        assert result["exit_code"] == -1
        assert "output" in result

    def test_handles_output_containing_marker_like_strings(self):
        """Handles case where command output contains marker-like text."""
        # Should use the actual marker boundaries, not marker-like strings in output
        captured = """prefix __START__
__START__
real output with __START__ in it
__END__:0"""

        result = parse_marked_output(captured, "__START__", "__END__")

        # Should extract from first __START__ to last __END__
        assert "__START__" in result["output"]
        assert result["exit_code"] == 0


class TestFindMarkersInOutput:
    """Tests for find_markers_in_output function."""

    def test_finds_both_markers(self):
        """Detects when both markers are present."""
        captured = """__START__
some output
__END__:0"""

        result = find_markers_in_output(captured, "__START__", "__END__")

        assert result["has_start"] is True
        assert result["has_end"] is True

    def test_finds_only_end_marker(self):
        """Detects when only end marker is present (start scrolled off)."""
        captured = """some output that scrolled
more output
__END__:0"""

        result = find_markers_in_output(captured, "__START__", "__END__")

        assert result["has_start"] is False
        assert result["has_end"] is True

    def test_finds_only_start_marker(self):
        """Detects when only start marker is present (command still running)."""
        captured = """__START__
command output in progress..."""

        result = find_markers_in_output(captured, "__START__", "__END__")

        assert result["has_start"] is True
        assert result["has_end"] is False

    def test_finds_neither_marker(self):
        """Detects when neither marker is present."""
        captured = "some unrelated output"

        result = find_markers_in_output(captured, "__START__", "__END__")

        assert result["has_start"] is False
        assert result["has_end"] is False

    def test_requires_colon_suffix_for_end_marker(self):
        """End marker detection requires the :exit_code suffix."""
        # End marker without colon should not be detected
        captured = """__START__
output
__END__"""

        result = find_markers_in_output(captured, "__START__", "__END__")

        assert result["has_start"] is True
        assert result["has_end"] is False  # No colon suffix

    def test_detects_end_marker_with_various_exit_codes(self):
        """End marker is detected regardless of exit code value."""
        for exit_code in [0, 1, 127, 255]:
            captured = f"__END__:{exit_code}"
            result = find_markers_in_output(captured, "__START__", "__END__")
            assert result["has_end"] is True
