#!/usr/bin/env python3
"""Delete Claude Code or Codex session files with confirmation."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from claude_code_tools.session_utils import get_session_uuid, resolve_session_path


def get_session_info(session_file: Path) -> dict:
    """
    Extract session information for display.

    Args:
        session_file: Path to session file

    Returns:
        Dict with session metadata (lines, date_range, last_user_msg, etc.)
    """
    with open(session_file, 'r') as f:
        lines = f.readlines()

    total_lines = len(lines)
    first_timestamp = None
    last_timestamp = None
    last_user_msg = None

    for line in lines:
        try:
            data = json.loads(line.strip())

            # Extract timestamp
            timestamp = None
            if 'timestamp' in data:
                timestamp = data['timestamp']
            elif 'created_at' in data:
                timestamp = data['created_at']

            if timestamp:
                if first_timestamp is None:
                    first_timestamp = timestamp
                last_timestamp = timestamp

            # Look for last user message
            msg_type = data.get('type')

            # Claude Code format
            if msg_type == 'user':
                message = data.get('message', {})
                content = message.get('content', [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'text':
                            text = item.get('text', '').strip()
                            if text:
                                last_user_msg = text
                elif isinstance(content, str):
                    last_user_msg = content.strip()

            # Codex format
            elif msg_type == 'response_item':
                payload = data.get('payload', {})
                if payload.get('type') == 'message' and payload.get('role') == 'user':
                    content = payload.get('content', [])
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and item.get('type') == 'input_text':
                                text = item.get('text', '').strip()
                                if text:
                                    last_user_msg = text

        except (json.JSONDecodeError, KeyError):
            continue

    return {
        'total_lines': total_lines,
        'first_timestamp': first_timestamp,
        'last_timestamp': last_timestamp,
        'last_user_msg': last_user_msg,
    }


def format_timestamp(ts: Optional[str]) -> str:
    """Format timestamp for display."""
    if not ts:
        return 'Unknown'

    try:
        # Try ISO format
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return ts


def confirm_deletion(session_file: Path, info: dict) -> bool:
    """
    Show session info and ask for confirmation.

    Args:
        session_file: Path to session file
        info: Session metadata dict

    Returns:
        True if user confirms deletion, False otherwise
    """
    print("\n" + "=" * 70)
    print("SESSION DELETION CONFIRMATION")
    print("=" * 70)
    print(f"\nFile: {session_file}")
    print(f"Session ID: {get_session_uuid(session_file.stem)}")
    print(f"\nTotal lines: {info['total_lines']}")

    # Date range
    first = format_timestamp(info['first_timestamp'])
    last = format_timestamp(info['last_timestamp'])
    if first != 'Unknown' or last != 'Unknown':
        print(f"Date range: {first} → {last}")

    # Last user message
    if info['last_user_msg']:
        # Truncate if too long
        msg = info['last_user_msg']
        if len(msg) > 200:
            msg = msg[:200] + "..."
        print(f"\nLast user message:")
        print(f"  {msg}")

    print("\n" + "=" * 70)
    response = input("\nAre you sure you want to DELETE this session? (yes/no): ").strip().lower()
    return response in ['yes', 'y']


def delete_resolved_session(session_file: Path, *, force: bool = False) -> None:
    """Confirm and delete one already-resolved session transcript.

    Args:
        session_file: Exact transcript selected by the shared resolver.
        force: Whether to skip the final destructive confirmation.
    """
    try:
        info = get_session_info(session_file)
    except Exception as error:
        print(f"Error reading session file: {error}", file=sys.stderr)
        sys.exit(1)

    if not force and not confirm_deletion(session_file, info):
        print("\nDeletion cancelled.")
        return

    try:
        session_file.unlink()
        print(f"\n✅ Session deleted: {get_session_uuid(session_file.stem)}")
    except Exception as error:
        print(f"\n❌ Error deleting session: {error}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Delete Claude Code or Codex session files with confirmation"
    )
    parser.add_argument(
        "session_file",
        help="Session file path or session ID (supports partial matching)"
    )
    parser.add_argument(
        "--claude-home",
        type=str,
        help="Path to Claude home directory (default: ~/.claude or $CLAUDE_CONFIG_DIR)"
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Skip confirmation prompt (use with caution!)"
    )

    args = parser.parse_args()

    # Resolve session file
    try:
        session_file = resolve_session_path(args.session_file, claude_home=args.claude_home)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except SystemExit:
        # resolve_session_path calls sys.exit for multiple matches
        sys.exit(1)

    delete_resolved_session(session_file, force=args.force)


if __name__ == "__main__":
    main()
