"""Tests for prompt-empty detection."""

from unittest.mock import patch

from claude_code_tools.msg.prompt_detect import (
    PromptState,
    detect_prompt_state,
)


def _mock_capture(lines: list[str]):
    """Create a mock for _capture_last_lines."""
    def _capture(pane_target, count=5):
        return lines
    return _capture


class TestPromptDetection:

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_claude_empty_prompt(self, mock_capture):
        mock_capture.return_value = [
            "Some output above",
            "─" * 40,
            "❯ ",
            "─" * 40,
        ]
        result = detect_prompt_state("test:1.1", "claude")
        assert result == PromptState.EMPTY

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_claude_prompt_with_text(self, mock_capture):
        mock_capture.return_value = [
            "Some output above",
            "─" * 40,
            "❯ some user input here",
            "─" * 40,
        ]
        result = detect_prompt_state("test:1.1", "claude")
        assert result == PromptState.HAS_TEXT

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_codex_empty_prompt(self, mock_capture):
        mock_capture.return_value = [
            "  gpt-5.4 · 98% left",
            "",
            "› ",
        ]
        result = detect_prompt_state("test:1.2", "codex")
        assert result == PromptState.EMPTY

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_codex_prompt_with_text(self, mock_capture):
        mock_capture.return_value = [
            "  gpt-5.4 · 98% left",
            "",
            "› fix the auth bug",
        ]
        result = detect_prompt_state("test:1.2", "codex")
        assert result == PromptState.HAS_TEXT

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_no_prompt_found(self, mock_capture):
        mock_capture.return_value = [
            "Building project...",
            "Compiling src/main.rs",
            "Running tests...",
        ]
        result = detect_prompt_state("test:1.1", "claude")
        assert result == PromptState.UNKNOWN

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_empty_capture(self, mock_capture):
        mock_capture.return_value = []
        result = detect_prompt_state("test:1.1", "claude")
        assert result == PromptState.UNKNOWN

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_unknown_agent_kind(self, mock_capture):
        mock_capture.return_value = ["❯ "]
        result = detect_prompt_state(
            "test:1.1", "unknown_agent",
        )
        assert result == PromptState.UNKNOWN

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_bare_chevron_prompt(self, mock_capture):
        """Test with > prompt (fallback pattern)."""
        mock_capture.return_value = [
            "output",
            "> ",
        ]
        result = detect_prompt_state("test:1.1", "claude")
        assert result == PromptState.EMPTY
