"""Claude Code specific logic for suppressing tool results."""

import json
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple


def build_tool_name_mapping(input_file: Path) -> Dict[str, str]:
    """
    Build a mapping of tool_use_id to tool name for Claude sessions.

    Args:
        input_file: Path to the input JSONL file.

    Returns:
        Dictionary mapping tool_use_id to tool name.
    """
    tool_map = {}

    with open(input_file, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            if data.get("type") != "assistant":
                continue

            content = data.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue

            for item in content:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "tool_use"
                ):
                    tool_id = item.get("id")
                    tool_name = item.get("name")
                    if tool_id and tool_name:
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
            if isinstance(item, dict) and "text" in item:
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
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            else:
                parts.append(str(item))
        content_str = "".join(parts)
    else:
        content_str = str(content)

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

        with open(input_file, "r") as f:
            for line_num, line in enumerate(f, start=1):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if data.get("type") == "assistant":
                    content = data.get("message", {}).get("content", [])
                    total_length = sum(
                        len(str(item.get("text", "")))
                        for item in content
                        if isinstance(item, dict) and item.get("type") == "text"
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
    with open(input_file, "r") as infile, open(
        output_file, "w"
    ) as outfile:
        for line_num, line in enumerate(infile, start=1):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                outfile.write(line)
                continue

            # Neutralize "context full" error markers - these indicate the parent
            # session hit context limits. We keep the lines (to preserve UUID chain)
            # but remove the error signals so Claude Code won't block resumption.
            if data.get("error") == "invalid_request":
                data["error"] = None
            if data.get("isApiErrorMessage") is True:
                data["isApiErrorMessage"] = False
            msg = data.get("message", {})
            if msg.get("model") == "<synthetic>":
                # Replace synthetic error message with a note
                msg["model"] = "trimmed"
                if "content" in msg:
                    msg["content"] = [{"type": "text", "text": "[Context limit reached in parent session - trimmed]"}]

            # Trim assistant messages if needed
            if data.get("type") == "assistant" and line_num in assistant_indices_to_trim:
                content = data.get("message", {}).get("content", [])
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        original_text = item.get("text", "")
                        original_length = len(original_text)
                        if original_length >= threshold:
                            placeholder = (
                                f"[Assistant message trimmed - "
                                f"original content was {original_length:,} characters. "
                                f"See line {line_num} of {parent_file} for full content]"
                            )
                            item["text"] = placeholder
                            chars_saved += original_length - len(placeholder)
                            num_assistant_trimmed += 1

            # Check if this is a user message with tool results
            elif data.get("type") == "user":
                content = data.get("message", {}).get("content")

                # Handle array content with tool_result
                if isinstance(content, list):
                    for item in content:
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "tool_result"
                        ):
                            tool_use_id = item.get("tool_use_id")
                            tool_name = tool_map.get(
                                tool_use_id, "Unknown"
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
                                    num_tools_trimmed += 1
                                    chars_saved += saved

                # Also suppress in toolUseResult.content if present
                if (
                    "toolUseResult" in data
                    and isinstance(data["toolUseResult"], dict)
                ):
                    tool_result = data["toolUseResult"]
                    if "content" in tool_result:
                        # Find the tool_use_id from message content
                        tool_use_id = None
                        if isinstance(content, list):
                            for item in content:
                                if item.get("type") == "tool_result":
                                    tool_use_id = item.get(
                                        "tool_use_id"
                                    )
                                    break

                        if tool_use_id:
                            tool_name = tool_map.get(
                                tool_use_id, "Unknown"
                            )
                            result_content = tool_result["content"]
                            content_length = get_content_length(
                                result_content
                            )

                            if content_length >= threshold and (
                                target_tools is None
                                or tool_name.lower() in target_tools
                            ):
                                truncated = truncate_content(
                                    result_content, threshold, tool_name,
                                    line_num=line_num, parent_file=parent_file
                                )
                                # Only update if truncation saves space
                                if len(truncated) < content_length:
                                    tool_result["content"] = truncated

            # Replace sessionId if new_session_id provided
            if new_session_id and "sessionId" in data:
                data["sessionId"] = new_session_id

            # Write the (potentially modified) line
            outfile.write(json.dumps(data) + "\n")

    return num_tools_trimmed, num_assistant_trimmed, chars_saved
