"""Utility functions for working with Claude Code and Codex sessions."""

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, List, Tuple


def parse_flexible_timestamp(ts_str: str, is_upper_bound: bool = False) -> float:
    """
    Parse a flexible timestamp string into a Unix timestamp.

    Supports date formats:
        20251120           - YYYYMMDD
        2025-11-20         - YYYY-MM-DD (ISO)
        11/20/25           - MM/DD/YY
        11/20/2025         - MM/DD/YYYY

    Supports optional time suffix (T or space separator):
        ...T16:45:23 or ... 16:45:23  - full time
        ...T16:45 or ... 16:45        - without seconds
        ...T16 or ... 16              - hour only

    Args:
        ts_str: Timestamp string in one of the supported formats
        is_upper_bound: If True (for --before), fill missing parts with max values
                       (23:59:59). If False (for --after), fill with min values
                       (00:00:00).

    Returns:
        Unix timestamp (float)

    Raises:
        ValueError: If the timestamp format is invalid
    """
    ts_str = ts_str.strip()

    # Split date and time parts (separator: T or space)
    time_part = None
    if 'T' in ts_str:
        date_str, time_part = ts_str.split('T', 1)
    elif ' ' in ts_str:
        date_str, time_part = ts_str.split(' ', 1)
    else:
        date_str = ts_str

    # Parse date part - try multiple formats
    year, month, day = None, None, None

    # YYYYMMDD
    if re.match(r'^\d{8}$', date_str):
        year = int(date_str[:4])
        month = int(date_str[4:6])
        day = int(date_str[6:8])
    # YYYY-MM-DD (ISO)
    elif re.match(r'^\d{4}-\d{1,2}-\d{1,2}$', date_str):
        parts = date_str.split('-')
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    # MM/DD/YY or MM/DD/YYYY
    elif re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$', date_str):
        parts = date_str.split('/')
        month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
        if year < 100:
            year += 2000  # 25 -> 2025
    else:
        raise ValueError(
            f"Invalid date format: {date_str}. "
            "Expected: YYYYMMDD, YYYY-MM-DD, MM/DD/YY, or MM/DD/YYYY"
        )

    # Parse time part if present
    hour, minute, second = None, None, None
    if time_part:
        time_match = re.match(r'^(\d{1,2})(?::(\d{2})(?::(\d{2}))?)?$', time_part)
        if not time_match:
            raise ValueError(
                f"Invalid time format: {time_part}. "
                "Expected: HH, HH:MM, or HH:MM:SS"
            )
        hour = int(time_match.group(1))
        minute = int(time_match.group(2)) if time_match.group(2) else None
        second = int(time_match.group(3)) if time_match.group(3) else None

    # Fill missing time components based on bound type
    if hour is None:
        hour = 23 if is_upper_bound else 0
        minute = 59 if is_upper_bound else 0
        second = 59 if is_upper_bound else 0
    else:
        if minute is None:
            minute = 59 if is_upper_bound else 0
            second = 59 if is_upper_bound else 0
        elif second is None:
            second = 59 if is_upper_bound else 0

    dt = datetime(year, month, day, hour, minute, second)
    return dt.timestamp()


def filter_sessions_by_time(
    sessions: list,
    before: Optional[str] = None,
    after: Optional[str] = None,
    time_key: str = "mod_time",
    time_index: Optional[int] = None,
) -> list:
    """
    Filter sessions by time bounds.

    Args:
        sessions: List of sessions (dicts or tuples)
        before: Upper bound timestamp string (inclusive)
        after: Lower bound timestamp string (inclusive)
        time_key: Key to use for dict sessions (default: "mod_time")
        time_index: Index to use for tuple sessions (if None, uses time_key for dicts)

    Returns:
        Filtered list of sessions
    """
    if not before and not after:
        return sessions

    before_ts = parse_flexible_timestamp(before, is_upper_bound=True) if before else None
    after_ts = parse_flexible_timestamp(after, is_upper_bound=False) if after else None

    def get_time(session):
        if time_index is not None:
            return session[time_index]
        return session.get(time_key, 0)

    result = []
    for s in sessions:
        t = get_time(s)
        if before_ts is not None and t > before_ts:
            continue
        if after_ts is not None and t < after_ts:
            continue
        result.append(s)

    return result


def is_agent_available(agent: str) -> bool:
    """
    Check if a coding agent is available on this system.

    Checks two conditions (either one is sufficient):
    1. The agent command exists in PATH (e.g., 'claude' or 'codex')
    2. The agent config directory exists (e.g., ~/.claude or ~/.codex)

    Args:
        agent: Agent name ('claude' or 'codex')

    Returns:
        True if the agent is available, False otherwise
    """
    agent = agent.lower()

    # Check if command exists in PATH
    command = "claude" if agent == "claude" else "codex"
    if shutil.which(command):
        return True

    # Check if config directory exists
    config_dir = Path.home() / f".{agent}"
    if config_dir.exists() and config_dir.is_dir():
        return True

    return False


def get_claude_home(cli_arg: Optional[str] = None) -> Path:
    """
    Get Claude home directory with proper precedence.

    Precedence order:
    1. CLI argument (if provided)
    2. CLAUDE_CONFIG_DIR environment variable (if set)
    3. Default ~/.claude

    Args:
        cli_arg: Optional CLI argument value for --claude-home

    Returns:
        Path to Claude home directory
    """
    # CLI argument has highest priority
    if cli_arg:
        return Path(cli_arg).expanduser()

    # Check environment variable
    env_var = os.environ.get('CLAUDE_CONFIG_DIR')
    if env_var:
        return Path(env_var).expanduser()

    # Default fallback
    return Path.home() / ".claude"


