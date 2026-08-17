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

    @patch("claude_code_tools.msg.prompt_detect.subprocess.run")
    def test_capture_targets_registered_tmux_socket(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "› \n"

        result = detect_prompt_state("%2", "codex", "/tmp/tmux-b")

        assert result == PromptState.EMPTY
        assert run.call_args.args[0][:3] == ["tmux", "-S", "/tmp/tmux-b"]


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
    def test_codex_dim_placeholder_is_an_empty_prompt(self, mock_capture):
        mock_capture.return_value = [
            "\x1b[1m›\x1b[0m \x1b[2mFind and fix a bug in @filename\x1b[0m",
        ]

        result = detect_prompt_state("test:1.2", "codex")

        assert result == PromptState.EMPTY

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_claude_dim_placeholder_is_an_empty_prompt(self, mock_capture):
        mock_capture.return_value = [
            "\x1b[39m❯\xa0\x1b[2mTry asking about this repository\x1b[0m",
        ]

        result = detect_prompt_state("test:1.1", "claude")

        assert result == PromptState.EMPTY

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_typed_text_before_dim_suggestion_is_not_empty(self, mock_capture):
        mock_capture.return_value = [
            "\x1b[1m›\x1b[0m fix auth\x1b[2m with a token refresh\x1b[0m",
        ]

        result = detect_prompt_state("test:1.2", "codex")

        assert result == PromptState.HAS_TEXT

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_dim_prompt_glyph_does_not_hide_typed_text(self, mock_capture):
        mock_capture.return_value = [
            "\x1b[2m› typed input\x1b[0m",
        ]

        result = detect_prompt_state("test:1.2", "codex")

        assert result == PromptState.HAS_TEXT

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_reset_after_dim_does_not_hide_typed_text(self, mock_capture):
        for reset in ("22", "0"):
            mock_capture.return_value = [
                f"\x1b[1m›\x1b[0m \x1b[2;{reset}mtyped input\x1b[0m",
            ]

            result = detect_prompt_state("test:1.2", "codex")

            assert result == PromptState.HAS_TEXT

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_dim_after_reset_is_still_a_placeholder(self, mock_capture):
        mock_capture.return_value = [
            "\x1b[1m›\x1b[0m \x1b[22;2mplaceholder text\x1b[0m",
        ]

        result = detect_prompt_state("test:1.2", "codex")

        assert result == PromptState.EMPTY

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_indexed_color_two_is_not_dim(self, mock_capture):
        mock_capture.return_value = [
            "\x1b[1m›\x1b[0m \x1b[38;5;2mtyped input\x1b[0m",
        ]

        result = detect_prompt_state("test:1.2", "codex")

        assert result == PromptState.HAS_TEXT

    @patch(
        "claude_code_tools.msg.prompt_detect"
        "._capture_last_lines",
    )
    def test_rgb_color_mode_two_is_not_dim(self, mock_capture):
        mock_capture.return_value = [
            "\x1b[1m›\x1b[0m \x1b[38;2;2;40;80mtyped input\x1b[0m",
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
