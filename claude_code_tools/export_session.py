"""Export sessions with YAML front matter for indexing."""

import json
import os
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Optional

# Lazy import yaml to allow module to load even if not installed
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    yaml = None  # type: ignore
    YAML_AVAILABLE = False


def _require_yaml():
    """Raise helpful error if pyyaml is not installed."""
    if not YAML_AVAILABLE:
        raise ImportError(
            "pyyaml is required for YAML front matter export.\n"
            "Install with: pip install pyyaml\n"
            "Or reinstall claude-code-tools: uv tool install claude-code-tools"
        )


def _truncate_text(text: str, max_length: int = 200) -> str:
    """Truncate text to max length, adding ellipsis if needed."""
    text = text.strip()
    # Replace newlines with spaces for single-line display
    text = " ".join(text.split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def _get_last_line_timestamp(file_path: Path) -> Optional[str]:
    """
    Efficiently read the last line of a JSONL file and extract its timestamp.

    Reads a chunk from the end of file (O(1) seek + single read).

    Args:
        file_path: Path to the JSONL file

    Returns:
        ISO timestamp string if found, None otherwise
    """
    try:
        with open(file_path, 'rb') as f:
            # Seek to end to get file size
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return None

            # Read last 16KB (should be plenty for a JSONL line)
            chunk_size = min(16384, file_size)
            f.seek(-chunk_size, 2)
            chunk = f.read().decode('utf-8')

            # Split by newlines and get last non-empty line
            lines = chunk.strip().split('\n')
            last_line = lines[-1] if lines else None
            if not last_line:
                return None

            # Parse JSON and extract timestamp
            data = json.loads(last_line)
            return data.get("timestamp")

    except (OSError, IOError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _extract_claude_message_text(data: dict) -> Optional[str]:
    """
    Extract text content from a Claude session message.

    Args:
        data: Parsed JSON line from Claude session

    Returns:
        Extracted text or None if not a text message
    """
    message = data.get("message", {})
    content = message.get("content")

    if not content:
        return None

    # Handle string content
    if isinstance(content, str):
        return content.strip() if content.strip() else None

    # Handle list of content blocks
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str) and block.strip():
                return block.strip()
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    return text

    return None


def _extract_codex_message_text(data: dict) -> Optional[str]:
    """
    Extract text content from a Codex session message.

    Args:
        data: Parsed JSON line from Codex session

    Returns:
        Extracted text or None if not a text message
    """
    payload = data.get("payload", {})
    if payload.get("type") != "message":
        return None

    content = payload.get("content", [])
    if not isinstance(content, list):
        return None

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        # Both input_text and output_text have text field
        if block_type in ("input_text", "output_text"):
            text = block.get("text", "").strip()
            if text:
                return text

    return None


def extract_first_last_messages(
    session_file: Path, agent: str
) -> tuple[Optional[dict[str, str]], Optional[dict[str, str]]]:
    """
    Extract first and last user/assistant messages from a session.

    Args:
        session_file: Path to session JSONL file
        agent: Agent type ('claude' or 'codex')

    Returns:
        Tuple of (first_msg, last_msg) where each is a dict with 'role' and
        'content' keys, or None if not found
    """
    first_msg: Optional[dict[str, str]] = None
    last_msg: Optional[dict[str, str]] = None

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                role: Optional[str] = None
                text: Optional[str] = None

                if agent == "claude":
                    msg_type = data.get("type")
                    if msg_type in ("user", "assistant"):
                        role = msg_type
                        text = _extract_claude_message_text(data)
                elif agent == "codex":
                    if data.get("type") == "response_item":
                        payload = data.get("payload", {})
                        if payload.get("type") == "message":
                            role = payload.get("role")
                            text = _extract_codex_message_text(data)

                if role and text:
                    msg_dict = {
                        "role": role,
                        "content": _truncate_text(text),
                    }
                    if first_msg is None:
                        first_msg = msg_dict
                    # Always update last_msg to get the last one
                    last_msg = msg_dict

    except (OSError, IOError):
        pass

    return first_msg, last_msg


def extract_session_metadata(session_file: Path, agent: str) -> dict[str, Any]:
    """
    Extract metadata from a session JSONL file.

    Reads the first few lines to extract:
    - session_id
    - cwd (working directory)
    - git branch (if available)
    - lineage info (trim_metadata, continue_metadata)

    Args:
        session_file: Path to session JSONL file
        agent: Agent type ('claude' or 'codex')

    Returns:
        Dict with extracted metadata
    """
    # Detect sidechain from filename pattern (agent-* prefix)
    # This is more reliable than checking isSidechain field in JSON,
    # which can be set on individual messages within main sessions
    is_sidechain = session_file.name.startswith("agent-")

    metadata: dict[str, Any] = {
        "session_id": session_file.stem,
        "agent": agent,
        "file_path": str(session_file.absolute()),
        "cwd": None,
        "branch": None,
        "derivation_type": None,
        "is_sidechain": is_sidechain,
        "session_type": None,  # "helper" for SDK/headless sessions
        "parent_session_id": None,
        "parent_session_file": None,
        "original_session_id": None,
        "trim_stats": None,
        "first_msg": None,
        "last_msg": None,
    }

    # Track session start timestamp from JSON metadata
    session_start_timestamp: str | None = None

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Extract cwd (first line that has it)
                if metadata["cwd"] is None and data.get("cwd"):
                    metadata["cwd"] = data["cwd"]

                # Extract git branch (first line that has it)
                if metadata["branch"] is None and data.get("gitBranch"):
                    metadata["branch"] = data["gitBranch"]

                # Extract session ID from sessionId field if available
                if data.get("sessionId"):
                    metadata["session_id"] = data["sessionId"]

                # Extract trim_metadata (for trimmed sessions)
                if "trim_metadata" in data:
                    tm = data["trim_metadata"]
                    metadata["derivation_type"] = "trimmed"
                    metadata["parent_session_file"] = tm.get("parent_file")
                    if tm.get("parent_file"):
                        parent_path = Path(tm["parent_file"])
                        metadata["parent_session_id"] = parent_path.stem
                    if tm.get("stats"):
                        metadata["trim_stats"] = tm["stats"]

                # Extract continue_metadata (for continued sessions)
                if "continue_metadata" in data:
                    cm = data["continue_metadata"]
                    metadata["derivation_type"] = "continued"
                    metadata["parent_session_id"] = cm.get("parent_session_id")
                    metadata["parent_session_file"] = cm.get("parent_session_file")

                # Extract sessionType (e.g., "helper" for SDK/headless sessions)
                if "sessionType" in data and metadata["session_type"] is None:
                    metadata["session_type"] = data["sessionType"]

                # Extract git branch for Claude from file-history-snapshot metadata
                if (
                    agent == "claude"
                    and metadata["branch"] is None
                    and data.get("type") == "file-history-snapshot"
                ):
                    git_info = data.get("metadata", {}).get("git", {})
                    if git_info.get("branch"):
                        metadata["branch"] = git_info["branch"]

                # Extract git branch for Codex sessions from session_meta
                if agent == "codex" and data.get("type") == "session_meta":
                    payload = data.get("payload", {})
                    git_info = payload.get("git", {})
                    if git_info.get("branch"):
                        metadata["branch"] = git_info["branch"]
                    if payload.get("cwd"):
                        metadata["cwd"] = payload["cwd"]
                    if payload.get("id"):
                        metadata["session_id"] = payload["id"]
                    if session_start_timestamp is None and data.get("timestamp"):
                        session_start_timestamp = data["timestamp"]

                # Extract session start timestamp from first entry with timestamp
                if session_start_timestamp is None and data.get("timestamp"):
                    session_start_timestamp = data["timestamp"]

                # Stop once we have the essential metadata (cwd and branch)
                # or after 500 lines as a safety limit
                if (metadata["cwd"] and metadata["branch"]) or line_num >= 500:
                    break

    except (OSError, IOError):
        pass

    # Note: customTitle extraction is done in search_index.py's _extract_session_content
    # during the single-pass content extraction, to avoid an extra file scan here.

    # Get modified time from last JSONL entry's timestamp (reflects actual session
    # activity, portable across machines). Fall back to file mtime if not found.
    last_timestamp = _get_last_line_timestamp(session_file)
    if last_timestamp:
        metadata["modified"] = last_timestamp
    else:
        try:
            stat = session_file.stat()
            metadata["modified"] = datetime.fromtimestamp(
                stat.st_mtime
            ).astimezone().isoformat()
        except OSError:
            pass

    # Use session start timestamp from JSON metadata if available,
    # otherwise fall back to file birthtime (macOS) or mtime
    if session_start_timestamp:
        metadata["created"] = session_start_timestamp
    else:
        try:
            stat = session_file.stat()
            # On macOS, st_birthtime is actual creation time; st_ctime is metadata
            # change time. Fall back to mtime if birthtime unavailable.
            if hasattr(stat, "st_birthtime"):
                metadata["created"] = datetime.fromtimestamp(
                    stat.st_birthtime
                ).astimezone().isoformat()
            else:
                metadata["created"] = datetime.fromtimestamp(
                    stat.st_mtime
                ).astimezone().isoformat()
        except OSError:
            pass

    # Count lines
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            metadata["lines"] = sum(1 for _ in f)
    except (OSError, IOError):
        metadata["lines"] = 0

    # Derive project name from cwd
    if metadata["cwd"]:
        metadata["project"] = Path(metadata["cwd"]).name

    # Extract first and last messages
    first_msg, last_msg = extract_first_last_messages(session_file, agent)
    metadata["first_msg"] = first_msg
    metadata["last_msg"] = last_msg

    return metadata


def find_original_session_id(session_file: Path) -> Optional[str]:
    """
    Trace back through lineage to find the original session ID.

    Args:
        session_file: Path to session file

    Returns:
        Original session ID or None if this is the original
    """
    try:
        from claude_code_tools.session_lineage import get_full_lineage_chain

        chain = get_full_lineage_chain(session_file)
        if chain and len(chain) > 1:
            # Last item in chain is the original
            original_file, _ = chain[-1]
            return original_file.stem
    except Exception:
        pass

    return None


def generate_yaml_frontmatter(metadata: dict[str, Any]) -> str:
    """
    Generate YAML front matter string from metadata.

    Args:
        metadata: Dict with session metadata

    Returns:
        YAML front matter string with --- delimiters

    Raises:
        ImportError: If pyyaml is not installed
    """
    _require_yaml()

    # Build ordered dict for cleaner YAML output
    yaml_data: dict[str, Any] = {}

    # Identity
    yaml_data["session_id"] = metadata.get("session_id")
    yaml_data["agent"] = metadata.get("agent")
    yaml_data["file_path"] = metadata.get("file_path")

    # Project context
    if metadata.get("project"):
        yaml_data["project"] = metadata["project"]
    if metadata.get("branch"):
        yaml_data["branch"] = metadata["branch"]
    if metadata.get("cwd"):
        yaml_data["cwd"] = metadata["cwd"]

    # Stats
    if metadata.get("lines"):
        yaml_data["lines"] = metadata["lines"]
    if metadata.get("created"):
        yaml_data["created"] = metadata["created"]
    if metadata.get("modified"):
        yaml_data["modified"] = metadata["modified"]

    # Lineage and session type
    if metadata.get("derivation_type"):
        yaml_data["derivation_type"] = metadata["derivation_type"]
    if metadata.get("is_sidechain"):
        yaml_data["is_sidechain"] = metadata["is_sidechain"]
    if metadata.get("parent_session_id"):
        yaml_data["parent_session_id"] = metadata["parent_session_id"]
    if metadata.get("parent_session_file"):
        yaml_data["parent_session_file"] = metadata["parent_session_file"]
    if metadata.get("original_session_id"):
        yaml_data["original_session_id"] = metadata["original_session_id"]

    # First and last messages
    if metadata.get("first_msg"):
        yaml_data["first_msg"] = metadata["first_msg"]
    if metadata.get("last_msg"):
        yaml_data["last_msg"] = metadata["last_msg"]

    # Trim stats (only for trimmed sessions)
    if metadata.get("trim_stats"):
        yaml_data["trim_stats"] = metadata["trim_stats"]

    yaml_str = yaml.dump(yaml_data, default_flow_style=False, sort_keys=False)
    return f"---\n{yaml_str}---\n"


def export_conversation_content(session_file: Path, agent: str) -> str:
    """
    Export conversation content (without YAML front matter).

    Reuses existing export logic from export_claude_session / export_codex_session.

    Args:
        session_file: Path to session file
        agent: Agent type ('claude' or 'codex')

    Returns:
        Formatted conversation content
    """
    output = StringIO()

    if agent == "claude":
        from claude_code_tools.export_claude_session import export_session_to_markdown

        export_session_to_markdown(session_file, output)
    else:
        from claude_code_tools.export_codex_session import export_session_to_markdown

        export_session_to_markdown(session_file, output)

    return output.getvalue()


def export_with_yaml_frontmatter(
    session_file: Path,
    output_path: Path,
    agent: str,
    include_original_lineage: bool = True,
) -> dict[str, Any]:
    """
    Export a session with YAML front matter.

    Creates an export file with:
    1. YAML front matter containing all metadata
    2. Conversation content

    Args:
        session_file: Path to session JSONL file
        output_path: Path for output file
        agent: Agent type ('claude' or 'codex')
        include_original_lineage: If True, trace back to find original session ID

    Returns:
        Metadata dict that was written to YAML
    """
    # Extract metadata
    metadata = extract_session_metadata(session_file, agent)

    # Find original session ID if this is a derived session
    if include_original_lineage and metadata.get("derivation_type"):
        original_id = find_original_session_id(session_file)
        if original_id:
            metadata["original_session_id"] = original_id

    # Generate YAML front matter
    yaml_frontmatter = generate_yaml_frontmatter(metadata)

    # Export conversation content
    content = export_conversation_content(session_file, agent)

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write output file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(yaml_frontmatter)
        f.write("\n")
        f.write(content)

    return metadata


def parse_exported_session(export_path: Path) -> tuple[dict[str, Any], str]:
    """
    Parse an exported session file with YAML front matter.

    Args:
        export_path: Path to exported .txt file

    Returns:
        Tuple of (metadata_dict, conversation_content)

    Raises:
        ValueError: If file doesn't have valid YAML front matter
        ImportError: If pyyaml is not installed
    """
    _require_yaml()

    content = export_path.read_text(encoding="utf-8")

    if not content.startswith("---\n"):
        raise ValueError("Export file does not start with YAML delimiter")

    # Find closing delimiter
    end_idx = content.find("\n---\n", 4)
    if end_idx == -1:
        raise ValueError("No closing YAML delimiter found")

    yaml_str = content[4:end_idx]
    metadata = yaml.safe_load(yaml_str)

    conversation = content[end_idx + 5:]  # Skip "\n---\n"

    return metadata, conversation
