"""Defensive text and timestamp formatting for the workflow CLI."""

from __future__ import annotations

import sys
import unicodedata
from datetime import datetime

from claude_code_tools.workflow_runs import parse_timestamp

MAX_ERROR_CHARS = 2_000
MAX_ERROR_LINES = 20


def sanitize(value: object) -> str:
    """Return terminal-safe single-line text for a persisted value.

    Args:
        value: Value to render in human-readable output.

    Returns:
        Text with unsafe characters replaced by visible markers.
    """
    text = str(value)
    safe = "".join(_sanitize_character(character) for character in text)
    encoding = getattr(sys.stdout, "encoding", None)
    if not encoding:
        return safe
    try:
        safe.encode(encoding)
    except LookupError:
        return safe
    except UnicodeEncodeError:
        return safe.encode(encoding, errors="replace").decode(encoding)
    return safe


def _sanitize_character(character: str) -> str:
    """Return a terminal-safe representation of one Unicode character."""
    if character in {"\n", "\r"}:
        return " ⏎ "
    category = unicodedata.category(character)
    if category in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
        return "�"
    if category == "Zs" and character != " ":
        return "�"
    return character


def truncate(value: str, maximum: int) -> str:
    """Truncate text to a maximum length using an ellipsis.

    Args:
        value: Text to truncate.
        maximum: Maximum returned length.

    Returns:
        The original or truncated text.
    """
    if len(value) <= maximum:
        return value
    if maximum <= 1:
        return "…"
    return f"{value[: maximum - 1]}…"


def bounded_text(
    value: object,
    *,
    maximum: int,
    full: bool,
) -> tuple[str, bool]:
    """Sanitize and optionally bound one persisted value.

    Args:
        value: Value to render.
        maximum: Maximum number of visible characters in bounded mode.
        full: Whether to disable the size bound.

    Returns:
        The safe text and whether it was truncated.
    """
    text = str(value)
    if full:
        return sanitize(text), False
    raw_prefix = text[: maximum + 1]
    safe = sanitize(raw_prefix)
    truncated = len(text) > maximum or len(safe) > maximum
    if not truncated:
        return safe, False
    return truncate(safe, maximum), True


def bounded_error(value: str, *, full: bool) -> tuple[str, bool]:
    """Return safe error text with visible default line and character limits.

    Args:
        value: Persisted error text.
        full: Whether to disable the size limits.

    Returns:
        The rendered error and whether content was omitted.
    """
    if full:
        lines = value.splitlines() or [value]
        return "\n".join(sanitize(line) for line in lines), False

    raw_budget = MAX_ERROR_CHARS + 1
    raw_prefix = value[:raw_budget]
    lines = raw_prefix.splitlines() or [raw_prefix]
    omitted_lines = len(lines) > MAX_ERROR_LINES
    if omitted_lines:
        lines = lines[:MAX_ERROR_LINES]
    safe = "\n".join(sanitize(line) for line in lines)
    omitted_chars = len(value) > len(raw_prefix) or len(safe) > MAX_ERROR_CHARS
    if omitted_chars:
        safe = truncate(safe, MAX_ERROR_CHARS)
    truncated = omitted_lines or omitted_chars
    if truncated:
        safe += "\n… output truncated; use --full or --json for complete data."
    return safe, truncated


def now() -> datetime:
    """Return the current aware local time."""
    return datetime.now().astimezone()


def format_duration(seconds: float | None) -> str:
    """Format an optional duration as compact human-readable text.

    Args:
        seconds: Nonnegative duration in seconds, when known.

    Returns:
        A compact duration or an em dash when unavailable.
    """
    if seconds is None:
        return "—"
    rounded = max(0, int(seconds))
    days, remainder = divmod(rounded, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_age(value: str | None, current_time: datetime) -> str:
    """Format a persisted timestamp as an age.

    Args:
        value: Candidate ISO timestamp.
        current_time: Current aware time.

    Returns:
        A compact age or an em dash when unavailable.
    """
    parsed = parse_timestamp(value)
    if parsed is None:
        return "—"
    try:
        seconds = max(0.0, (current_time - parsed).total_seconds())
    except (OverflowError, ValueError):
        return "—"
    return f"{format_duration(seconds)} ago"


def format_time(value: str | None) -> str:
    """Format a persisted timestamp in the local timezone.

    Args:
        value: Candidate ISO timestamp.

    Returns:
        A local timestamp, an invalid-time diagnostic, or an em dash.
    """
    parsed = parse_timestamp(value)
    if parsed is None:
        return value or "—"
    try:
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except (OverflowError, ValueError):
        return f"{value} (invalid local time)"
