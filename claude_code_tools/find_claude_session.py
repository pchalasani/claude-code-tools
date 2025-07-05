#!/usr/bin/env python3
"""
find-claude-session: Search Claude Code session files by keywords

Usage:
    find-claude-session "keyword1,keyword2,keyword3..."
    
This tool searches for Claude Code session JSONL files that contain ALL specified keywords,
and returns matching session IDs in reverse chronological order.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Set


def get_claude_project_dir() -> Path:
    """Convert current working directory to Claude project directory path."""
    cwd = os.getcwd()
    # Replace / with - to match Claude's directory naming convention
    project_path = cwd.replace("/", "-")
    claude_dir = Path.home() / ".claude" / "projects" / project_path
    return claude_dir


def search_keywords_in_file(filepath: Path, keywords: List[str]) -> tuple[bool, int]:
    """
    Check if all keywords are present in the JSONL file and count lines.
    
    Args:
        filepath: Path to the JSONL file
        keywords: List of keywords to search for (case-insensitive)
        
    Returns:
        Tuple of (matches: bool, line_count: int)
        - matches: True if ALL keywords are found in the file
        - line_count: Total number of lines in the file
    """
    # Convert keywords to lowercase for case-insensitive search
    keywords_lower = [k.lower() for k in keywords]
    found_keywords = set()
    line_count = 0
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line_count += 1
                line_lower = line.lower()
                # Check which keywords are in this line
                for keyword in keywords_lower:
                    if keyword in line_lower:
                        found_keywords.add(keyword)
    except Exception:
        # Skip files that can't be read
        return False, 0
    
    matches = len(found_keywords) == len(keywords_lower)
    return matches, line_count


def find_sessions(keywords: List[str]) -> List[tuple[str, float, int]]:
    """
    Find all Claude Code sessions containing the specified keywords.
    
    Args:
        keywords: List of keywords to search for
        
    Returns:
        List of tuples (session_id, modification_time, line_count) sorted by modification time
    """
    claude_dir = get_claude_project_dir()
    
    if not claude_dir.exists():
        return []
    
    matching_sessions = []
    
    # Search all JSONL files in the directory
    for jsonl_file in claude_dir.glob("*.jsonl"):
        matches, line_count = search_keywords_in_file(jsonl_file, keywords)
        if matches:
            session_id = jsonl_file.stem  # filename without extension
            mod_time = jsonl_file.stat().st_mtime
            matching_sessions.append((session_id, mod_time, line_count))
    
    # Sort by modification time (newest first)
    matching_sessions.sort(key=lambda x: x[1], reverse=True)
    
    return matching_sessions


def main():
    parser = argparse.ArgumentParser(
        description="Search Claude Code session files by keywords",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    find-claude-session "langroid"
    find-claude-session "langroid,MCP"
    find-claude-session "error,TypeError,function"
        """
    )
    parser.add_argument(
        "keywords",
        help="Comma-separated keywords to search for (case-insensitive)"
    )
    
    args = parser.parse_args()
    
    # Parse keywords
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    
    if not keywords:
        print("Error: No keywords provided", file=sys.stderr)
        sys.exit(1)
    
    # Find matching sessions
    claude_dir = get_claude_project_dir()
    
    if not claude_dir.exists():
        print(f"No Claude project directory found for: {os.getcwd()}", file=sys.stderr)
        print(f"Expected directory: {claude_dir}", file=sys.stderr)
        sys.exit(1)
    
    matching_sessions = find_sessions(keywords)
    
    if not matching_sessions:
        print(f"No sessions found containing all keywords: {', '.join(keywords)}", file=sys.stderr)
        sys.exit(0)
    
    # Print session IDs with modification dates and line counts
    for session_id, mod_time, line_count in matching_sessions:
        # Convert timestamp to readable date
        mod_date = datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M:%S')
        print(f"{session_id} | {mod_date} | {line_count} lines")


if __name__ == "__main__":
    main()