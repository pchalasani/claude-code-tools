"""Noise/reminder classification for the Claude -> Codex porter.

Split out of :mod:`claude_code_tools.port_claude_to_codex` to keep
both modules under the repo's 1000-line limit. This module owns the
provenance-grounded rules deciding which parts of a Claude session
are Claude-internal noise and which are genuine user content:

* wrapper lines (command wrappers, local command output, task
  notifications) are noise only when the ENTIRE text is a sequence
  of complete, well-formed known wrapper blocks;
* teammate/agent notifications are noise only in their complete real
  shapes (banner + wrapper element closed by its MATCHING end tag,
  optionally followed by Claude's known trailing boilerplate, or a
  standalone matching-closed wrapper);
* ``[SESSION LINEAGE]`` context blocks are noise only in their
  complete aichat-generated shape (marker + known intro + known
  closing sentence);
* ``<system-reminder>`` blocks are dropped only when they occupy an
  entire string/block (the injected shape), or when a terminal block
  matches a known injected signature appended after genuine tool
  output.

Genuine prompts that merely BEGIN with a suspicious phrase or tag
(e.g. "Caveat: do not change...", an unclosed wrapper tag, quoted
reminder tags) are always preserved verbatim. All scans are linear
passes with no regexes, so hostile pasted transcripts full of
unmatched tags cannot trigger quadratic behavior.
"""

from __future__ import annotations

from typing import Any, Optional

from claude_code_tools.export_session import (
    CLAUDE_INTERNAL_WRAPPER_TAGS,
)

# Claude-internal wrapper tags: a user line whose ENTIRE text is a
# sequence of well-formed blocks with these tags is Claude-recorded
# noise (command wrappers, local command output, background-task
# notifications), never typed by the user. Grounded in real session
# shapes -- these lines always consist of nothing but complete
# wrapper blocks. The tag set itself is shared with the Claude-side
# extraction logic in :mod:`claude_code_tools.export_session` (single
# source of truth); this module adds the stricter complete-wrapper
# validation on top.
_CLAUDE_WRAPPER_TAGS = CLAUDE_INTERNAL_WRAPPER_TAGS

# Claude-internal agent/teammate notification shapes recorded as
# type=user lines even though they were not typed by the user. Real
# lines (ground truth from actual sessions) are the banner, a
# complete ``<teammate-message ...>...</teammate-message>`` (or
# ``agent-message``) element, and optionally Claude's trailing
# boilerplate paragraph starting with the known trailer prefix.
_AGENT_NOTIFICATION_BANNER = "Another Claude session sent a message:"
_AGENT_MESSAGE_TAG_NAMES = ("teammate-message", "agent-message")
_AGENT_NOTIFICATION_TRAILER_PREFIX = (
    "This came from another Claude session"
)

# Context block injected by aichat's own continue/lineage tooling
# (session_utils rollover prompts and trim_session lineage notes).
# A text is a lineage block only in the COMPLETE generated shape:
# the marker, one of the known generated intro sentences, and one of
# the known generated closing sentences. Genuine prompts that merely
# mention or quote the marker never match.
_SESSION_LINEAGE_PREFIX = "[SESSION LINEAGE]"
_SESSION_LINEAGE_INTROS = (
    "This session continues from a previous conversation",
    "This session continues from a chain of prior conversations",
    "This session was ROLLED OVER from",
    "This session was TRIMMED from",
)
_SESSION_LINEAGE_CLOSERS = (
    "based on the above session.",
    "based on the above sessions.",
    "=== END CONTEXT RECOVERY INSTRUCTIONS ===",
    "Use sub-agents to read these files if you need more context.",
)

# System-reminder tag literals (matched with linear scans, never
# regexes, so hostile pasted transcripts full of unmatched tags cannot
# trigger quadratic behavior).
_REM_OPEN = "<system-reminder>"
_REM_CLOSE = "</system-reminder>"

# Known signatures (whitespace-normalized prefixes of the reminder
# body) of the reminders Claude appends AFTER genuine output inside
# the same tool-result string. Grounded in a corpus scan of real
# sessions: the Read-tool malicious-file notice is the only shape
# observed mixed into genuine output; the others are known injected
# reminder families kept as defense in depth. A terminal reminder
# whose body matches none of these is ambiguous (it may be genuine
# file content that merely ends with the literal tag) and is
# preserved verbatim.
_INJECTED_TRAILING_SIGNATURES = (
    "Whenever you read a file, you should consider whether it",
    "Warning: the file exists but",
    "This memory is",
    "[Truncated:",
)


