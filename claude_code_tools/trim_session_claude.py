"""Claude Code specific logic for suppressing tool results."""

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

# Session files are hostile input: a stray invalid-UTF-8 byte must
# never raise UnicodeDecodeError mid-trim. surrogateescape maps
# undecodable bytes to lone surrogates on read and back to the exact
# original bytes on write, so undecodable lines round-trip
# byte-for-byte through the passthrough path (see
# ``_has_undecodable_bytes``: such lines are never rewritten, even
# when they parse as JSON).
_ENCODING = "utf-8"
_ENCODING_ERRORS = "surrogateescape"

# Markers of placeholders synthesized by earlier trims: tool results
# and tool_use inputs share the "[...truncated" prefix; replaced
# assistant messages start with "[Assistant message trimmed".
# Content carrying one of these has ALREADY been trimmed and its
# notice references the backup holding the FULL original content. A
# re-trim (e.g. a second run with a stricter threshold) must skip
# such content: re-truncating it would mint a fresh notice pointing
# at a new backup that only contains the old placeholder, losing the
# only reference to the original content.
_TRIM_NOTICE_MARKERS = ("[...truncated", "[Assistant message trimmed")

# A lone surrogate in a decoded line means the raw bytes were not
# valid UTF-8 (a strict UTF-8 decode can never produce a surrogate;
# surrogateescape maps undecodable bytes to U+DC80-U+DCFF). The
# whole range is matched for safety.
_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _already_trimmed(text: str) -> bool:
    """Return True if ``text`` carries a placeholder from a prior trim."""
    return any(marker in text for marker in _TRIM_NOTICE_MARKERS)


def _has_undecodable_bytes(line: str) -> bool:
    """Return True if ``line`` carries surrogateescape artifacts.

    Such a line held bytes that were not valid UTF-8. It may still
    parse as JSON, but ``json.dumps`` would re-encode the surrogates
    as ``\\udcXX`` escapes instead of the original raw bytes, so
    callers must pass these lines through verbatim.
    """
    return _SURROGATE_RE.search(line) is not None


def _zero_usage(usage: Dict[str, Any]) -> Dict[str, Any]:
    """Zero out all numeric values in a usage dict.

    Handles nested dicts (e.g. server_tool_use,
    cache_creation) and preserves nulls.
    """
    result = {}
    for k, v in usage.items():
        if isinstance(v, dict):
            result[k] = _zero_usage(v)
        elif isinstance(v, (int, float)):
            result[k] = 0
        else:
            result[k] = v
    return result


def build_tool_name_mapping(input_file: Path) -> Dict[str, str]:
    """
    Build a mapping of tool_use_id to tool name for Claude sessions.

    Args:
        input_file: Path to the input JSONL file.

    Returns:
        Dictionary mapping tool_use_id to tool name.
    """
    tool_map = {}

    with open(
        input_file, "r", encoding=_ENCODING, errors=_ENCODING_ERRORS
    ) as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Session lines are hostile input: any level of the
            # structure may have the wrong type.
            if not isinstance(data, dict):
                continue
            if data.get("type") != "assistant":
                continue

            msg = data.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue

            for item in content:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "tool_use"
                ):
                    tool_id = item.get("id")
                    tool_name = item.get("name")
                    if (
                        isinstance(tool_id, str)
                        and tool_id
                        and isinstance(tool_name, str)
                        and tool_name
                    ):
                        tool_map[tool_id] = tool_name

    return tool_map


def get_content_length(content: Any) -> int:
    """
    Calculate the length of tool result content.

    Args:
        content: The content field from a tool_result.

    Returns:
        Length in characters.
    """
    if isinstance(content, str):
        return len(content)
    elif isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict) and isinstance(
                item.get("text"), str
            ):
                total += len(item["text"])
            else:
                total += len(str(item))
        return total
    else:
        return len(str(content))


def truncate_content(
    content: Any,
    threshold: int,
    tool_name: str,
    line_num: Optional[int] = None,
    parent_file: Optional[str] = None,
) -> str:
    """
    Truncate content to threshold length, preserving first N characters.

    Args:
        content: The content field from a tool_result.
        threshold: Maximum length to preserve.
        tool_name: Name of the tool (for truncation notice).
        line_num: Line number in the parent file (for reference).
        parent_file: Path to the parent session file (for reference).

    Returns:
        Truncated content string.
    """
    # Convert content to string if needed
    if isinstance(content, str):
        content_str = content
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(
                item.get("text"), str
            ):
                parts.append(item["text"])
            else:
                parts.append(str(item))
        content_str = "".join(parts)
    else:
        content_str = str(content)

    # Content produced by an earlier trim is never re-truncated: its
    # notice references the backup that holds the full original.
    if _already_trimmed(content_str):
        return content_str

    # If content is within threshold, return as-is
    if len(content_str) <= threshold:
        return content_str

    # Truncate and add notice
    original_length = len(content_str)
    truncated = content_str[:threshold]

    # Build truncation notice with optional reference to parent file
    if line_num is not None and parent_file:
        truncation_notice = (
            f"\n\n[...truncated - original content was "
            f"{original_length:,} characters, showing first {threshold}. "
            f"See line {line_num} of {parent_file} for full content]"
        )
    else:
        truncation_notice = (
            f"\n\n[...truncated - original content was "
            f"{original_length:,} characters, showing first {threshold}]"
        )

    result = truncated + truncation_notice

    # Only return truncated version if it actually saves space
    # Otherwise, keep the original content
    if len(result) >= original_length:
        return content_str

    return result


