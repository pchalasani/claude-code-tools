"""Detect whether a tmux pane's prompt is empty or has text.

Used by the watcher to decide if it's safe to type a
slash command into the pane, or if the user is mid-typing.
"""

from __future__ import annotations

import re
import subprocess
from enum import Enum


class PromptState(str, Enum):
    """State of an agent's input prompt."""

    EMPTY = "empty"          # Prompt visible, no user text
    HAS_TEXT = "has_text"    # User is typing something
    UNKNOWN = "unknown"      # Can't determine

# Prompt patterns: regex matching an empty prompt line.
# The key is agent_kind, value is a compiled regex.
# These match the prompt character with optional
# whitespace and nothing else after it.
PROMPT_PATTERNS: dict[str, re.Pattern] = {
    "claude": re.compile(
        r"^\s*[❯>]\s*$"
    ),
    "codex": re.compile(
        r"^\s*[›>]\s*$"
    ),
}

# Patterns for a prompt with text after it
PROMPT_WITH_TEXT_PATTERNS: dict[str, re.Pattern] = {
    "claude": re.compile(
        r"^\s*[❯>]\s+.+"
    ),
    "codex": re.compile(
        r"^\s*[›>]\s+.+"
    ),
}


def detect_prompt_state(
    pane_target: str,
    agent_kind: str = "claude",
) -> PromptState:
    """Check if a tmux pane's prompt is empty.

    Args:
        pane_target: tmux pane identifier
            (e.g., "cctools:1.4" or "%12")
        agent_kind: "claude" or "codex"

    Returns:
        PromptState indicating the prompt state.
    """
    lines = _capture_last_lines(pane_target)
    if not lines:
        return PromptState.UNKNOWN

    empty_pattern = PROMPT_PATTERNS.get(agent_kind)
    text_pattern = PROMPT_WITH_TEXT_PATTERNS.get(
        agent_kind,
    )

    if not empty_pattern:
        return PromptState.UNKNOWN

    # Scan all captured lines for prompt patterns.
    # The prompt may be surrounded by decorative lines
    # (separators, status bars, etc.) so we check all
    # lines, not just the last non-empty one.
    for line in reversed(lines):
        stripped = line.rstrip()
        if not stripped:
            continue
        if empty_pattern.match(stripped):
            return PromptState.EMPTY
        if text_pattern and text_pattern.match(stripped):
            return PromptState.HAS_TEXT

    return PromptState.UNKNOWN


def _capture_last_lines(
    pane_target: str,
    count: int = 15,
) -> list[str]:
    """Capture the last N lines from a tmux pane."""
    try:
        result = subprocess.run(
            [
                "tmux", "capture-pane",
                "-t", pane_target,
                "-p",  # print to stdout
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        all_lines = result.stdout.splitlines()
        return all_lines[-count:] if all_lines else []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
