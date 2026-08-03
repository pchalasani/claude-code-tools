#!/usr/bin/env python3
"""
Session lineage tracking for Claude Code and Codex sessions.

This module provides utilities for tracing session parent relationships through
trim and continuation metadata, and building complete lineage chains with
exported files.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class SessionNode:
    """
    Represents a node in the session lineage chain.

    Attributes:
        session_file: Path to the session JSONL file
        derivation_type: How this session was derived ("trimmed", "continued",
                        or None for original)
        exported_file: Path to exported txt file (if exists or was created)
        parent: Link to parent node in the chain (None for original session)
    """
    session_file: Path
    derivation_type: Optional[str]
    exported_file: Optional[Path]
    parent: Optional['SessionNode'] = None


def get_parent_info(session_file: Path) -> Tuple[
    Optional[Path], Optional[str], Optional[Path]
]:
    """
    Extract parent session information from session metadata.

    Args:
        session_file: Path to the session JSONL file

    Returns:
        Tuple of (parent_file, derivation_type, exported_file):
        - parent_file: Path to parent session (if any)
        - derivation_type: "trimmed", "continued", or None
        - exported_file: Path to exported chat log (only for continued
                        sessions)

    Returns (None, None, None) if file is not a derived session.
    """
    if not session_file.exists():
        return None, None, None

    try:
        with open(
            session_file,
            "r",
            encoding="utf-8",
            errors="replace",
        ) as f:
            first_line = f.readline().strip()

        if not first_line:
            return None, None, None

        data = json.loads(first_line)

        # Check for continue metadata first (takes precedence)
        if "continue_metadata" in data:
            continue_meta = data["continue_metadata"]
            parent_file = continue_meta.get("parent_session_file")
            exported_file = continue_meta.get("exported_chat_log")

            if parent_file:
                parent_path = Path(parent_file)
                exported_path = (
                    Path(exported_file) if exported_file else None
                )
                return parent_path, "continued", exported_path

        # Check for trim metadata
        if "trim_metadata" in data:
            trim_meta = data["trim_metadata"]
            parent_file = trim_meta.get("parent_file")

            if parent_file:
                return Path(parent_file), "trimmed", None

        return None, None, None

    except (json.JSONDecodeError, KeyError, FileNotFoundError):
        return None, None, None


def get_continuation_lineage(
    session_file: Path, export_missing: bool = False
) -> List[SessionNode]:
    """
    Get the full lineage of continuation sessions with exported files.

    Traces backwards through parent chain, collecting only continued sessions
    (which have exported files) and optionally trimmed sessions (exporting
    them on demand if requested).

    Args:
        session_file: Path to the session to trace from
        export_missing: If True, export trimmed sessions that don't have
                       exported files

    Returns:
        List of SessionNode objects in chronological order (oldest first).
        Each node represents a continued session or (optionally) a trimmed
        session with its exported file.

    Note:
        - Original sessions are not included in the result
        - Trimmed sessions without exports are only included if
          export_missing=True
        - The function follows both trim_metadata.parent_file and
          continue_metadata.parent_session_file
    """
    nodes: List[SessionNode] = []
    current_file = session_file
    visited = set()  # Prevent circular references

    # Trace backwards to build the chain
    while current_file and current_file not in visited:
        visited.add(current_file)

        parent_file, derivation_type, exported_file = get_parent_info(
            current_file
        )

        # If no parent, we've reached the original session
        if not parent_file:
            break

        # Decide whether to include this session
        include_session = False
        final_exported_file = exported_file

        if derivation_type == "continued":
            # Always include continued sessions
            include_session = True
            # Make exported_file absolute if it's relative
            if exported_file and not exported_file.is_absolute():
                # Try to resolve relative to session file's directory
                abs_exported = current_file.parent / exported_file
                if abs_exported.exists():
                    final_exported_file = abs_exported
                else:
                    # Try relative to cwd
                    final_exported_file = Path.cwd() / exported_file

        elif derivation_type == "trimmed" and export_missing:
            # Include trimmed sessions only if export_missing is True
            include_session = True
            # Export the trimmed session on demand
            final_exported_file = _export_session_on_demand(current_file)

        if include_session:
            node = SessionNode(
                session_file=current_file,
                derivation_type=derivation_type,
                exported_file=final_exported_file,
                parent=None  # Will link parents after reversing
            )
            nodes.append(node)

        # Move to parent
        current_file = parent_file

    # Reverse to get chronological order (oldest first)
    nodes.reverse()

    # Link parent references
    for i in range(1, len(nodes)):
        nodes[i].parent = nodes[i - 1]

    return nodes


def _export_session_on_demand(session_file: Path) -> Optional[Path]:
    """
    Export a session file to txt format on demand.

    This is used when a trimmed session in the lineage chain needs to be
    exported for full context.

    Args:
        session_file: Path to the session JSONL file

    Returns:
        Path to the exported txt file, or None if export failed
    """
    try:
        # Import here to avoid circular dependency
        from claude_code_tools.export_claude_session import (
            export_session_programmatic,
        )

        # Export with auto-generated filename
        exported_path = export_session_programmatic(
            str(session_file), output_path=None, verbose=False
        )
        return exported_path

    except Exception as e:
        print(
            f"Warning: Failed to export session {session_file.name}: {e}"
        )
        return None


def get_full_lineage_chain(session_file: Path) -> List[Tuple[Path, str]]:
    """
    Get the complete parent chain including both trimmed and continued
    sessions.

    This is useful for commands like find-original that need to display the
    full derivation history.

    Args:
        session_file: Path to the session to trace from

    Returns:
        List of (session_file, derivation_type) tuples in reverse chronological
        order (newest first, ending with the original session).

    Note:
        Unlike get_continuation_lineage(), this includes ALL sessions in the
        chain and does not export anything. The original session will have
        derivation_type = "original".
    """
    chain: List[Tuple[Path, str]] = []
    current_file = session_file
    visited = set()

    # Start with the current session
    parent_file, derivation_type, _ = get_parent_info(current_file)
    if parent_file:
        # This is a derived session
        chain.append((current_file, derivation_type or "unknown"))
        current_file = parent_file
        visited.add(session_file)
    else:
        # This is already the original
        return [(current_file, "original")]

    # Trace backwards
    while current_file and current_file not in visited:
        visited.add(current_file)

        parent_file, derivation_type, _ = get_parent_info(current_file)

        if not parent_file:
            # Reached the original
            chain.append((current_file, "original"))
            break

        chain.append((current_file, derivation_type or "unknown"))
        current_file = parent_file

    return chain
