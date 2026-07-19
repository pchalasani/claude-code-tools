"""Flattening helpers for the Codex -> Claude session converter.

Split out of :mod:`claude_code_tools.port_codex_to_claude` to keep
both modules under the repo's 1000-line limit. This module owns the
per-item flattening rules: rendering tool calls/results as clearly
labeled, truncated plain text, and dropping reasoning items and every
form of encrypted content.

Memory bounds: structured tool values are serialized incrementally
into a buffer capped at ``TOOL_TEXT_CAP`` characters (never
materializing a full ``json.dumps`` copy of an enormous value).
"""

from __future__ import annotations

import json
from typing import Any, Optional, Union

from claude_code_tools.export_session import (
    _extract_codex_message_text,
    _is_meta_user_message,
)

# Maximum characters kept for each flattened tool-arg / tool-result
# text. Longer text is cut with an explicit truncation suffix.
# Ordinary user/assistant message text is NEVER truncated: only
# flattened tool arguments/results are capped.
TOOL_TEXT_CAP = 1500

# Depth bound for the encrypted-content scan/strip; payloads nested
# deeper than this are conservatively treated as encrypted (dropped).
_ENCRYPTED_SCAN_MAX_DEPTH = 100

# Chunk size (chars) used when locating the stripped bounds of a huge
# string without copying it whole.
_STRIP_CHUNK_CHARS = 4096


def _truncate_text(text: str, cap: int) -> str:
    """Truncate text to ``cap`` chars with an explicit suffix.

    Args:
        text: Raw text to truncate.
        cap: Maximum number of characters to keep.

    Returns:
        The original text if within the cap, otherwise the first
        ``cap`` characters followed by ``"... [truncated N chars]"``.
    """
    if len(text) <= cap:
        return text
    dropped = len(text) - cap
    return f"{text[:cap]}... [truncated {dropped} chars]"


def _stripped_bounds(text: str) -> tuple[int, int]:
    """Locate the whitespace-stripped bounds of a string without copying.

    Scans inward from both ends in bounded chunks (each at most
    ``_STRIP_CHUNK_CHARS`` characters), so even a huge
    whitespace-padded value never produces a near-full-size copy.

    Args:
        text: The string to inspect.

    Returns:
        ``(start, end)`` indices such that ``text[start:end]`` equals
        ``text.strip()`` (``start == end`` for all-whitespace input).
    """
    n = len(text)
    start = 0
    while start < n:
        chunk = text[start : start + _STRIP_CHUNK_CHARS]
        lstripped = chunk.lstrip()
        if lstripped:
            start += len(chunk) - len(lstripped)
            break
        start += len(chunk)
    end = n
    while end > start:
        chunk_start = max(start, end - _STRIP_CHUNK_CHARS)
        chunk = text[chunk_start:end]
        rstripped = chunk.rstrip()
        if rstripped:
            end = chunk_start + len(rstripped)
            break
        end = chunk_start
    return start, end


def _strip_and_truncate(text: str, cap: int) -> str:
    """Strip and truncate a string with bounded intermediate copies.

    Equivalent to ``_truncate_text(text.strip(), cap)`` but never
    materializes the full stripped string: the stripped bounds are
    located first (chunked scan, no copy), then only the slice that
    fits within ``cap`` is extracted.

    Args:
        text: Raw (possibly huge, whitespace-padded) string value.
        cap: Maximum number of characters to keep.

    Returns:
        The stripped text if within the cap, otherwise its first
        ``cap`` characters followed by ``"... [truncated N chars]"``.
    """
    start, end = _stripped_bounds(text)
    length = end - start
    if length <= cap:
        return text[start:end]
    dropped = length - cap
    return f"{text[start:start + cap]}... [truncated {dropped} chars]"


def _join_text_blocks(content: Any) -> str:
    """Join the plain-text blocks of a Codex content payload.

    Encrypted blocks (``encrypted_content``), non-text blocks and
    blocks whose ``text`` value is not a string are skipped.

    Args:
        content: A string, or a list of Codex content blocks.

    Returns:
        The concatenated text (may be empty).
    """
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("input_text", "output_text", "text"):
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts).strip()


