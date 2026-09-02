"""Detect whether a tmux pane's prompt is empty or has text.

Used by the watcher to decide if it's safe to type a
slash command into the pane, or if the user is mid-typing.
"""

from __future__ import annotations

import re
import subprocess
from enum import Enum


ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x1b]*(?:\x1b\\|\x07))")


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
    tmux_socket: str | None = None,
) -> PromptState:
    """Check if a tmux pane's prompt is empty.

    Args:
        pane_target: tmux pane identifier
            (e.g., "cctools:1.4" or "%12")
        agent_kind: "claude" or "codex"

    Returns:
        PromptState indicating the prompt state.
    """
    lines = _capture_last_lines(pane_target, tmux_socket=tmux_socket)
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
        stripped = ANSI_ESCAPE.sub("", line).rstrip()
        if not stripped:
            continue
        if empty_pattern.match(stripped):
            return PromptState.EMPTY
        if text_pattern and text_pattern.match(stripped):
            if _text_starts_dim_after_prompt(line, agent_kind):
                return PromptState.EMPTY
            return PromptState.HAS_TEXT

    return PromptState.UNKNOWN


def _text_starts_dim_after_prompt(line: str, agent_kind: str) -> bool:
    """Return whether the first prompt text is explicitly dimmed."""
    prompt_chars = "❯>" if agent_kind == "claude" else "›>"
    positions = [line.find(char) for char in prompt_chars]
    positions = [position for position in positions if position >= 0]
    if not positions:
        return False

    suffix = line[min(positions) + 1:]
    dim = False
    index = 0
    while index < len(suffix):
        match = ANSI_ESCAPE.match(suffix, index)
        if match:
            sequence = match.group()
            if sequence.startswith("\x1b[") and sequence.endswith("m"):
                parameters = sequence[2:-1].split(";")
                parameter_index = 0
                while parameter_index < len(parameters):
                    parameter = parameters[parameter_index]
                    if parameter in {"38", "48", "58"}:
                        color_mode = (
                            parameters[parameter_index + 1]
                            if parameter_index + 1 < len(parameters)
                            else ""
                        )
                        if color_mode == "5":
                            parameter_index += 3
                            continue
                        if color_mode == "2":
                            parameter_index += 5
                            continue
                    if parameter in {"", "0", "22"}:
                        dim = False
                    elif parameter == "2":
                        dim = True
                    parameter_index += 1
            index = match.end()
            continue
        if suffix[index].isspace():
            index += 1
            continue
        return dim
    return False


def _capture_last_lines(
    pane_target: str,
    count: int = 15,
    tmux_socket: str | None = None,
) -> list[str]:
    """Capture the last N lines from a tmux pane."""
    try:
        cmd = ["tmux"]
        if tmux_socket:
            cmd += ["-S", tmux_socket]
        cmd += ["capture-pane", "-e", "-t", pane_target, "-p"]
        result = subprocess.run(
            cmd,
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