def get_codex_home(cli_arg: Optional[str] = None) -> Path:
    """
    Get Codex home directory.

    Args:
        cli_arg: Optional CLI argument value for --codex-home

    Returns:
        Path to Codex home directory (default: ~/.codex)
    """
    if cli_arg:
        return Path(cli_arg).expanduser()
    return Path.home() / ".codex"


def encode_claude_project_path(project_path: str) -> str:
    """
    Encode a project path to Claude's directory naming format.

    Claude Code replaces certain characters when creating project directories.
    This function replicates that encoding to ensure path reconstruction matches.

    Known character replacements:
    - '/' → '-' (path separators)
    - '_' → '-' (underscores, common in temp directories)
    - '.' → '-' (dots, e.g., .claude-trace directories)

    Args:
        project_path: Absolute path to project directory
            (e.g., /Users/foo/Git/my_project)

    Returns:
        Encoded path suitable for Claude's projects directory
            (e.g., -Users-foo-Git-my-project)
    """
    return project_path.replace("/", "-").replace("_", "-").replace(".", "-")


def resolve_session_path(
    session_id_or_path: str, claude_home: Optional[str] = None
) -> Path:
    """
    Resolve a session ID or path to a full file path.

    Supports partial session ID matching. If multiple sessions match a partial
    ID, shows an error message with all matches.

    Args:
        session_id_or_path: Either a full path, full session ID, or partial
            session ID
        claude_home: Optional custom Claude home directory (defaults to
            ~/.claude or $CLAUDE_CONFIG_DIR)

    Returns:
        Resolved Path object

    Raises:
        FileNotFoundError: If session cannot be found
        ValueError: If partial ID matches multiple sessions
        SystemExit: If multiple matches found (exits with error message)
    """
    path = Path(session_id_or_path)

    # If it's already a valid path, use it
    if path.exists():
        return path

    # Otherwise, treat it as a session ID (full or partial) and try to find it
    session_id = session_id_or_path.strip()

    # Search ALL Claude project directories globally
    base_dir = get_claude_home(claude_home)
    projects_dir = base_dir / "projects"

    claude_matches: List[Path] = []
    if projects_dir.exists():
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            # Look for exact match first (fast path)
            exact_path = project_dir / f"{session_id}.jsonl"
            if exact_path.exists():
                return exact_path
            # Collect partial matches
            for jsonl_file in project_dir.glob("*.jsonl"):
                if session_id in jsonl_file.stem:
                    claude_matches.append(jsonl_file)

    # Try Codex path - search through sessions directory
    codex_home = Path.home() / ".codex"
    sessions_dir = codex_home / "sessions"

    codex_matches: List[Path] = []
    if sessions_dir.exists():
        for jsonl_file in sessions_dir.rglob("*.jsonl"):
            # Extract session ID from Codex filename (format: rollout-...-UUID.jsonl)
            if session_id in jsonl_file.stem:
                codex_matches.append(jsonl_file)

    # Combine all matches
    all_matches = claude_matches + codex_matches

    if len(all_matches) == 0:
        # Not found anywhere
        raise FileNotFoundError(
            f"Session '{session_id}' not found in Claude Code "
            f"({projects_dir}) or Codex ({sessions_dir}) directories"
        )
    elif len(all_matches) == 1:
        # Single match - perfect!
        return all_matches[0]
    else:
        # Multiple matches - show user the options
        print(
            f"Error: Multiple sessions match '{session_id}':",
            file=sys.stderr
        )
        print(file=sys.stderr)
        for i, match in enumerate(all_matches, 1):
            print(f"  {i}. {match.stem}", file=sys.stderr)
        print(file=sys.stderr)
        print(
            "Please use a more specific session ID to uniquely identify the session.",
            file=sys.stderr
        )
        sys.exit(1)


def get_current_session_id(claude_home: Optional[str] = None) -> Optional[str]:
    """
    Get the session ID of the currently active Claude Code or Codex session.

    Uses the Tantivy search index to find the most recently modified session
    for the current working directory.

    Args:
        claude_home: Optional custom Claude home directory (default: ~/.claude)

    Returns:
        Session ID (UUID or timestamped name) if found, None otherwise
    """
    from claude_code_tools.search_index import get_latest_session_from_index

    cwd = os.getcwd()

    # Query the index for the latest session in this cwd
    result = get_latest_session_from_index(cwd=cwd)
    if result:
        return result.get("session_id")

    return None


def get_latest_session_for_cwd(
    cwd: Optional[str] = None,
    branch: Optional[str] = None,
    agent: Optional[str] = None,
) -> Optional[dict]:
    """
    Get the latest session for the given cwd/branch/agent from the index.

    Args:
        cwd: Working directory to filter by (default: current directory)
        branch: Git branch to filter by (optional)
        agent: Agent type to filter by ("claude" or "codex", optional)

    Returns:
        Dict with session info including 'session_id', 'export_path', etc.
        or None if no matching session found
    """
    from claude_code_tools.search_index import get_latest_session_from_index

    if cwd is None:
        cwd = os.getcwd()

    return get_latest_session_from_index(cwd=cwd, branch=branch, agent=agent)