def _is_pure_wrapper_text(text: str) -> bool:
    """Check whether text is entirely known Claude wrapper blocks.

    This mirrors the provenance test used for system reminders: real
    Claude-recorded wrapper lines (command wrappers, local command
    output, task notifications) consist of NOTHING but complete
    ``<tag>...</tag>`` blocks with tags from
    :data:`_CLAUDE_WRAPPER_TAGS`, separated only by whitespace. Any
    deviation -- an unclosed tag, an unknown tag, genuine text before
    or after a block -- means the text is user-authored and must be
    preserved. The scan is a single linear pass (no regexes).

    Args:
        text: Stripped candidate user text.

    Returns:
        True when the whole text is one or more well-formed known
        wrapper blocks (plus surrounding whitespace).
    """
    pos = 0
    n = len(text)
    found = False
    while True:
        while pos < n and text[pos].isspace():
            pos += 1
        if pos == n:
            return found
        if text[pos] != "<":
            return False
        gt = text.find(">", pos + 1, pos + 40)
        if gt == -1:
            return False
        tag = text[pos + 1 : gt]
        if tag not in _CLAUDE_WRAPPER_TAGS:
            return False
        end = text.find(f"</{tag}>", gt + 1)
        if end == -1:
            return False
        pos = end + len(tag) + 3
        found = True


def _consume_agent_wrapper(text: str) -> Optional[str]:
    """Consume one complete agent wrapper element at the text start.

    The element must open with ``<teammate-message`` or
    ``<agent-message`` (the tag name terminated by ``>`` or
    whitespace, so e.g. ``<teammate-messages`` never matches), and be
    closed by the MATCHING end tag. Mismatched, unclosed or unknown
    tags do not parse. The scan is linear (no regexes).

    Args:
        text: Candidate text beginning at the wrapper element.

    Returns:
        The remainder of the text after the matching closing tag, or
        None when no complete matching wrapper element starts here.
    """
    for name in _AGENT_MESSAGE_TAG_NAMES:
        open_prefix = f"<{name}"
        if not text.startswith(open_prefix):
            continue
        after = text[len(open_prefix) :]
        if not after or not (
            after[0] == ">" or after[0].isspace()
        ):
            continue
        gt = text.find(">", len(open_prefix))
        if gt == -1:
            return None
        close_tag = f"</{name}>"
        end = text.find(close_tag, gt + 1)
        if end == -1:
            return None
        return text[end + len(close_tag) :]
    return None


def _is_agent_notification(text: str) -> bool:
    """Check whether text is a complete agent/teammate notification.

    Real notification lines (ground truth from actual sessions) are
    the "Another Claude session sent a message:" banner followed by a
    complete ``<teammate-message ...>...</teammate-message>`` (or
    ``agent-message``) element closed by its MATCHING end tag, then
    either nothing or Claude's known trailing boilerplate paragraph
    ("This came from another Claude session ..."). A standalone
    complete matching wrapper element (defense in depth) also counts.
    Anything else -- the banner with no wrapper, an unclosed or
    mismatched wrapper, or genuine user text after the closing tag --
    is user-authored and preserved.

    Args:
        text: Stripped candidate user text.

    Returns:
        True when the text matches a complete known notification
        shape.
    """
    if text.startswith(_AGENT_NOTIFICATION_BANNER):
        candidate = text[len(_AGENT_NOTIFICATION_BANNER) :].lstrip()
    else:
        candidate = text
    remainder = _consume_agent_wrapper(candidate)
    if remainder is None:
        return False
    tail = remainder.strip()
    return not tail or tail.startswith(
        _AGENT_NOTIFICATION_TRAILER_PREFIX
    )


def _is_lineage_block(text: str) -> bool:
    """Check whether text is a complete generated lineage block.

    aichat's continue/rollover/trim tooling generates ``[SESSION
    LINEAGE]`` context blocks with fixed intro and closing sentences
    (see session_utils' rollover prompts and trim_session's lineage
    note). Only the complete generated shape -- marker, known intro,
    known closer at the very end -- classifies as noise; a genuine
    prompt that merely begins with, quotes, or asks about the marker
    is preserved.

    Args:
        text: Stripped candidate user text.

    Returns:
        True when the text is a complete aichat-generated lineage
        block.
    """
    if not text.startswith(_SESSION_LINEAGE_PREFIX):
        return False
    rest = text[len(_SESSION_LINEAGE_PREFIX) :].lstrip()
    if not rest.startswith(_SESSION_LINEAGE_INTROS):
        return False
    return text.rstrip().endswith(_SESSION_LINEAGE_CLOSERS)


