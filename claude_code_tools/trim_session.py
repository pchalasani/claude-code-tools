#!/usr/bin/env python3
"""
Trim CLI agent JSONL session files to reduce size.

This script processes JSONL session logs from Claude Code or Codex and
trims content to reduce file size while preserving conversation flow:
- Replaces large tool results with placeholder text
- Optionally trims assistant messages
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, Tuple

from . import trim_session_claude as claude_processor
from . import trim_session_codex as codex_processor
from .session_utils import get_claude_home, resolve_session_path
from .session_lineage import get_full_lineage_chain


def inject_lineage_into_first_user_message(
    output_file: Path,
    input_file: Path,
    agent: str,
    current_derivation_type: str = "trimmed",
) -> None:
    """
    Inject parent session lineage information into the first user message.

    This helps the agent understand the session history and refer back to
    parent sessions for full context when needed.

    Args:
        output_file: Path to the trimmed output session file
        input_file: Path to the original input session file
        agent: Agent type ('claude' or 'codex')
        current_derivation_type: How this session was derived ("trimmed" or
            "continued"/"rolled over")
    """
    # Get lineage chain (newest first, ending with original)
    try:
        lineage_chain = get_full_lineage_chain(input_file)
    except Exception:
        # If lineage tracing fails, skip injection
        return

    # Only inject if there's lineage history (more than just the input file)
    if len(lineage_chain) <= 1:
        return

    # Build lineage message - reverse to get chronological order (oldest first)
    chronological_chain = list(reversed(lineage_chain))

    # Map internal derivation types to user-friendly names
    def friendly_type(dtype: str) -> str:
        if dtype == "continued":
            return "rolled over"
        return dtype

    # Build numbered list with relationships
    file_lines = []
    for i, (path, derivation_type) in enumerate(chronological_chain):
        if derivation_type == "original":
            file_lines.append(f"  {i+1}. {path} (original)")
        else:
            parent_num = i  # previous item in 1-indexed list
            friendly = friendly_type(derivation_type)
            file_lines.append(f"  {i+1}. {path} ({friendly} from {parent_num})")
    file_list = "\n".join(file_lines)

    # Determine how this session was produced
    current_friendly = friendly_type(current_derivation_type)

    # Build context explanation based on derivation type
    if current_derivation_type in ("continued", "rolled over"):
        context_explanation = (
            f"This session was ROLLED OVER from a parent session. Work was "
            f"handed off to this fresh session with a summary of the task. "
            f"The parent session is fully preserved and you can read it for "
            f"detailed context if needed."
        )
    else:
        context_explanation = (
            f"This session was TRIMMED from a parent session. Long messages "
            f"(tool results and assistant responses) were truncated to free "
            f"up context space. Each truncated message shows a marker like:\n"
            f"  [... See line N of /path/to/parent.jsonl for full content]\n"
            f"You can read the parent file at that line number to retrieve "
            f"the full original content."
        )

    lineage_note = (
        f"\n\n[SESSION LINEAGE]\n\n"
        f"{context_explanation}\n\n"
        f"Parent sessions in chronological order (oldest to newest):\n"
        f"{file_list}\n\n"
        f"Use sub-agents to read these files if you need more context.\n\n"
    )

    # Read the output file
    with open(output_file, "r") as f:
        lines = f.readlines()

    # Find and modify the first user message
    modified = False
    for i, line in enumerate(lines):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        if agent == "claude":
            # Claude format: type == "user" with message.content
            if data.get("type") == "user":
                message = data.get("message", {})
                content = message.get("content", [])
                if isinstance(content, list) and content:
                    # Find first text item and prepend lineage note
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            item["text"] = lineage_note + item.get("text", "")
                            modified = True
                            break
                    if not modified and content:
                        # If no text item, add one at the beginning
                        content.insert(0, {"type": "text", "text": lineage_note})
                        modified = True
                elif isinstance(content, str):
                    message["content"] = lineage_note + content
                    modified = True

                if modified:
                    lines[i] = json.dumps(data) + "\n"
                    break

        elif agent == "codex":
            # Codex format: type == "input" or response_item with input payload
            if data.get("type") == "input":
                # Direct input message
                content = data.get("content", "")
                if isinstance(content, str):
                    data["content"] = lineage_note + content
                    modified = True
                    lines[i] = json.dumps(data) + "\n"
                    break
            elif data.get("type") == "response_item":
                payload = data.get("payload", {})
                if payload.get("type") == "message" and payload.get("role") == "user":
                    content = payload.get("content", [])
                    if isinstance(content, list) and content:
                        for item in content:
                            if isinstance(item, dict) and "text" in item:
                                item["text"] = lineage_note + item.get("text", "")
                                modified = True
                                break
                    if modified:
                        lines[i] = json.dumps(data) + "\n"
                        break

    # Write back if modified
    if modified:
        with open(output_file, "w") as f:
            f.writelines(lines)


def update_session_id_in_file(
    file_path: Path,
    new_session_id: str,
    agent: str,
) -> None:
    """
    Update session ID in all lines of a session file.

    This ensures the file content is consistent with its filename UUID.
    Must be called after cloning or smart-trimming a session.

    Args:
        file_path: Path to the session file to update
        new_session_id: The new session ID (should match filename UUID)
        agent: Agent type ('claude' or 'codex')
    """
    # Read all lines
    with open(file_path, "r") as f:
        lines = f.readlines()

    modified_lines = []
    for line in lines:
        line = line.rstrip("\n")
        if not line.strip():
            modified_lines.append(line + "\n")
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            modified_lines.append(line + "\n")
            continue

        if agent == "claude":
            # Claude: sessionId is in every line
            if "sessionId" in data:
                data["sessionId"] = new_session_id
        elif agent == "codex":
            # Codex: session ID is in session_meta payload.id
            if data.get("type") == "session_meta":
                if "payload" in data and "id" in data["payload"]:
                    data["payload"]["id"] = new_session_id

        modified_lines.append(json.dumps(data) + "\n")

    # Write back
    with open(file_path, "w") as f:
        f.writelines(modified_lines)


def is_trimmed_session(session_file: Path) -> bool:
    """
    Check if a session file is a derived session (trimmed or continued).

    Args:
        session_file: Path to session JSONL file.

    Returns:
        True if session has trim_metadata or continue_metadata, False otherwise.
    """
    import json

    if not session_file.exists():
        return False

    try:
        with open(session_file, "r") as f:
            first_line = f.readline().strip()
            if not first_line:
                return False

            data = json.loads(first_line)
            if not isinstance(data, dict):
                return False
            return "trim_metadata" in data or "continue_metadata" in data
    except (json.JSONDecodeError, IOError, UnicodeError):
        return False


def get_session_derivation_type(session_file: Path) -> Optional[str]:
    """
    Get the derivation type of a session file.

    Args:
        session_file: Path to session JSONL file.

    Returns:
        "trimmed", "continued", or None if not a derived session.
    """
    import json

    if not session_file.exists():
        return None

    try:
        with open(session_file, "r") as f:
            first_line = f.readline().strip()
            if not first_line:
                return None

            data = json.loads(first_line)
            if not isinstance(data, dict):
                return None
            if "trim_metadata" in data:
                return "trimmed"
            elif "continue_metadata" in data:
                return "continued"
            return None
    except (json.JSONDecodeError, IOError, UnicodeError):
        return None


def extract_session_info(input_file: Path, agent: str) -> dict:
    """
    Extract session info needed for resuming.

    Args:
        input_file: Path to session JSONL file.
        agent: Agent type ('claude' or 'codex').

    Returns:
        Dict with cwd/project info.
    """
    import json

    with open(input_file, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            if agent == "claude":
                # Extract cwd from any event
                if "cwd" in data:
                    return {"cwd": data["cwd"]}
            elif agent == "codex":
                # Extract cwd from session_meta
                if data.get("type") == "session_meta":
                    payload = data.get("payload", {})
                    if "cwd" in payload:
                        return {"cwd": payload["cwd"]}

    return {"cwd": None}


def _agent_indicator(data: object) -> Optional[str]:
    """Classify one parsed session line by positive format markers.

    Args:
        data: A JSON-decoded line of a session file (any shape).

    Returns:
        "codex" or "claude" if the line positively identifies the
        agent format, else None.
    """
    # Hostile input: a line may parse to a non-dict shape.
    if not isinstance(data, dict):
        return None

    # Codex indicators
    if data.get("type") == "session_meta":
        return "codex"
    if data.get("type") == "response_item" and "payload" in data:
        return "codex"

    # Claude indicators
    if "sessionId" in data:
        return "claude"
    if data.get("type") in ["user", "assistant"] and "message" in data:
        return "claude"

    return None


def detect_agent_strict(input_file: Path) -> Optional[str]:
    """Detect the agent type from POSITIVE indicators only.

    Unlike ``detect_agent`` (which defaults to "claude" when the
    first lines carry no indicator), this scans the whole file and
    refuses to guess: empty files, random text, or JSONL without
    Claude/Codex markers yield None instead of "claude". Callers
    that mutate files (e.g. in-place trimming) must use this.

    Args:
        input_file: Path to session JSONL file.

    Returns:
        "claude" or "codex" on a positive indicator, else None.
    """
    import json

    # Hostile input: a stray invalid-UTF-8 byte must not raise
    # UnicodeDecodeError; an undecodable line simply carries no
    # positive indicator.
    with open(
        input_file, "r", encoding="utf-8", errors="surrogateescape"
    ) as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            indicator = _agent_indicator(data)
            if indicator is not None:
                return indicator
    return None


def detect_agent(input_file: Path) -> str:
    """
    Auto-detect agent type from session file structure.

    Args:
        input_file: Path to session JSONL file.

    Returns:
        "claude" or "codex"
    """
    import json

    # Read first 20 lines to detect structure (surrogateescape: an
    # invalid byte must not crash detection of a hostile file).
    with open(
        input_file, "r", encoding="utf-8", errors="surrogateescape"
    ) as f:
        for i, line in enumerate(f):
            if i >= 20:
                break
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            indicator = _agent_indicator(data)
            if indicator is not None:
                return indicator

    # Default to claude if uncertain
    return "claude"


def create_placeholder(tool_name: str, original_length: int) -> str:
    """
    Create a placeholder string for suppressed content.

    Args:
        tool_name: Name of the tool.
        original_length: Original content length in characters.

    Returns:
        Placeholder string.
    """
    return (
        f"[Results from {tool_name} tool suppressed - "
        f"original content was {original_length:,} characters]"
    )


MIN_TOKEN_SAVINGS = 300  # Minimum tokens saved to consider trim worthwhile


def trim_and_create_session(
    agent: Optional[str],
    input_file: Path,
    target_tools: Optional[Set[str]],
    threshold: int,
    output_dir: Optional[Path] = None,
    trim_assistant_messages: Optional[int] = None,
    min_token_savings: int = MIN_TOKEN_SAVINGS,
) -> dict:
    """
    Trim tool results and assistant messages, creating a new session file.

    Args:
        agent: Agent type ('claude' or 'codex'). If None, auto-detects from file.
        input_file: Path to input JSONL file.
        target_tools: Set of tool names to suppress (None means all).
        threshold: Minimum length threshold for trimming.
        output_dir: Output directory (None = auto-detect based on agent).
        trim_assistant_messages: Optional trimming of assistant messages:
            - Positive N: trim first N assistant messages exceeding threshold
            - Negative N: trim all except last abs(N) assistant messages exceeding threshold
            - None: don't trim assistant messages
        min_token_savings: Minimum tokens saved to consider trim worthwhile.
            If savings are below this, deletes output file and sets nothing_to_trim=True.

    Returns:
        Dict with:
            - session_id: New session UUID (None if nothing_to_trim)
            - output_file: Path to new session file (None if nothing_to_trim)
            - num_tools_trimmed: Number of tool results trimmed
            - num_assistant_trimmed: Number of assistant messages trimmed
            - chars_saved: Characters saved
            - tokens_saved: Estimated tokens saved
            - detected_agent: Detected agent type
            - nothing_to_trim: True if savings below min_token_savings threshold
    """
    import json
    from datetime import datetime, timezone

    # Auto-detect agent if not specified
    if agent is None:
        agent = detect_agent(input_file)

    # Generate session UUID
    session_uuid = str(uuid.uuid4())

    # Determine output directory and filename based on agent
    if agent == "codex":
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%dT%H-%M-%S")
        date_path = now.strftime("%Y/%m/%d")
        output_filename = (
            f"rollout-{timestamp}-{session_uuid}{input_file.suffix}"
        )

        if output_dir:
            final_output_dir = output_dir / date_path
        else:
            # Find sessions root by going up from input file
            sessions_root = input_file.parent.parent.parent.parent
            final_output_dir = sessions_root / date_path

        final_output_dir.mkdir(parents=True, exist_ok=True)
    else:  # claude
        output_filename = f"{session_uuid}{input_file.suffix}"
        final_output_dir = output_dir if output_dir else input_file.parent

    output_path = final_output_dir / output_filename

    # Process the session
    num_tools_trimmed, num_assistant_trimmed, chars_saved = process_session(
        agent,
        input_file,
        output_path,
        target_tools,
        threshold,
        verbose=False,
        new_session_id=session_uuid,
        trim_assistant_messages=trim_assistant_messages,
    )

    # Estimate tokens saved
    tokens_saved = int(chars_saved / 4)

    # Add trim metadata to first line of output file
    metadata_fields = {
        "trim_metadata": {
            "parent_file": str(input_file.absolute()),
            "trimmed_at": datetime.now(timezone.utc).isoformat(),
            "trim_params": {
                "threshold": threshold,
                "tools": list(target_tools) if target_tools else None,
                "trim_assistant_messages": trim_assistant_messages,
            },
            "stats": {
                "num_tools_trimmed": num_tools_trimmed,
                "num_assistant_trimmed": num_assistant_trimmed,
                "tokens_saved": tokens_saved,
            },
        }
    }

    # Read the file and modify first line
    with open(output_path, "r") as f:
        lines = f.readlines()

    if lines:
        try:
            # Parse first line and add metadata fields
            first_line_data = json.loads(lines[0])
            # Remove continue_metadata if present - a session is either trimmed OR
            # continued, not both. The trim_metadata.parent_file preserves ancestry.
            first_line_data.pop("continue_metadata", None)
            first_line_data.update(metadata_fields)
            lines[0] = json.dumps(first_line_data) + "\n"

            # Write back the modified file
            with open(output_path, "w") as f:
                f.writelines(lines)
        except json.JSONDecodeError:
            # If first line is not valid JSON, leave file as-is
            pass

    # Check if savings are worth it
    if tokens_saved < min_token_savings:
        # Not worth trimming - delete output file and return nothing_to_trim flag
        output_path.unlink(missing_ok=True)
        return {
            "session_id": None,
            "output_file": None,
            "num_tools_trimmed": num_tools_trimmed,
            "num_assistant_trimmed": num_assistant_trimmed,
            "chars_saved": chars_saved,
            "tokens_saved": tokens_saved,
            "detected_agent": agent,
            "nothing_to_trim": True,
        }

    # Inject parent session lineage into first user message
    inject_lineage_into_first_user_message(output_path, input_file, agent)

    return {
        "session_id": session_uuid,
        "output_file": str(output_path),
        "num_tools_trimmed": num_tools_trimmed,
        "num_assistant_trimmed": num_assistant_trimmed,
        "chars_saved": chars_saved,
        "tokens_saved": tokens_saved,
        "detected_agent": agent,
        "nothing_to_trim": False,
    }


def process_session(
    agent: str,
    input_file: Path,
    output_file: Path,
    target_tools: Optional[Set[str]],
    threshold: int,
    verbose: bool = True,
    new_session_id: Optional[str] = None,
    trim_assistant_messages: Optional[int] = None,
) -> Tuple[int, int, int]:
    """
    Process session file and trim tool results and assistant messages.

    Args:
        agent: Agent type ('claude' or 'codex').
        input_file: Path to input JSONL file.
        output_file: Path to output JSONL file.
        target_tools: Set of tool names to suppress (None means all).
        threshold: Minimum length threshold for trimming.
        verbose: Whether to print progress messages.
        new_session_id: Optional new session ID to replace in session metadata.
        trim_assistant_messages: Optional trimming of assistant messages (see trim_and_create_session).

    Returns:
        Tuple of (num_tools_trimmed, num_assistant_trimmed, chars_saved).
    """
    if verbose:
        print("Building tool name mapping...", file=sys.stderr)

    if agent == "claude":
        tool_map = claude_processor.build_tool_name_mapping(input_file)
        if verbose:
            print(
                f"Found {len(tool_map)} tool invocations", file=sys.stderr
            )
            print("Processing session...", file=sys.stderr)

        return claude_processor.process_claude_session(
            input_file,
            output_file,
            tool_map,
            target_tools,
            threshold,
            create_placeholder,
            new_session_id=new_session_id,
            trim_assistant_messages=trim_assistant_messages,
        )
    elif agent == "codex":
        tool_map = codex_processor.build_tool_name_mapping(input_file)
        if verbose:
            print(
                f"Found {len(tool_map)} tool invocations", file=sys.stderr
            )
            print("Processing session...", file=sys.stderr)

        return codex_processor.process_codex_session(
            input_file,
            output_file,
            tool_map,
            target_tools,
            threshold,
            create_placeholder,
            new_session_id=new_session_id,
            trim_assistant_messages=trim_assistant_messages,
        )
    else:
        raise ValueError(f"Unknown agent type: {agent}")


def main() -> None:
    """Parse arguments and process the JSONL file."""
    parser = argparse.ArgumentParser(
        description="Trim JSONL session files to reduce size.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Trim all tool results over 500 chars (auto-detects agent)
  %(prog)s session.jsonl

  # Trim only specific tools
  %(prog)s session.jsonl --tools bash,read,edit

  # Use custom length threshold
  %(prog)s session.jsonl --len 1000

  # Trim first 100 assistant messages over threshold
  %(prog)s session.jsonl --trim-assistant-messages 100

  # Trim all assistant messages except last 10 over threshold
  %(prog)s session.jsonl --trim-assistant-messages -10

  # Manually specify agent (usually not needed)
  %(prog)s session.jsonl --agent codex

  # Custom output directory
  %(prog)s session.jsonl --output-dir /tmp
        """,
    )

    parser.add_argument(
        "input_file",
        nargs='?',
        help="Session file path or session ID (optional - uses $CLAUDE_SESSION_ID if not provided)"
    )
    parser.add_argument(
        "--agent",
        "-a",
        choices=["claude", "codex"],
        help="Agent type: claude or codex (auto-detected if not specified)",
    )
    parser.add_argument(
        "--tools",
        "-t",
        help="Comma-separated list of tool names to trim "
        "(e.g., 'bash,read,edit'). If not specified, all tools are "
        "candidates for trimming.",
    )
    parser.add_argument(
        "--len",
        "-l",
        type=int,
        default=500,
        help="Minimum length threshold in characters for trimming "
        "(default: 500)",
    )
    parser.add_argument(
        "--trim-assistant-messages",
        type=int,
        help="Trim assistant messages: positive N trims first N messages "
        "exceeding threshold, negative N trims all except last abs(N) "
        "messages exceeding threshold",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        help="Output directory (default: same as input file)",
    )
    parser.add_argument(
        "--claude-home",
        type=str,
        help="Path to Claude home directory (default: ~/.claude)"
    )

    args = parser.parse_args()

    # Handle input file resolution
    if args.input_file is None:
        # Try to get session ID from environment variable
        session_id = os.environ.get('CLAUDE_SESSION_ID')
        if not session_id:
            print(f"Error: No session file provided and CLAUDE_SESSION_ID not set", file=sys.stderr)
            print(f"Usage: trim-session <session-file-or-id> or run from within Claude Code with !trim-session", file=sys.stderr)
            sys.exit(1)

        # Reconstruct Claude Code session file path
        cwd = os.getcwd()
        base_dir = get_claude_home(args.claude_home)
        encoded_path = cwd.replace("/", "-")
        claude_project_dir = base_dir / "projects" / encoded_path
        input_path = claude_project_dir / f"{session_id}.jsonl"

        if not input_path.exists():
            print(f"Error: Session file not found: {input_path}", file=sys.stderr)
            print(f"(Reconstructed from CLAUDE_SESSION_ID={session_id})", file=sys.stderr)
            sys.exit(1)

        print(f"📋 Using current Claude Code session: {session_id}")
    else:
        # Resolve session ID or path to full path
        try:
            input_path = resolve_session_path(args.input_file, claude_home=args.claude_home)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    # Auto-detect agent and warn if user specified wrong one
    detected_agent = detect_agent(input_path)
    if args.agent and args.agent != detected_agent:
        print(
            f"⚠️  Detected {detected_agent} session, ignoring --agent {args.agent} argument",
            file=sys.stderr,
        )
    agent_to_use = detected_agent

    # Parse tool names
    target_tools = None
    if args.tools:
        target_tools = {
            tool.strip().lower() for tool in args.tools.split(",")
        }
        print(
            f"Trimming tools: {', '.join(sorted(target_tools))}",
            file=sys.stderr,
        )
    else:
        print(
            "Trimming all tools (no --tools specified)",
            file=sys.stderr,
        )

    print(f"Agent: {agent_to_use}", file=sys.stderr)
    print(f"Length threshold: {args.len} characters", file=sys.stderr)

    if args.trim_assistant_messages:
        if args.trim_assistant_messages > 0:
            print(
                f"Trimming first {args.trim_assistant_messages} assistant messages "
                f"exceeding threshold",
                file=sys.stderr,
            )
        else:
            print(
                f"Trimming all assistant messages except last "
                f"{abs(args.trim_assistant_messages)} exceeding threshold",
                file=sys.stderr,
            )

    # Process the file using helper function
    print(f"\nInput: {input_path}", file=sys.stderr)

    output_dir = Path(args.output_dir) if args.output_dir else None
    result = trim_and_create_session(
        agent_to_use,
        input_path,
        target_tools,
        args.len,
        output_dir,
        args.trim_assistant_messages,
    )

    # Print statistics
    print("\n" + "=" * 70)
    print("TRIM SUMMARY")
    print("=" * 70)
    print(f"Agent: {result['detected_agent']}")
    print(f"Tool results trimmed: {result['num_tools_trimmed']}")
    print(f"Assistant messages trimmed: {result['num_assistant_trimmed']}")
    print(f"Characters saved: {result['chars_saved']:,}")
    print(f"Estimated tokens saved: {result['tokens_saved']:,}")
    print("")
    print(f"Output file: {result['output_file']}")
    print("")
    print("Session UUID:")
    print(result["session_id"])
    print("=" * 70)

    # Extract session info for resuming
    session_info = extract_session_info(input_path, result["detected_agent"])
    cwd = session_info.get("cwd")

    # Construct resume command based on agent type
    agent_name = result["detected_agent"]
    session_id = result["session_id"]
    output_file = result["output_file"]

    if agent_name == "claude":
        # Claude uses --resume with session ID
        if cwd:
            resume_cmd = f"cd {cwd} && claude --resume {session_id}"
        else:
            resume_cmd = f"claude --resume {session_id}"
        resume_args = ["claude", "--resume", session_id]
    else:  # codex
        # Codex uses 'resume' subcommand with session ID
        if cwd:
            resume_cmd = f"cd {cwd} && codex resume {session_id}"
        else:
            resume_cmd = f"codex resume {session_id}"
        resume_args = ["codex", "resume", session_id]

    # Interactive prompt
    print("\n")
    try:
        response = input("Resume this session now? [Y/n]: ").strip().lower()
        if response in ["n", "no"]:
            print(f"\nTo resume later, run:")
            print(f"  {resume_cmd}")
        else:
            # Default to yes (empty input or "y"/"yes")
            print(f"\n🚀 Resuming session...\n")
            import subprocess

            # Change to cwd if specified, then run the agent
            if cwd:
                os.chdir(cwd)

            subprocess.run(resume_args)
    except (KeyboardInterrupt, EOFError):
        print(f"\n\nTo resume later, run:")
        print(f"  {resume_cmd}")


if __name__ == "__main__":
    main()