def display_lineage(
    session_file: Path,
    agent_type: str,
    claude_home: Optional[str] = None,
    codex_home: Optional[str] = None,
    verbose: bool = False,
) -> List[Path]:
    """
    Trace and display continuation lineage for a session.

    This function:
    1. Traces the continuation lineage (all parent sessions)
    2. Displays the lineage chain to the user
    3. Returns session file paths for use by continue functions

    Args:
        session_file: Path to the session file to analyze
        agent_type: 'claude' or 'codex' (kept for compatibility, not used)
        claude_home: Optional custom Claude home directory (not used)
        codex_home: Optional custom Codex home directory (not used)
        verbose: If True, show detailed progress

    Returns:
        List of session file paths in chronological order (oldest first),
        including the current session at the end.
    """
    from claude_code_tools.session_lineage import get_full_lineage_chain

    # Get and display continuation lineage
    print("Step 1: Tracing session lineage...")

    try:
        # Get full lineage chain (newest first, ending with original)
        lineage_chain = get_full_lineage_chain(session_file)

        if len(lineage_chain) > 1:
            print(f"✅ Found {len(lineage_chain)} session(s) in lineage:")
            # Use shared formatter (expects chronological order)
            chronological = list(reversed(lineage_chain))
            print(build_session_file_list(chronological))
            print()
        else:
            print("✅ This is the original session (no parent chain)")
            print()

        # Collect all session files in chronological order (oldest first)
        # lineage_chain is newest-first, so reverse it
        all_session_files = [path for path, _ in reversed(lineage_chain)]
        return all_session_files

    except Exception as e:
        print(f"⚠️  Warning: Could not trace lineage: {e}", file=sys.stderr)
        # Fall back to just the current session
        return [session_file]


def continue_with_options(
    session_file_path: str,
    current_agent: str,
    claude_home: Optional[str] = None,
    codex_home: Optional[str] = None,
    preset_agent: Optional[str] = None,
    preset_prompt: Optional[str] = None,
    rollover_type: str = "context",
) -> None:
    """
    Unified continue flow with proper timing.

    This function provides the complete continue experience:
    1. Export current session
    2. Display lineage (so user sees full history)
    3. Prompt for agent choice (unless preset_agent provided)
    4. Prompt for custom instructions (unless preset_prompt provided)
    5. Execute continuation with chosen options

    Args:
        session_file_path: Path to the session file to continue
        current_agent: Agent type of the session ('claude' or 'codex')
        claude_home: Optional custom Claude home directory
        codex_home: Optional custom Codex home directory
        preset_agent: If provided, skip agent choice prompt and use this agent
        preset_prompt: If provided, skip custom prompt and use this
        rollover_type: "quick" for fast resume (just lineage), "context" for
            full context recovery with sub-agents (default)
    """
    session_file = Path(session_file_path)

    print("\n🔄 Starting rollover to fresh session...")
    print()

    # Step 1: Display lineage and collect session files
    # This allows user to make informed decisions
    all_session_files = display_lineage(
        session_file,
        current_agent,
        claude_home=claude_home,
        codex_home=codex_home,
    )

    # Step 3: Prompt for agent choice (unless preset or other agent unavailable)
    other_agent_name = "codex" if current_agent == "claude" else "claude"

    if preset_agent:
        continue_agent = preset_agent.lower()
        print(f"ℹ️  Using specified agent: {continue_agent.upper()}")
    elif not is_agent_available(other_agent_name):
        # Other agent not available, use current agent without prompting
        continue_agent = current_agent
        print(f"ℹ️  Rolling over with {current_agent.upper()}")
    else:
        # Both agents available, offer choice
        print(f"Current session is from: {current_agent.upper()}")
        print("Which agent should take over the work?")
        print(f"1. {current_agent.upper()} (default - same agent)")
        print(f"2. {other_agent_name.upper()} (cross-agent)")
        print()

        try:
            choice = input(
                f"Enter choice [1-2] (or Enter for {current_agent.upper()}): "
            ).strip()
            if not choice or choice == "1":
                continue_agent = current_agent
            elif choice == "2":
                continue_agent = other_agent_name
            else:
                print("Invalid choice, using default.")
                continue_agent = current_agent
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return

    print(f"\nℹ️  Rolling over with {continue_agent.upper()}")

    # Step 4: Prompt for custom instructions (unless preset provided)
    # preset_prompt can be:
    #   None - not from Node UI, so prompt the user
    #   "" (empty string) - from Node UI, user chose to skip, don't prompt
    #   "some text" - from Node UI, user provided custom prompt
    if preset_prompt is not None:
        # From Node UI - use whatever was provided (even empty means skip)
        custom_prompt = preset_prompt if preset_prompt else None
        if custom_prompt:
            print(f"ℹ️  Using custom prompt: {custom_prompt[:50]}...")
        else:
            print("ℹ️  No custom summarization instructions")
    else:
        # Not from Node UI - prompt the user
        print("\nEnter custom summarization instructions (or press Enter to skip):")
        try:
            custom_prompt = input("> ").strip() or None
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return

    # Step 5: Execute continuation with precomputed session files
    quick_rollover = rollover_type == "quick"
    if continue_agent == "claude":
        from claude_code_tools.claude_continue import claude_continue
        claude_continue(
            str(session_file),
            claude_home=claude_home,
            verbose=False,
            custom_prompt=custom_prompt,
            precomputed_session_files=all_session_files,
            quick_rollover=quick_rollover,
        )
    else:
        from claude_code_tools.codex_continue import codex_continue
        codex_continue(
            str(session_file),
            codex_home=codex_home,
            verbose=False,
            custom_prompt=custom_prompt,
            precomputed_session_files=all_session_files,
            quick_rollover=quick_rollover,
        )


