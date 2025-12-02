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

# Pre-compiled patterns for sensitive data sanitization
SENSITIVE_PATTERNS = [
    (re.compile(r'sk-ant-[a-zA-Z0-9-]{20,}'), 'sk-ant-[REDACTED]'),
    (re.compile(r'sk-[a-zA-Z0-9]{32,}'), 'sk-[REDACTED]'),
    (re.compile(r'Bearer\s+[^\s]+', re.IGNORECASE), 'Bearer [REDACTED]'),
    (re.compile(r'(password|passwd|pwd)[:=]\s*[^\s]+', re.IGNORECASE), r'\1=[REDACTED]'),
    (re.compile(r'(api[_-]?key)[:=]\s*[^\s]+', re.IGNORECASE), r'\1=[REDACTED]'),
    (re.compile(r'(token)[:=]\s*[^\s]+', re.IGNORECASE), r'\1=[REDACTED]'),
    (re.compile(r'(secret)[:=]\s*[^\s]+', re.IGNORECASE), r'\1=[REDACTED]'),
    (re.compile(r'(Authorization):\s*[^\n]+', re.IGNORECASE), r'\1: [REDACTED]'),
]


def sanitize_sensitive_data(text):
    """Remove sensitive patterns from text before logging."""
    if not text:
        return text

    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


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

    # Sanitize all data before logging to prevent credential exposure
    safe_command = sanitize_sensitive_data(command)
    safe_error_line = sanitize_sensitive_data(error_line)
    safe_output = sanitize_sensitive_data(full_output[:2000])

    entry = {
        "timestamp": datetime.now().isoformat(),
        "command": safe_command,
        "error_line": safe_error_line,
        "full_output": safe_output,
        "status": "pending",
        "search_suggestions": [
            f"{safe_error_line} fix",
            f"{safe_error_line} stackoverflow",
            f"{safe_error_line} solution"
        ]
    }

    try:
        with open(log_file, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except (OSError, IOError):
        # If logging fails, continue without blocking
        pass

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
