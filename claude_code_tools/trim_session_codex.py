"""Codex specific logic for suppressing tool results."""

import json
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple


def build_tool_name_mapping(input_file: Path) -> Dict[str, str]:
    """
    Build a mapping of call_id to tool name for Codex sessions.

    Args:
        input_file: Path to the input JSONL file.

    Returns:
        Dictionary mapping call_id to tool name.
    """
    tool_map = {}

    with open(input_file, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Look for function_call entries
            if data.get("type") != "response_item":
                continue

            payload = data.get("payload", {})
            if payload.get("type") != "function_call":
                continue

            call_id = payload.get("call_id")
            tool_name = payload.get("name")

            if call_id and tool_name:
                tool_map[call_id] = tool_name

    return tool_map


def get_output_length(output_str: str) -> int:
    """
    Calculate the length of Codex tool output.

    Codex outputs are double-JSON-encoded, so we need to parse twice.

    Args:
        output_str: The JSON-encoded output string.

    Returns:
        Length of the actual output content in characters.
    """
    try:
        # First parse: get the output object
        output_obj = json.loads(output_str)
        # Second parse or direct access to output field
        if isinstance(output_obj, dict) and "output" in output_obj:
            return len(str(output_obj["output"]))
        else:
            return len(str(output_obj))
    except (json.JSONDecodeError, TypeError):
        return len(output_str)


def truncate_output(
    output_str: str,
    threshold: int,
    tool_name: str,
    metadata: Dict,
    line_num: Optional[int] = None,
    parent_file: Optional[str] = None,
) -> str:
    """
    Truncate Codex output to threshold length, preserving first N characters.

    Args:
        output_str: The JSON-encoded output string.
        threshold: Maximum length to preserve.
        tool_name: Name of the tool (for truncation notice).
        metadata: Original metadata to preserve.
        line_num: Line number in the parent file (for reference).
        parent_file: Path to the parent session file (for reference).

    Returns:
        JSON-encoded output string with truncated content.
    """
    try:
        # First parse: get the output object
        output_obj = json.loads(output_str)
        # Get the actual output content
        if isinstance(output_obj, dict) and "output" in output_obj:
            content = str(output_obj["output"])
        else:
            content = str(output_obj)
    except (json.JSONDecodeError, TypeError):
        content = output_str

    # If content is within threshold, return original
    if len(content) <= threshold:
        return output_str

    # Truncate and add notice
    original_length = len(content)
    truncated = content[:threshold]

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

    # Create truncated output object
    truncated_obj = {
        "output": truncated + truncation_notice,
        "metadata": metadata,
    }

    result = json.dumps(truncated_obj)

    # Only return truncated version if it actually saves space
    # Otherwise, keep the original output
    if len(result) >= len(output_str):
        return output_str

    return result


def create_suppressed_output(
    tool_name: str, original_length: int, call_id: str, metadata: Dict
) -> str:
    """
    Create a suppressed output in Codex format (deprecated - use truncate_output).

    Args:
        tool_name: Name of the tool.
        original_length: Original output length.
        call_id: The call_id for correlation.
        metadata: Original metadata to preserve.

    Returns:
        JSON-encoded output string with suppression placeholder.
    """
    placeholder_text = (
        f"[Results from {tool_name} tool suppressed - "
        f"original content was {original_length:,} characters]"
    )

    # Preserve metadata, replace output
    suppressed_obj = {
        "output": placeholder_text,
        "metadata": metadata,
    }

    return json.dumps(suppressed_obj)


def _keep_from_line(input_file: Path, keep_last: Optional[int]) -> Optional[int]:
    """Return the first line of the last N user turns, ignoring UI mirrors."""
    if not keep_last or keep_last <= 0:
        return None
    turn_lines: list[int] = []
    with open(input_file, "r") as f:
        for line_num, line in enumerate(f, start=1):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Only user prompts start turns; assistant messages, tool calls,
            # reasoning and event_msg mirrors all belong to the current turn.
            payload = data.get("payload") if isinstance(data, dict) else None
            if (
                isinstance(payload, dict)
                and data.get("type") == "response_item"
                and payload.get("type") == "message"
                and payload.get("role") == "user"
            ):
                turn_lines.append(line_num)
    if not turn_lines:
        return None
    return turn_lines[-min(keep_last, len(turn_lines))]


def process_codex_session(
    input_file: Path,
    output_file: Path,
    tool_map: Dict[str, str],
    target_tools: Set[str],
    threshold: int,
    create_placeholder: callable,
    new_session_id: Optional[str] = None,
    trim_assistant_messages: Optional[int] = None,
    keep_last: Optional[int] = None,
    parent_file: Optional[str] = None,
) -> Tuple[int, int, int]:
    """
    Process Codex session file and trim tool results and assistant messages.

    Args:
        input_file: Path to input JSONL file.
        output_file: Path to output JSONL file.
        tool_map: Mapping of call_id to tool name.
        target_tools: Set of tool names to suppress (None means all).
        threshold: Minimum length threshold for trimming.
        create_placeholder: Function to create placeholder text (unused
            for Codex, we use create_suppressed_output instead).
        new_session_id: Optional new session ID to replace in session_meta events.
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

        with open(input_file, "r") as f:
            for line_num, line in enumerate(f, start=1):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if data.get("type") == "response_item":
                    payload = data.get("payload", {})
                    if (
                        payload.get("type") == "message"
                        and payload.get("role") == "assistant"
                    ):
                        content = payload.get("content", [])
                        total_length = sum(
                            len(str(item.get("text", "")))
                            for item in content
                            if isinstance(item, dict)
                            and item.get("type") == "output_text"
                        )
                        if total_length >= threshold:
                            assistant_messages.append(
                                (line_num, total_length, data)
                            )

        # Determine which to trim based on parameter
        if trim_assistant_messages > 0:
            # Trim first N
            count = min(trim_assistant_messages, len(assistant_messages))
            assistant_indices_to_trim = {
                msg[0] for msg in assistant_messages[:count]
            }
        elif trim_assistant_messages < 0:
            # Trim all except last abs(N)
            keep_count = min(
                abs(trim_assistant_messages), len(assistant_messages)
            )
            trim_count = len(assistant_messages) - keep_count
            assistant_indices_to_trim = {
                msg[0] for msg in assistant_messages[:trim_count]
            }

    # Records at or after this line are the kept tail and are never trimmed.
    keep_from_line = _keep_from_line(input_file, keep_last)

    # Second pass: process and trim
    with open(input_file, "r") as infile, open(
        output_file, "w"
    ) as outfile:
        for line_num, line in enumerate(infile, start=1):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                outfile.write(line)
                continue

            exempt = keep_from_line is not None and line_num >= keep_from_line

            # Replace session_id in session_meta events if new_session_id provided
            if new_session_id and data.get("type") == "session_meta":
                if "payload" in data and "id" in data["payload"]:
                    data["payload"]["id"] = new_session_id

            # Trim assistant messages if needed
            if (
                data.get("type") == "response_item"
                and line_num in assistant_indices_to_trim
            ):
                payload = data.get("payload", {})
                if (
                    payload.get("type") == "message"
                    and payload.get("role") == "assistant"
                ):
                    content = payload.get("content", [])
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "output_text":
                            original_text = item.get("text", "")
                            original_length = len(original_text)
                            if original_length >= threshold and not exempt:
                                placeholder = (
                                    f"[Assistant message trimmed - "
                                    f"original content was {original_length:,} characters. "
                                    f"See line {line_num} of {parent_file} for full content]"
                                )
                                item["text"] = placeholder
                                chars_saved += original_length - len(placeholder)
                                num_assistant_trimmed += 1

            # Look for function_call_output entries
            if data.get("type") != "response_item":
                outfile.write(json.dumps(data) + "\n")
                continue

            payload = data.get("payload", {})
            if payload.get("type") != "function_call_output":
                outfile.write(json.dumps(data) + "\n")
                continue

            # This is a tool result
            call_id = payload.get("call_id")
            tool_name = tool_map.get(call_id, "Unknown")
            output_str = payload.get("output", "")

            # Calculate output length (parse double-JSON)
            output_length = get_output_length(output_str)

            # Check if should suppress
            should_suppress = output_length >= threshold and not exempt and (
                target_tools is None or tool_name.lower() in target_tools
            )

            if should_suppress:
                # Parse the output to extract metadata
                try:
                    output_obj = json.loads(output_str)
                    metadata = (
                        output_obj.get("metadata", {})
                        if isinstance(output_obj, dict)
                        else {}
                    )
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

                # Truncate output
                truncated_output = truncate_output(
                    output_str, threshold, tool_name, metadata,
                    line_num=line_num, parent_file=parent_file
                )

                # Only count as trimmed if truncation actually saved space
                # (truncate_output returns original if no savings)
                saved = len(output_str) - len(truncated_output)
                if saved > 0:
                    payload["output"] = truncated_output
                    num_tools_trimmed += 1
                    chars_saved += saved

            # Write the (potentially modified) line
            outfile.write(json.dumps(data) + "\n")

    return num_tools_trimmed, num_assistant_trimmed, chars_saved