def detect_agent_from_path(file_path: Path) -> Optional[str]:
    """
    Auto-detect agent type from file path.

    Args:
        file_path: Path to session file

    Returns:
        'claude', 'codex', or None if cannot detect
    """
    path_str = str(file_path.absolute())

    if "/.claude/" in path_str or path_str.startswith(
        str(Path.home() / ".claude")
    ):
        return "claude"
    elif "/.codex/" in path_str or path_str.startswith(
        str(Path.home() / ".codex")
    ):
        return "codex"

    return None


def detect_agent_from_content(
    file_path: Path,
    max_lines: Optional[int] = 25,
    max_line_chars: int = 1_000_000,
) -> Optional[str]:
    """Detect agent type by sniffing a session file's JSONL content.

    Used as a fallback when the file path lies outside the default
    agent home directories (e.g. a custom --codex-home or a copied
    rollout file).

    Reads with a bounded ``readline`` loop so a file with one enormous
    unterminated line never materializes more than ``max_line_chars``
    characters at a time; oversized lines are discarded in chunks and
    treated as unrecognized. Pathologically nested JSON (RecursionError
    from the parser) is likewise skipped.

    Args:
        file_path: Path to a session JSONL file.
        max_lines: Number of leading non-empty lines to inspect, or
            None to stream through the ENTIRE file (still memory
            bounded) until a verdict is reached.
        max_line_chars: Maximum characters held for any single line.

    Returns:
        'claude', 'codex', or None if the format is not recognized.
    """
    codex_line_types = {
        "session_meta",
        "response_item",
        "event_msg",
        "turn_context",
        "world_state",
        "compacted",
    }
    # Known values of the legacy 2025 rollout ``record_type`` field.
    # Only these mark a line as Codex: presence of the key alone must
    # NOT (e.g. a Claude record carrying ``record_type: null`` is
    # still a Claude record).
    codex_record_types = {"state"}
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            checked = 0
            while max_lines is None or checked < max_lines:
                line = f.readline(max_line_chars + 1)
                if not line:
                    break
                if len(line) > max_line_chars and not line.endswith("\n"):
                    # Oversized line: discard its remainder in bounded
                    # chunks and skip it.
                    while True:
                        rest = f.readline(max_line_chars + 1)
                        if not rest or rest.endswith("\n"):
                            break
                    checked += 1
                    continue
                line = line.strip()
                if not line:
                    continue
                checked += 1
                try:
                    data = json.loads(line)
                except (ValueError, RecursionError):
                    # ValueError covers json.JSONDecodeError plus
                    # non-decode failures like integer literals over
                    # Python's digit limit.
                    continue
                if not isinstance(data, dict):
                    continue
                # Recognized line types take priority: a Codex record
                # that happens to carry a (null/copied) sessionId key
                # must not be misclassified as Claude. Records whose
                # ``type`` is a non-string shape (list/dict/number)
                # are malformed: they are skipped entirely -- neither
                # crashing the set-membership check below nor letting
                # a stray sessionId classify garbage as Claude.
                line_type = data.get("type")
                if line_type is not None and not isinstance(
                    line_type, str
                ):
                    continue
                if line_type in codex_line_types:
                    return "codex"
                # Legacy record_type lines: only a RECOGNIZED string
                # value is Codex evidence. Field presence alone (or
                # an unknown/null value) must not shadow stronger
                # structural markers like a Claude sessionId below.
                record_type = data.get("record_type")
                if (
                    isinstance(record_type, str)
                    and record_type in codex_record_types
                ):
                    return "codex"
                if (
                    "id" in data
                    and "timestamp" in data
                    and ("git" in data or "instructions" in data)
                ):
                    # Legacy 2025 codex rollout header line.
                    return "codex"
                session_id = data.get("sessionId")
                if isinstance(session_id, str) and session_id:
                    return "claude"
    except OSError:
        return None
    return None


def is_valid_session(filepath: Path) -> bool:
    """
    Check if a session file is a valid resumable session (WHITELIST approach).

    Supports both Claude Code and Codex session formats:
    - Claude: user, assistant, tool_result, tool_use (with sessionId)
    - Codex: event_msg, response_item, turn_context (with session_meta)

    Sessions containing ONLY metadata types (file-history-snapshot, queue-operation,
    session_meta alone) are invalid.

    Args:
        filepath: Path to session JSONL file.

    Returns:
        True if session contains at least one resumable message, False otherwise.
    """
    if not filepath.exists():
        return False

    # Whitelist of resumable message types
    # Claude Code types (require sessionId)
    claude_valid_types = {"user", "assistant", "tool_result", "tool_use", "system"}
    # Codex types (conversation content types)
    codex_valid_types = {"event_msg", "response_item", "turn_context"}

    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            has_any_content = False

            for line in f:
                line = line.strip()
                if not line:
                    continue

                has_any_content = True

                try:
                    data = json.loads(line)
                except (ValueError, RecursionError):
                    # Skip malformed/unparseable JSON lines (including
                    # ValueError from oversized integer literals) and
                    # continue checking other lines.
                    continue

                # Valid JSONL records like null / [] / "x" / 1 are not
                # session lines: skip them instead of crashing.
                if not isinstance(data, dict):
                    continue

                # Only string types are recognized: an unhashable
                # list/dict value must be skipped, not crash the
                # set-membership tests below.
                entry_type = data.get("type")
                if not isinstance(entry_type, str):
                    continue

                # Claude Code: valid type with a non-empty string
                # sessionId (arrays/objects/numbers are not session
                # ids and must not validate a record).
                session_id = data.get("sessionId")
                if (
                    entry_type in claude_valid_types
                    and isinstance(session_id, str)
                    and session_id
                ):
                    return True

                # Codex: valid conversation content type
                if entry_type in codex_valid_types:
                    return True

            # If we scanned entire file and found no valid message types
            # (only metadata or empty), session is invalid
            return False if has_any_content else False  # Empty file is invalid

    except (OSError, IOError):
        return False  # File read errors indicate invalid file