def _is_noise_text(text: str) -> bool:
    """Check whether user-message text is Claude-internal noise.

    Classification validates COMPLETE known wrapper shapes instead of
    bare text prefixes (record-level provenance -- ``isMeta``,
    ``isSidechain``, line type -- is handled by the callers). A text
    is noise only when it is entirely a sequence of well-formed
    Claude wrapper blocks (:func:`_is_pure_wrapper_text`), a complete
    teammate/agent notification (:func:`_is_agent_notification`), or
    a complete aichat-generated ``[SESSION LINEAGE]`` context block
    (:func:`_is_lineage_block`). Genuine prompts that merely begin
    with a suspicious phrase or tag (e.g. "Caveat: do not
    change...", an unclosed wrapper tag, a quoted lineage marker)
    are preserved. System-reminder blocks are NOT handled here: a user
    string/block that consists purely of reminder blocks is dropped
    by the callers (via :func:`_is_pure_reminder_text`), while any
    text merely containing the literal tag is genuine user-authored
    content, not noise.

    Args:
        text: Stripped candidate user text.

    Returns:
        True when the text is system-injected noise, not genuine
        user input.
    """
    if _is_lineage_block(text):
        return True
    if _is_agent_notification(text):
        return True
    return _is_pure_wrapper_text(text)


def _is_pure_reminder_text(text: str) -> bool:
    """Check whether text consists purely of reminder blocks.

    This is the provenance test for INJECTED reminders: in real
    Claude sessions an injected ``<system-reminder>`` always occupies
    an entire user string / text block / tool-result item on its own
    (one or more well-formed blocks separated only by whitespace),
    never mixed into genuine text. Anything else -- text around the
    tag, an unclosed tag, a quoted tag -- is genuine content. The
    scan is a single linear pass (no regex backtracking).

    Args:
        text: Raw user or tool text value.

    Returns:
        True when the whole text is one or more well-formed reminder
        blocks (plus surrounding whitespace).
    """
    pos = 0
    n = len(text)
    found = False
    while True:
        while pos < n and text[pos].isspace():
            pos += 1
        if pos == n:
            return found
        if not text.startswith(_REM_OPEN, pos):
            return False
        end = text.find(_REM_CLOSE, pos + len(_REM_OPEN))
        if end == -1:
            return False
        pos = end + len(_REM_CLOSE)
        found = True


def _strip_trailing_injected_reminders(text: str) -> str:
    """Peel known injected reminder blocks off a tool-output string.

    Claude appends certain reminders (e.g. the Read tool's
    malicious-file notice) AFTER genuine output inside the same
    tool-result string. Only a well-formed block anchored at the very
    end of the string whose body matches a known injected signature
    (:data:`_INJECTED_TRAILING_SIGNATURES`) is removed; stacked
    matching blocks are peeled one at a time. A terminal block with
    an unrecognized body is ambiguous -- it may be genuine content
    that simply ends with the literal tag (e.g. the tail of a read
    file) -- and is preserved verbatim, as is any reminder occurring
    mid-output.

    Args:
        text: Raw tool-output string.

    Returns:
        The string with known appended reminder blocks removed.
    """
    while True:
        trimmed = text.rstrip()
        if not trimmed.endswith(_REM_CLOSE):
            return text
        open_idx = trimmed.rfind(_REM_OPEN)
        if open_idx == -1:
            return text
        body = trimmed[open_idx + len(_REM_OPEN): -len(_REM_CLOSE)]
        if _REM_CLOSE in body:
            return text
        normalized = " ".join(body.split())
        if not normalized.startswith(_INJECTED_TRAILING_SIGNATURES):
            return text
        text = trimmed[:open_idx].rstrip()


def _clean_tool_text(text: str) -> str:
    """Remove injected reminder content from one tool-output string.

    Args:
        text: Raw tool-output string.

    Returns:
        Empty string when the text is purely injected reminder
        blocks; otherwise the text with known appended trailing
        reminders peeled off (genuine content, including literal
        reminder tags inside it, preserved verbatim).
    """
    if _REM_OPEN not in text:
        return text
    if _is_pure_reminder_text(text):
        return ""
    return _strip_trailing_injected_reminders(text)


def _strip_system_reminders(value: Any) -> Any:
    """Remove injected ``<system-reminder>`` content from tool results.

    Real Claude tool results carry injected reminders in exactly two
    shapes: an entire string / list item / ``text`` block that is
    nothing but reminder blocks (dropped), and a known-signature
    reminder appended after genuine output inside the same string
    (peeled off the end). Genuine output -- including literal
    reminder tags embedded inside or even terminating it -- is
    preserved verbatim. Other shapes pass through untouched for
    the tool-value stringifier to render.

    Args:
        value: Raw ``tool_result`` content value.

    Returns:
        The value with injected reminder content removed.
    """
    if isinstance(value, str):
        return _clean_tool_text(value)
    if isinstance(value, list):
        cleaned: list[Any] = []
        for item in value:
            if isinstance(item, str):
                text = _clean_tool_text(item)
                if text.strip():
                    cleaned.append(text)
            elif (
                isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ):
                text = _clean_tool_text(item["text"])
                if text.strip():
                    new_item = dict(item)
                    new_item["text"] = text
                    cleaned.append(new_item)
            else:
                cleaned.append(item)
        return cleaned
    return value