def _text_block_parts(content: Any) -> list[str]:
    """Collect the plain-text block strings of a Codex content list.

    Args:
        content: A list of Codex content blocks (any other type
            yields an empty list).

    Returns:
        List of non-empty text strings (references, not copies).
    """
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("input_text", "output_text", "text"):
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return parts


def _join_truncated(parts: list[str], sep: str, cap: int) -> str:
    """Join text parts stripped-then-capped, with bounded copies.

    Semantically equivalent to ``_truncate_text(sep.join(parts)
    .strip(), cap)``: the whitespace-stripped bounds of the LOGICAL
    joined text are located first, so leading/trailing whitespace
    neither consumes the cap nor inflates the truncation count. The
    full joined string is never materialized when it exceeds the
    cap -- only the slice that fits within ``cap`` is extracted.

    Args:
        parts: Text fragments to join.
        sep: Separator inserted between fragments.
        cap: Maximum number of joined (stripped) characters to keep.

    Returns:
        The stripped joined text, truncated with an explicit
        ``"... [truncated N chars]"`` suffix when over the cap.
    """
    segments: list[str] = []
    for i, part in enumerate(parts):
        if i:
            segments.append(sep)
        segments.append(part)
    # Global offset of the first non-whitespace character.
    start = 0
    offset = 0
    for seg in segments:
        seg_start, seg_end = _stripped_bounds(seg)
        if seg_end > seg_start:
            start = offset + seg_start
            break
        offset += len(seg)
    else:
        return ""
    # Global offset just past the last non-whitespace character.
    total_len = sum(len(seg) for seg in segments)
    end = total_len
    offset = total_len
    for seg in reversed(segments):
        offset -= len(seg)
        seg_start, seg_end = _stripped_bounds(seg)
        if seg_end > seg_start:
            end = offset + seg_end
            break
    stripped_total = end - start
    stop = start + min(stripped_total, cap)
    pieces: list[str] = []
    offset = 0
    for seg in segments:
        seg_end = offset + len(seg)
        if seg_end > start:
            lo = max(start - offset, 0)
            pieces.append(seg[lo : stop - offset])
        offset = seg_end
        if offset >= stop:
            break
    text = "".join(pieces)
    if stripped_total <= cap:
        return text
    return text + f"... [truncated {stripped_total - cap} chars]"


def _too_deep_repr(pieces: list[str]) -> str:
    """Render the capped fallback for un-encodable deep nesting.

    Args:
        pieces: Serialized chunks accumulated before the failure
            (already capped by the caller).

    Returns:
        The accumulated prefix with an explicit marker appended.
    """
    return "".join(pieces) + "... [unserializable: nesting too deep]"


def _dumps_truncated(value: Any, cap: int) -> str:
    """Serialize a structured value to JSON, capped at ``cap`` chars.

    Uses :meth:`json.JSONEncoder.iterencode` so at most ``cap``
    serialized characters are accumulated; a full ``json.dumps`` copy
    of an enormous value is never materialized. Hostile but parseable
    input can nest deeper than the encoder's recursion budget
    (``json.loads`` accepts depths ``iterencode`` cannot re-emit), so
    ``RecursionError`` is caught and rendered as the capped prefix
    serialized so far plus an explicit marker -- ``str(value)`` is no
    fallback there, since ``repr`` recurses just as deep.

    Args:
        value: JSON-compatible structure (dict, list, scalar).
        cap: Maximum number of serialized characters to keep.

    Returns:
        The (possibly truncated) deterministic JSON text, with an
        explicit ``"... [truncated N chars]"`` suffix when over the
        cap, or a capped best-effort rendering with an explicit
        marker when the value cannot be serialized.
    """
    encoder = json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, default=str
    )
    pieces: list[str] = []
    kept = 0
    total = 0
    try:
        for chunk in encoder.iterencode(value):
            total += len(chunk)
            room = cap - kept
            if room > 0:
                pieces.append(
                    chunk if len(chunk) <= room else chunk[:room]
                )
                kept += min(len(chunk), room)
    except (TypeError, ValueError):
        try:
            return _truncate_text(str(value), cap)
        except RecursionError:
            return _too_deep_repr(pieces)
    except RecursionError:
        return _too_deep_repr(pieces)
    if total <= cap:
        return "".join(pieces)
    return "".join(pieces) + f"... [truncated {total - cap} chars]"