def is_malformed_session(filepath: Path) -> bool:
    """
    Deprecated: Use is_valid_session() instead.
    Kept for backward compatibility - returns inverse of is_valid_session().

    Returns:
        True if session is malformed/invalid, False if valid.
    """
    return not is_valid_session(filepath)


def extract_cwd_from_session(session_file: Path) -> Optional[str]:
    """
    Extract the working directory (cwd) from a session file.

    Delegates to extract_session_metadata() from export_session.py which properly
    scans the entire file until it finds a valid cwd (handles compacted sessions
    where cwd may not appear until line 20+).

    Args:
        session_file: Path to the session JSONL file

    Returns:
        The cwd string if found, None otherwise
    """
    try:
        from claude_code_tools.export_session import extract_session_metadata

        # Detect agent from path
        path_str = str(session_file)
        agent = "codex" if ".codex" in path_str else "claude"

        metadata = extract_session_metadata(session_file, agent)
        return metadata.get("cwd")
    except Exception:
        return None


def extract_git_branch_claude(session_file: Path) -> Optional[str]:
    """
    Extract git branch from Claude session file.

    Delegates to extract_session_metadata() which properly scans the entire file.
    """
    try:
        from claude_code_tools.export_session import extract_session_metadata

        metadata = extract_session_metadata(session_file, "claude")
        return metadata.get("branch")
    except Exception:
        return None


def extract_session_metadata_codex(session_file: Path) -> Optional[dict]:
    """
    Extract metadata from Codex session file.

    Delegates to extract_session_metadata() for consistency.
    Returns dict with 'cwd' and 'branch' keys for backwards compatibility.
    """
    try:
        from claude_code_tools.export_session import extract_session_metadata

        metadata = extract_session_metadata(session_file, "codex")
        return {
            "cwd": metadata.get("cwd"),
            "branch": metadata.get("branch"),
        }
    except Exception:
        return None


def _iter_named_session_files(
    session_id: str,
    claude_home: Optional[str] = None,
    codex_home: Optional[str] = None,
) -> Iterator[Tuple[str, Path]]:
    """Yield session files whose NAME contains ``session_id``.

    Single shared implementation of the Claude/Codex home traversal
    used by every session-id lookup (:func:`find_session_file` and
    :func:`find_matching_session_files`), so the two cannot diverge.

    The identifier is treated as a LITERAL string: glob
    metacharacters (``* ? [``) are escaped, and an empty or
    whitespace-only identifier matches nothing. Because a session id
    names a single FILE, identifiers containing a path separator
    (``/`` or ``\\``, which ``glob.escape`` cannot neutralize) also
    match nothing rather than becoming multi-component glob patterns
    that could reach into unexpected nested directories. Claude-home
    matches are yielded first, then Codex-home matches, each in
    deterministic sorted order. No content validation happens here —
    callers apply their own per-agent validation.

    Args:
        session_id: Session identifier (full or partial), matched
            literally against file names.
        claude_home: Optional custom Claude home directory.
        codex_home: Optional custom Codex home directory.

    Yields:
        ``(agent, file_path)`` tuples with agent "claude" or "codex".
    """
    import glob as glob_module

    key = session_id.strip()
    if not key or "/" in key or "\\" in key or os.sep in key:
        return
    pattern = f"*{glob_module.escape(key)}*.jsonl"

    claude_base = get_claude_home(claude_home)
    projects_dir = claude_base / "projects"
    if projects_dir.is_dir():
        for project_dir in sorted(projects_dir.iterdir()):
            if project_dir.is_dir():
                for f in sorted(project_dir.glob(pattern)):
                    yield ("claude", f)

    codex_base = get_codex_home(codex_home)
    sessions_dir = codex_base / "sessions"
    if sessions_dir.is_dir():
        # Rollouts live under <year>/<month>/<day>/ directories.
        for f in sorted(sessions_dir.glob(f"*/*/*/{pattern}")):
            yield ("codex", f)


def find_session_file(
    session_id: str,
    claude_home: Optional[str] = None,
    codex_home: Optional[str] = None,
) -> Optional[Tuple[str, Path, str, Optional[str]]]:
    """
    Search for session file by ID in both Claude and Codex homes.

    Consumes the shared traversal in :func:`_iter_named_session_files`
    (partial IDs supported, matched literally) and returns the first
    candidate that passes per-agent validation.

    Args:
        session_id: Session identifier
        claude_home: Optional custom Claude home directory
        codex_home: Optional custom Codex home directory

    Returns:
        Tuple of (agent, file_path, project_path, git_branch) or None
        Note: project_path is the full working directory path, not just the name
    """
    for agent, session_file in _iter_named_session_files(
        session_id, claude_home=claude_home, codex_home=codex_home
    ):
        if agent == "claude":
            # Skip malformed/invalid sessions
            if is_malformed_session(session_file):
                continue
            # Extract actual cwd from session file
            actual_cwd = extract_cwd_from_session(session_file)
            if not actual_cwd:
                # Skip sessions without cwd
                continue
            # Try to get git branch from session file
            git_branch = extract_git_branch_claude(session_file)
            return ("claude", session_file, actual_cwd, git_branch)
        else:
            # Extract metadata from file
            metadata = extract_session_metadata_codex(session_file)
            if metadata:
                project_path = metadata.get("cwd", "")
                git_branch = metadata.get("branch")
                return ("codex", session_file, project_path, git_branch)

    return None