def process_claude_session(
    input_file: Path,
    output_file: Path,
    tool_map: Dict[str, str],
    target_tools: Set[str],
    threshold: int,
    create_placeholder: callable,
    new_session_id: Optional[str] = None,
    trim_assistant_messages: Optional[int] = None,
    parent_file: Optional[str] = None,
) -> Tuple[int, int, int]:
    """
    Process Claude Code session file and trim tool results and assistant messages.

    Args:
        input_file: Path to input JSONL file.
        output_file: Path to output JSONL file.
        tool_map: Mapping of tool_use_id to tool name.
        target_tools: Set of tool names to suppress (None means all).
        threshold: Minimum length threshold for trimming.
        create_placeholder: Function to create placeholder text.
        new_session_id: Optional new session ID to replace in all events.
        trim_assistant_messages: Optional assistant message trimming (see trim_and_create_session).
        parent_file: Path to parent session file (for truncation references).

    Returns:
        Tuple of (num_tools_trimmed, num_assistant_trimmed, chars_saved).
    """
    # Use input_file as parent_file if not provided
    if parent_file is None:
        parent_file = str(input_file.absolute())
    num_tools_trimmed = 0
    num_assistant_trimmed = 0
    chars_saved = 0

    # First pass: identify assistant messages to trim
    assistant_indices_to_trim = set()
    if trim_assistant_messages is not None:
        assistant_messages = []  # List of (line_num, length, data)

        with open(
            input_file,
            "r",
            encoding=_ENCODING,
            errors=_ENCODING_ERRORS,
        ) as f:
            for line_num, line in enumerate(f, start=1):
                # Lines with invalid UTF-8 pass through verbatim in
                # the second pass; never select them for trimming.
                if _has_undecodable_bytes(line):
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not isinstance(data, dict):
                    continue

                if data.get("type") == "assistant":
                    msg = data.get("message")
                    content = (
                        msg.get("content")
                        if isinstance(msg, dict)
                        else None
                    )
                    if not isinstance(content, list):
                        continue
                    # Placeholders from an earlier trim are not
                    # trimmable content: exclude them so already
                    # trimmed messages do not occupy trim slots.
                    total_length = sum(
                        len(str(item.get("text", "")))
                        for item in content
                        if isinstance(item, dict)
                        and item.get("type") == "text"
                        and not _already_trimmed(str(item.get("text", "")))
                    )
                    if total_length >= threshold:
                        assistant_messages.append((line_num, total_length, data))

        # Determine which to trim based on parameter
        if trim_assistant_messages > 0:
            # Trim first N
            count = min(trim_assistant_messages, len(assistant_messages))
            assistant_indices_to_trim = {
                msg[0] for msg in assistant_messages[:count]
            }
        elif trim_assistant_messages < 0:
            # Trim all except last abs(N)
            keep_count = min(abs(trim_assistant_messages), len(assistant_messages))
            trim_count = len(assistant_messages) - keep_count
            assistant_indices_to_trim = {
                msg[0] for msg in assistant_messages[:trim_count]
            }

    # Second pass: process and trim
    with open(
        input_file, "r", encoding=_ENCODING, errors=_ENCODING_ERRORS
    ) as infile, open(
        output_file, "w", encoding=_ENCODING, errors=_ENCODING_ERRORS
    ) as outfile:
        for line_num, line in enumerate(infile, start=1):
            # Invalid UTF-8 must round-trip byte-for-byte: such a
            # line may still parse as JSON (surrogateescape maps the
            # bad bytes to lone surrogates), but json.dumps would
            # rewrite those surrogates as \udcXX escapes instead of
            # the original bytes. Pass the line through untouched;
            # surrogateescape on write restores the exact bytes.
            if _has_undecodable_bytes(line):
                outfile.write(line)
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                outfile.write(line)
                continue

            # Non-dict JSON lines (arrays, strings, numbers) are
            # hostile/foreign shapes: pass them through verbatim.
            if not isinstance(data, dict):
                outfile.write(line)
                continue

            # Track real mutations: untouched records are written
            # back verbatim at the end of the loop (re-serializing
            # them would gratuitously reformat the line).
            modified = False

            # Neutralize "context full" error markers - these indicate the parent
            # session hit context limits. We keep the lines (to preserve UUID chain)
            # but remove the error signals so Claude Code won't block resumption.
            if data.get("error") == "invalid_request":
                data["error"] = None
                modified = True
            if data.get("isApiErrorMessage") is True:
                data["isApiErrorMessage"] = False
                modified = True
            # `message` is hostile too (may be null or a non-dict);
            # treat a malformed one as absent for the fixups below.
            # The line itself still passes through unchanged.
            msg = data.get("message")
            if not isinstance(msg, dict):
                msg = {}
            if msg.get("model") == "<synthetic>":
                # Replace synthetic error message with a note
                msg["model"] = "trimmed"
                modified = True
                if "content" in msg:
                    msg["content"] = [{"type": "text", "text": "[Context limit reached in parent session - trimmed]"}]

            # Zero out usage metadata so Claude Code doesn't
            # think the context is still full from the parent
            # session. Fresh usage will be populated by the API
            # on the first successful request.
            if isinstance(msg.get("usage"), dict):
                zeroed_usage = _zero_usage(msg["usage"])
                if zeroed_usage != msg["usage"]:
                    msg["usage"] = zeroed_usage
                    modified = True

            # Trim assistant messages
            if data.get("type") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict):
                            continue

                        # Truncate tool_use input params
                        # (Write/Edit calls carry full file
                        # content in their input fields)
                        if item.get("type") == "tool_use":
                            tool_input = item.get("input")
                            if isinstance(tool_input, dict):
                                for k, v in tool_input.items():
                                    # Never re-truncate a value that
                                    # an earlier trim already
                                    # replaced (its notice points at
                                    # the original backup).
                                    if (
                                        isinstance(v, str)
                                        and len(v) >= threshold
                                        and not _already_trimmed(v)
                                    ):
                                        orig_len = len(v)
                                        item["input"][k] = (
                                            v[:threshold]
                                            + f"\n\n[...truncated"
                                            f" {k} - was"
                                            f" {orig_len:,}"
                                            f" chars. See"
                                            f" line {line_num}"
                                            f" of {parent_file}"
                                            f"]"
                                        )
                                        modified = True
                                        saved = (
                                            orig_len
                                            - len(
                                                item["input"][k]
                                            )
                                        )
                                        if saved > 0:
                                            chars_saved += saved
                                            num_tools_trimmed += 1

                        # Truncate text blocks (selected
                        # messages only)
                        elif (
                            item.get("type") == "text"
                            and line_num
                            in assistant_indices_to_trim
                        ):
                            original_text = item.get("text", "")
                            if not isinstance(original_text, str):
                                continue
                            # A placeholder from an earlier trim is
                            # never replaced again: it references
                            # the backup with the full original.
                            if _already_trimmed(original_text):
                                continue
                            original_length = len(original_text)
                            if original_length >= threshold:
                                placeholder = (
                                    f"[Assistant message"
                                    f" trimmed - original"
                                    f" content was"
                                    f" {original_length:,}"
                                    f" characters. See"
                                    f" line {line_num} of"
                                    f" {parent_file} for"
                                    f" full content]"
                                )
                                item["text"] = placeholder
                                modified = True
                                chars_saved += (
                                    original_length
                                    - len(placeholder)
                                )
                                num_assistant_trimmed += 1

            # Check if this is a user message with tool results
            elif data.get("type") == "user":
                content = msg.get("content")

                # Handle array content with tool_result
                if isinstance(content, list):
                    for item in content:
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "tool_result"
                        ):
                            # tool_use_id may be unhashable (dict/list)
                            tool_use_id = item.get("tool_use_id")
                            tool_name = (
                                tool_map.get(tool_use_id, "Unknown")
                                if isinstance(tool_use_id, str)
                                else "Unknown"
                            )
                            result_content = item.get("content", "")

                            content_length = get_content_length(
                                result_content
                            )

                            # Check if should truncate
                            if content_length >= threshold and (
                                target_tools is None
                                or tool_name.lower() in target_tools
                            ):
                                truncated = truncate_content(
                                    result_content, threshold, tool_name,
                                    line_num=line_num, parent_file=parent_file
                                )
                                # Only count as trimmed if content actually changed
                                # (truncate_content returns original if no savings)
                                saved = content_length - len(truncated)
                                if saved > 0:
                                    item["content"] = truncated
                                    modified = True
                                    num_tools_trimmed += 1
                                    chars_saved += saved

                # NOTE: toolUseResult is internal metadata for
                # undo/display — NOT sent to the API. Leave it
                # intact to avoid breaking Claude Code's UI.

            # Replace sessionId if new_session_id provided
            if (
                new_session_id
                and "sessionId" in data
                and data["sessionId"] != new_session_id
            ):
                data["sessionId"] = new_session_id
                modified = True

            # Rewrite only lines that actually changed; everything
            # else passes through byte-for-byte.
            if modified:
                outfile.write(json.dumps(data) + "\n")
            else:
                outfile.write(line)

    return num_tools_trimmed, num_assistant_trimmed, chars_saved