def _stringify_tool_value(value: Any, cap: int) -> str:
    """Render a tool argument or result value as capped plain text.

    Strings pass through; lists of Codex text blocks are joined; any
    other JSON-compatible structure (dict, list, scalar) is serialized
    deterministically. In every branch at most ``cap`` characters are
    kept, with an explicit ``"... [truncated N chars]"`` suffix, and
    truncation is applied while the text is built so an oversized
    value never yields an uncapped intermediate string.

    Args:
        value: Raw tool argument/result value from a Codex payload.
        cap: Maximum number of characters to keep.

    Returns:
        A capped text rendering of the value (may be empty).
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return _strip_and_truncate(value, cap)
    if isinstance(value, list):
        parts = _text_block_parts(value)
        if parts:
            return _join_truncated(parts, "\n", cap)
    return _dumps_truncated(value, cap)


# Sentinel meaning "this value must be removed entirely" during the
# bounded encrypted-content strip.
_DROPPED = object()


def _strip_encrypted(value: Any) -> Any:
    """Remove ALL encrypted content from a structured value.

    Wherever they occur -- as dict values, list elements, or nested
    arbitrarily deep -- this drops:

    * every dict key named ``encrypted_content``, and
    * every dict whose own ``type`` is ``encrypted_content`` (the
      whole dict is removed, not just the marker).

    Non-encrypted sibling values are preserved. The traversal is
    strictly bounded: structures nested beyond
    ``_ENCRYPTED_SCAN_MAX_DEPTH`` or containing reference cycles
    cannot be verified plaintext and are conservatively dropped, so
    the strip can never raise ``RecursionError`` on hostile input.

    Args:
        value: Raw tool argument/result value from a Codex payload.

    Returns:
        A copy of the value with all encrypted content removed, or
        None when the value itself had to be dropped (scalars pass
        through unchanged).
    """
    stripped = _strip_encrypted_bounded(value, 0, set())
    return None if stripped is _DROPPED else stripped


def _strip_encrypted_bounded(
    value: Any, depth: int, active: set
) -> Union[Any, object]:
    """Depth- and cycle-bounded worker for :func:`_strip_encrypted`.

    Recursion depth is hard-capped at ``_ENCRYPTED_SCAN_MAX_DEPTH``
    (far below the interpreter's recursion limit), so arbitrarily
    nested valid JSON can never overflow the stack; anything at or
    beyond the cap is conservatively dropped.

    Args:
        value: Structure (or scalar) being stripped.
        depth: Current nesting depth (0 for the top-level value).
        active: ids of containers on the current traversal path,
            used to detect reference cycles.

    Returns:
        The stripped copy, or the ``_DROPPED`` sentinel when the
        value must be removed entirely.
    """
    if isinstance(value, dict):
        if depth >= _ENCRYPTED_SCAN_MAX_DEPTH or id(value) in active:
            return _DROPPED
        if value.get("type") == "encrypted_content":
            return _DROPPED
        active.add(id(value))
        try:
            out: dict[str, Any] = {}
            for key, val in value.items():
                if key == "encrypted_content":
                    continue
                stripped = _strip_encrypted_bounded(
                    val, depth + 1, active
                )
                if stripped is not _DROPPED:
                    out[key] = stripped
            return out
        finally:
            active.discard(id(value))
    if isinstance(value, list):
        if depth >= _ENCRYPTED_SCAN_MAX_DEPTH or id(value) in active:
            return _DROPPED
        active.add(id(value))
        try:
            items: list[Any] = []
            for item in value:
                stripped = _strip_encrypted_bounded(
                    item, depth + 1, active
                )
                if stripped is not _DROPPED:
                    items.append(stripped)
            return items
        finally:
            active.discard(id(value))
    return value


def _has_encrypted_content(payload: dict[str, Any]) -> bool:
    """Check whether a payload carries any encrypted content.

    Field PRESENCE marks a payload as encrypted: even an empty or
    null ``encrypted_content`` value means the item was produced in
    encrypted form and must be dropped. The scan is RECURSIVE: an
    ``encrypted_content`` field (or a block whose ``type`` is
    ``encrypted_content``) nested anywhere inside the payload -- at
    any depth, inside dicts or lists -- marks the whole payload as
    encrypted. Structures nested beyond
    ``_ENCRYPTED_SCAN_MAX_DEPTH`` (or containing reference cycles)
    cannot be verified plaintext and are conservatively treated as
    encrypted.

    Args:
        payload: A Codex response-item payload.

    Returns:
        True if the payload contains encrypted content anywhere.
    """
    seen: set[int] = set()
    stack: list[tuple[Any, int]] = [(payload, 0)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, dict):
            if depth > _ENCRYPTED_SCAN_MAX_DEPTH or id(value) in seen:
                return True
            seen.add(id(value))
            if (
                "encrypted_content" in value
                or value.get("type") == "encrypted_content"
            ):
                return True
            stack.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            if depth > _ENCRYPTED_SCAN_MAX_DEPTH or id(value) in seen:
                return True
            seen.add(id(value))
            stack.extend((v, depth + 1) for v in value)
    return False


def _flatten_payload(
    payload: dict[str, Any],
) -> Optional[tuple[str, str]]:
    """Flatten one Codex response-item payload into (role, text).

    Tool-result items (``function_call_output`` /
    ``custom_tool_call_output``) ALWAYS yield a labeled message, even
    when the ``output`` field is absent, null, empty, or
    whitespace-only: missing/null output is rendered as
    ``"(no output)"`` and empty text as ``"(empty output)"``.

    Args:
        payload: The ``payload`` dict of a ``response_item`` line
            (or, for legacy rollouts, the bare item itself).

    Returns:
        A ``(role, text)`` tuple where role is ``"user"`` or
        ``"assistant"``, or None if the item is dropped.
    """
    ptype = payload.get("type")

    if ptype == "message":
        role = payload.get("role")
        text = _extract_codex_message_text({"payload": payload})
        if not text:
            return None
        if role == "user":
            if _is_meta_user_message(payload, text):
                return None
            return ("user", text)
        if role == "assistant":
            return ("assistant", text)
        # developer / system / anything else: Codex-internal noise.
        return None

    if ptype == "agent_message":
        # Inter-agent traffic. Encrypted payloads (even partially
        # encrypted ones with a plaintext routing envelope) are
        # dropped entirely.
        if _has_encrypted_content(payload):
            return None
        text = _join_text_blocks(payload.get("content"))
        if not text:
            return None
        return ("assistant", text)

    if ptype in ("function_call", "custom_tool_call"):
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            name = "unknown"
        args = payload.get("arguments")
        if args is None:
            args = payload.get("input")
        args_text = _stringify_tool_value(
            _strip_encrypted(args), TOOL_TEXT_CAP
        )
        return ("assistant", f"[codex tool call] {name}({args_text})")

    if ptype in ("function_call_output", "custom_tool_call_output"):
        output = payload.get("output")
        text = _stringify_tool_value(
            _strip_encrypted(output), TOOL_TEXT_CAP
        )
        if not text:
            text = "(no output)" if output is None else "(empty output)"
        return ("assistant", f"[codex tool result] {text}")

    # reasoning (incl. encrypted_content), web searches, etc.: drop.
    return None