def find_matching_session_files(
    session_id: str,
    claude_home: Optional[str] = None,
    codex_home: Optional[str] = None,
) -> list:
    """Find every VALID session file whose name contains ``session_id``.

    Consumes the SAME shared traversal as :func:`find_session_file`
    (:func:`_iter_named_session_files`: literal matching, escaped glob
    metacharacters, deterministic sorted order) but collects ALL
    matches, so callers can reject ambiguous partial IDs with a clear
    error instead of silently acting on whichever file filesystem
    iteration happened to yield first.

    Every filename match is validated before it is returned
    (mirroring the existing info/copy lookup behavior in
    :func:`find_session_file`): a Claude-home candidate must be a
    valid resumable session per :func:`is_valid_session`, and a
    Codex-home candidate must be recognized as a Codex rollout by
    :func:`detect_agent_from_content` (which also covers the legacy
    2025 format) run as a STREAMING FULL-FILE check
    (``max_lines=None``), so a valid rollout whose leading records
    are unrecognized or oversized is still found via its later
    ``session_meta``/``response_item`` records. Malformed/garbage
    files that merely share a filename fragment therefore never
    produce a false "found" result nor make an otherwise unique
    valid session appear ambiguous.

    Args:
        session_id: Session identifier (full or partial), matched
            literally against file names.
        claude_home: Optional custom Claude home directory.
        codex_home: Optional custom Codex home directory.

    Returns:
        List of ``(agent, file_path)`` tuples, possibly empty.
    """
    matches: list = []
    for agent, f in _iter_named_session_files(
        session_id, claude_home=claude_home, codex_home=codex_home
    ):
        if agent == "claude":
            if is_valid_session(f):
                matches.append(("claude", f))
        elif detect_agent_from_content(f, max_lines=None) == "codex":
            matches.append(("codex", f))
    return matches


def format_session_id_display(
    session_id: str,
    is_trimmed: bool = False,
    is_continued: bool = False,
    is_sidechain: bool = False,
    truncate_length: int = 8,
) -> str:
    """
    Format session ID with annotations for display in find commands.

    Provides consistent session ID formatting across all find commands
    (find, find-claude, find-codex) with standard annotations.

    Args:
        session_id: Full session ID string
        is_trimmed: Whether session is trimmed (adds "(t)")
        is_continued: Whether session is continued (adds "(c)")
        is_sidechain: Whether session is a sub-agent (adds "(sub)")
        truncate_length: Number of characters to show before "..." (default 8)

    Returns:
        Formatted string like "abc123... (t) (sub)"

    Examples:
        >>> format_session_id_display("abc123-def456", is_trimmed=True)
        'abc123... (t)'
        >>> format_session_id_display("abc123-def456", is_sidechain=True, truncate_length=16)
        'abc123-def456... (sub)'
    """
    display = session_id[:truncate_length] + "..."

    if is_trimmed:
        display += " (t)"
    if is_continued:
        display += " (c)"
    if is_sidechain:
        display += " (sub)"

    return display


