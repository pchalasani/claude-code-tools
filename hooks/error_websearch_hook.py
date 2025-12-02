#!/usr/bin/env python3
"""
Post-tool hook that detects errors in Bash output and logs them for resolution.
This hook triggers after Bash commands complete and checks for error patterns.
"""
import json
import sys
import os
import re
from datetime import datetime

# Pre-compiled error patterns for performance (compiled once at module load)
ERROR_PATTERNS = [
    re.compile(r'error:', re.IGNORECASE),
    re.compile(r'\bERROR\s+in\b', re.IGNORECASE),  # Webpack-style errors
    re.compile(r'failed', re.IGNORECASE),
    re.compile(r'exception', re.IGNORECASE),
    re.compile(r'not found', re.IGNORECASE),
    re.compile(r'cannot find', re.IGNORECASE),
    re.compile(r'undefined', re.IGNORECASE),
    re.compile(r'npm ERR!'),
    re.compile(r'SyntaxError'),
    re.compile(r'TypeError'),
    re.compile(r'ReferenceError'),
    re.compile(r'ModuleNotFoundError'),
    re.compile(r'ImportError'),
    re.compile(r'command not found', re.IGNORECASE),
    re.compile(r'Permission denied', re.IGNORECASE),
    re.compile(r'ENOENT'),
    re.compile(r'EACCES'),
    re.compile(r'compilation failed', re.IGNORECASE),
    re.compile(r'build failed', re.IGNORECASE),
    re.compile(r'test failed', re.IGNORECASE),
    re.compile(r'assertion failed', re.IGNORECASE),
    re.compile(r'AssertionError'),
    re.compile(r'panic:', re.IGNORECASE),
    re.compile(r'fatal:', re.IGNORECASE),
]

def detect_errors(output):
    """Detect if output contains error patterns."""
    if not output:
        return False, None

    for pattern in ERROR_PATTERNS:
        if pattern.search(output):
            # Extract the error line
            lines = output.split('\n')
            for line in lines:
                if pattern.search(line):
                    return True, line.strip()[:200]  # First 200 chars

    return False, None

def log_error_for_resolution(error_line, command, full_output):
    """Log error to a file for the error-resolver agent to pick up."""
    log_dir = os.path.expanduser("~/.claude/logs")
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, "pending_errors.jsonl")

    entry = {
        "timestamp": datetime.now().isoformat(),
        "command": command,
        "error_line": error_line,
        "full_output": full_output[:2000],  # Limit size
        "status": "pending",
        "search_suggestions": [
            f"{error_line} fix",
            f"{error_line} stackoverflow",
            f"{error_line} solution"
        ]
    }

    with open(log_file, 'a') as f:
        f.write(json.dumps(entry) + '\n')

    return entry

def main():
    data = json.load(sys.stdin)

    tool_name = data.get("tool_name")

    # Only process Bash tool results
    if tool_name != "Bash":
        print(json.dumps({"decision": "approve"}))
        sys.exit(0)

    # Get the output from the tool result
    tool_result = data.get("tool_result", {})
    output = tool_result.get("stdout", "") + tool_result.get("stderr", "")
    command = data.get("tool_input", {}).get("command", "")

    # Check for errors
    has_error, error_line = detect_errors(output)

    if has_error:
        # Log the error for resolution
        entry = log_error_for_resolution(error_line, command, output)

        # Print suggestion (this goes to Claude's context)
        suggestion = {
            "decision": "approve",  # Don't block, just inform
            "metadata": {
                "error_detected": True,
                "error_line": error_line,
                "action_required": "Use WebSearch to find solution",
                "suggested_searches": entry["search_suggestions"]
            }
        }
        print(json.dumps(suggestion, ensure_ascii=False))
    else:
        print(json.dumps({"decision": "approve"}))

    sys.exit(0)

if __name__ == "__main__":
    main()