def mark_session_as_helper(session_file: Path) -> bool:
    """
    Mark a session file as a helper session by adding sessionType metadata.

    Helper sessions are created by programmatic tools (smart-trim, query, etc.)
    and should be excluded from search indexing.

    This adds/updates `"sessionType": "helper"` in the first JSON line of the
    session file.

    Args:
        session_file: Path to the session JSONL file

    Returns:
        True if successfully marked, False otherwise
    """
    if not session_file.exists():
        return False

    try:
        with open(session_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        if not lines:
            return False

        # Parse and modify first line
        first_line = lines[0].strip()
        if not first_line:
            return False

        try:
            data = json.loads(first_line)
            data["sessionType"] = "helper"
            lines[0] = json.dumps(data) + "\n"
        except json.JSONDecodeError:
            return False

        # Write back
        with open(session_file, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        return True

    except (OSError, IOError):
        return False


def default_export_path(
    session_file: Path,
    agent: str,
    base_dir: Optional[Path] = None,
) -> Path:
    """
    Generate default export path for a session.

    Path format: {base_dir}/exported-sessions/{agent}/{session_filename}.txt

    Args:
        session_file: Path to the session file
        agent: Agent type ('claude' or 'codex')
        base_dir: Base directory (defaults to session's project dir, then cwd)

    Returns:
        Path to the export file
    """
    if base_dir is None:
        # Infer base_dir from session file metadata
        if agent == "codex":
            metadata = extract_session_metadata_codex(session_file)
            if metadata and metadata.get("cwd"):
                base_dir = Path(metadata["cwd"])
        else:  # claude
            cwd = extract_cwd_from_session(session_file)
            if cwd:
                base_dir = Path(cwd)

        # Fall back to cwd if inference fails
        if base_dir is None:
            base_dir = Path.cwd()

    agent_dir = "codex" if agent == "codex" else "claude"
    filename = session_file.stem + ".txt"

    return base_dir / "exported-sessions" / agent_dir / filename


def get_session_uuid(filename_or_stem: str) -> str:
    """
    Extract the UUID portion from a session filename or stem.

    Works for both Claude and Codex session filenames:
    - Claude: "abc123de-f456-7890-abcd-ef1234567890.jsonl" -> UUID is the stem
    - Codex: "rollout-2025-01-01T12-00-00-abc123de-f456-7890-abcd-ef1234567890.jsonl"
             -> UUID is the last 36 characters

    Args:
        filename_or_stem: Session filename or stem (with or without .jsonl)

    Returns:
        The UUID portion (36 characters for standard UUIDs, or full stem if shorter)
    """
    # Remove .jsonl extension if present
    stem = filename_or_stem
    if stem.endswith(".jsonl"):
        stem = stem[:-6]

    # UUID is always last 36 chars (format: 8-4-4-4-12 = 36 with dashes)
    # This handles both Claude (stem IS the UUID) and Codex (rollout-timestamp-UUID)
    if len(stem) >= 36:
        return stem[-36:]
    return stem


# =============================================================================
# Rollover Prompt Building Utilities
# =============================================================================
# These functions consolidate the prompt-building logic shared between
# claude_continue.py and codex_continue.py to avoid code duplication.


def friendly_derivation_type(dtype: str) -> str:
    """
    Convert derivation type to user-friendly display string.

    Args:
        dtype: Derivation type from lineage chain (e.g., "continued", "trimmed")

    Returns:
        User-friendly string (e.g., "rollover" instead of "continued")
    """
    if dtype == "continued":
        return "rollover"
    return dtype


def _get_session_timestamps(session_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract created and modified timestamps from a session file.

    Args:
        session_path: Path to the JSONL session file

    Returns:
        Tuple of (created_iso, modified_iso) strings, or None if not found
    """
    created = None
    modified = None

    try:
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)

                    # Get timestamp from message entries (user/assistant types)
                    # These have timestamp at top level
                    ts = data.get("timestamp") or data.get("isoTimestamp")

                    # Also check nested snapshot.timestamp for file-history entries
                    if not ts and isinstance(data.get("snapshot"), dict):
                        ts = data["snapshot"].get("timestamp")

                    if ts:
                        if created is None:
                            created = ts
                        modified = ts  # Keep updating to get the last one

                except json.JSONDecodeError:
                    pass

    except (OSError, IOError):
        pass

    # Fallback to file mtime if no modified timestamp found
    if not modified and session_path.exists():
        mtime = session_path.stat().st_mtime
        modified = datetime.fromtimestamp(mtime).strftime("%Y-%m-%dT%H:%M:%S")

    return created, modified


def _format_time_span(created: Optional[str], modified: Optional[str]) -> str:
    """
    Format the time span between created and modified timestamps.

    Args:
        created: ISO timestamp string for session start
        modified: ISO timestamp string for session end

    Returns:
        Formatted string like "spanning 2 days, last modified 2025-12-10 11:45"
    """
    if not modified:
        return ""

    # Parse modified timestamp for display (convert to local time)
    try:
        # Handle various ISO formats
        mod_str = modified.replace("Z", "+00:00")
        mod_dt = datetime.fromisoformat(mod_str)
        # Convert to local time if timezone-aware
        if mod_dt.tzinfo is not None:
            mod_dt = mod_dt.astimezone()  # Convert to local timezone
        mod_display = mod_dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        mod_display = modified[:16] if len(modified) >= 16 else modified

    # Calculate span if we have both timestamps
    span_str = ""
    if created and modified:
        try:
            cre_str = created.replace("Z", "+00:00")
            mod_str = modified.replace("Z", "+00:00")
            cre_dt = datetime.fromisoformat(cre_str)
            mod_dt = datetime.fromisoformat(mod_str)

            delta = mod_dt - cre_dt
            total_minutes = int(delta.total_seconds() // 60)
            days = delta.days
            hours = delta.seconds // 3600
            minutes = (delta.seconds % 3600) // 60

            if days >= 1:
                span_str = f"{days}d"
            elif hours >= 1:
                span_str = f"{hours}h"
            elif total_minutes >= 1:
                span_str = f"{total_minutes}m"
            else:
                span_str = "<1m"
        except (ValueError, TypeError):
            pass

    if span_str:
        return f"{span_str}, {mod_display}"
    else:
        return mod_display


def build_session_file_list(
    chronological_chain: List[Tuple[Path, str]],
) -> str:
    """
    Build a numbered list of session files with derivation relationships.

    Args:
        chronological_chain: List of (path, derivation_type) tuples in
            chronological order (oldest first)

    Returns:
        Formatted string with numbered file list, e.g.:
            1. /path/to/session1.jsonl (original, spanning 2 days, last modified 2025-12-10 11:45)
            2. /path/to/session2.jsonl (rollover from 1, spanning 1 day, last modified 2025-12-10 15:30)
    """
    file_lines = []
    for i, (path, derivation_type) in enumerate(chronological_chain):
        # Get timestamps and format span
        created, modified = _get_session_timestamps(path)
        time_info = _format_time_span(created, modified)

        if derivation_type == "original":
            if time_info:
                file_lines.append(f"{i+1}. {path} (original, {time_info})")
            else:
                file_lines.append(f"{i+1}. {path} (original)")
        else:
            parent_num = i  # previous item in 1-indexed
            friendly = friendly_derivation_type(derivation_type)
            if time_info:
                file_lines.append(f"{i+1}. {path} ({friendly} from {parent_num}, {time_info})")
            else:
                file_lines.append(f"{i+1}. {path} ({friendly} from {parent_num})")
    return "\n".join(file_lines)


def build_rollover_prompt(
    all_session_files: List[Path],
    chronological_chain: List[Tuple[Path, str]],
    quick_rollover: bool = False,
    custom_prompt: Optional[str] = None,
    subagent_instruction: Optional[str] = None,
) -> str:
    """
    Build the rollover prompt for continuing a session.

    This function consolidates the prompt-building logic for both quick rollover
    (just present lineage) and context rollover (use sub-agents to extract
    context). It handles both single-session and multi-session lineage chains.

    Args:
        all_session_files: List of session file paths in chronological order
        chronological_chain: List of (path, derivation_type) tuples in
            chronological order (oldest first)
        quick_rollover: If True, just present lineage without context recovery.
            If False, include instructions for sub-agent context recovery.
        custom_prompt: Optional custom instructions for context recovery.
            Only used when quick_rollover=False.
        subagent_instruction: Instructions for sub-agent usage, e.g.:
            - "PARALLEL HAIKU SUB-AGENTS" (for Claude)
            - "parallel sub-agents (if available)" (for Codex)
            Only used when quick_rollover=False.

    Returns:
        The formatted rollover prompt string
    """
    is_single = len(all_session_files) == 1

    if quick_rollover:
        return _build_quick_rollover_prompt(
            all_session_files, chronological_chain, is_single
        )
    else:
        return _build_context_rollover_prompt(
            all_session_files,
            chronological_chain,
            is_single,
            custom_prompt,
            subagent_instruction,
        )


def _build_quick_rollover_prompt(
    all_session_files: List[Path],
    chronological_chain: List[Tuple[Path, str]],
    is_single: bool,
) -> str:
    """Build prompt for quick rollover (just present lineage)."""
    if is_single:
        session_file = all_session_files[0]
        return f"""[SESSION LINEAGE]

This session continues from a previous conversation. The prior session log is:

  {session_file}

The file is in JSONL format. Since it can be large, use appropriate strategies (such as sub-agents if available) to carefully explore it if you need context.

Do not do anything yet. Simply greet the user and await instructions on how they want to continue the work based on the above session."""
    else:
        file_list = build_session_file_list(chronological_chain)
        return f"""[SESSION LINEAGE]

This session continues from a chain of prior conversations. Here are the JSONL session files in CHRONOLOGICAL ORDER (oldest to newest):

{file_list}

**Terminology:**
- "trimmed" = Long messages were truncated to free up context. Full content in parent session.
- "rolled over" = Work handed off to fresh session.

Since these files can be large, use appropriate strategies (such as sub-agents if available) to carefully explore them if you need context.

Do not do anything yet. Simply greet the user and await instructions on how they want to continue the work based on the above sessions."""


def _build_context_rollover_prompt(
    all_session_files: List[Path],
    chronological_chain: List[Tuple[Path, str]],
    is_single: bool,
    custom_prompt: Optional[str],
    subagent_instruction: Optional[str],
) -> str:
    """Build prompt for context rollover (sub-agent context recovery)."""
    # Default context recovery instructions
    default_instructions = """Recover context from the session(s) above:
1. Get a rough overview of what happened throughout the session(s)
2. Extract detailed context of the LAST task being worked on, in the MOST RECENT session-file, so work can continue smoothly.

When done, state your understanding of the task history and current state to me."""

    context_instructions = custom_prompt if custom_prompt else default_instructions

    # Default sub-agent instruction if not provided
    if subagent_instruction is None:
        subagent_instruction = "parallel sub-agents"

    if is_single:
        session_file = all_session_files[0]
        return f"""[SESSION LINEAGE]

This session continues from a previous conversation. The prior session log is:

  {session_file}

The file is in JSONL format (one JSON object per line). Each line represents a message with fields like 'type' (user/assistant), 'message.content', etc.

---

Strategically use {subagent_instruction} to recover context as instructed below. Do NOT read the file yourself - use sub-agents to preserve your context window.

=== CONTEXT RECOVERY INSTRUCTIONS ===
{context_instructions}
=== END CONTEXT RECOVERY INSTRUCTIONS ==="""
    else:
        file_list = build_session_file_list(chronological_chain)
        return f"""[SESSION LINEAGE]

This session continues from a chain of prior conversations. Here are the JSONL session files in CHRONOLOGICAL ORDER (oldest to newest):

{file_list}

**Terminology:**
- "trimmed" = Long messages were truncated to free up context. Full content in parent session.
- "rolled over" = Work handed off to fresh session.

Each file is in JSONL format. The LAST file ({all_session_files[-1]}) is the most recent session.

---

Strategically use {subagent_instruction} to recover context as instructed below. Do NOT read these files yourself - use sub-agents to preserve your context window.

=== CONTEXT RECOVERY INSTRUCTIONS ===
{context_instructions}
=== END CONTEXT RECOVERY INSTRUCTIONS ==="""


def count_user_messages(session_file: Path, agent: str) -> int:
    """
    Count user messages in a session file.

    Only counts actual user messages, not tool results or system messages.
    This is the "lines" metric shown in the UI (e.g., "43L" in search results).

    Args:
        session_file: Path to session JSONL file
        agent: Agent type ('claude' or 'codex')

    Returns:
        Number of user messages in the session
    """
    user_count = 0

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

                if agent == "claude":
                    # Claude format: type is "user" or "assistant"
                    msg_type = data.get("type")
                    if msg_type != "user":
                        continue

                    # Exclude tool results - they have content as list with
                    # {"type": "tool_result"}
                    message = data.get("message", {})
                    content = message.get("content")
                    is_tool_result = (
                        isinstance(content, list)
                        and len(content) > 0
                        and isinstance(content[0], dict)
                        and content[0].get("type") == "tool_result"
                    )
                    if not is_tool_result:
                        user_count += 1

                elif agent == "codex":
                    # Codex format: type is "response_item" with payload
                    if data.get("type") != "response_item":
                        continue

                    payload = data.get("payload", {})
                    if payload.get("type") != "message":
                        continue

                    if payload.get("role") == "user":
                        user_count += 1

    except (OSError, IOError):
        pass

    return user_count
