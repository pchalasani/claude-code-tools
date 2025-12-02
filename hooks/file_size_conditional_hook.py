#!/usr/bin/env python3
"""Hook to check file size and suggest delegation for large files."""
import os
import json
import sys


def is_binary_file(filepath):
    """Check if a file is binary by looking for null bytes in first chunk."""
    try:
        with open(filepath, 'rb') as f:
            # Read first 8192 bytes (or less if file is smaller)
            chunk = f.read(8192)
            if not chunk:  # Empty file
                return False

            # Files with null bytes are likely binary
            if b'\x00' in chunk:
                return True

            # Try to decode as UTF-8
            try:
                chunk.decode('utf-8')
                return False
            except UnicodeDecodeError:
                return True
    except Exception:
        # If we can't read the file, assume it's binary to be safe
        return True


def count_lines(filepath):
    """Count lines in a file."""
    with open(filepath, 'rb') as f:
        return sum(1 for _ in f)


def validate_file_path(file_path):
    """
    Validate file path to prevent path traversal attacks.
    Returns tuple: (is_valid: bool, resolved_path: str or None, error: str or None)
    """
    if not file_path:
        return False, None, None

    from pathlib import Path

    try:
        # Resolve to absolute path
        resolved = Path(file_path).resolve()

        # Reject symlinks to prevent following to restricted areas
        if os.path.islink(file_path):
            return False, None, "Symlinks are not allowed for security reasons"

        # Check path exists
        if not resolved.exists():
            return False, None, None

        return True, str(resolved), None

    except (ValueError, OSError) as e:
        return False, None, f"Invalid file path: {type(e).__name__}"


def check_file_size(file_path, offset=0, limit=0, is_main_agent=True):
    """
    Check if a file should be blocked based on size.
    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Validate and sanitize file path
    is_valid, resolved_path, error = validate_file_path(file_path)

    if error:
        return True, error

    if not is_valid or not resolved_path:
        return False, None

    # Skip binary files
    if is_binary_file(resolved_path):
        return False, None

    line_count = count_lines(resolved_path)

    if is_main_agent and line_count > 500:
        reason = f"""
            I see you are trying to read a file with {line_count} lines,
            or a part of it.
            Please delegate the analysis to a SUB-AGENT using your Task tool,
            so you don't bloat your context with the file content!
            """
        return True, reason
    elif (not is_main_agent) and line_count > 10_000:
        reason = f"""
        File too large ({line_count} lines), please use the Gemini CLI
        bash command to delegate the analysis to Gemini since it has
        a 1M-token context window! This will help you avoid bloating
        your context.

        You can use Gemini CLI as in these EXAMPLES:

        `gemini -p "@src/somefile.py tell me at which line the definition of
                  the function 'my_function' is located"

        `gemini -p "@package.json @src/index.js Analyze the dependencies used in the code"

        See further guidelines in claude-mds/use-gemini-cli.md
        """
        return True, reason

    return False, None


def main():
    """Main entry point for hook."""
    data = json.load(sys.stdin)

    # Check if we're in a subtask
    flag_file = '.claude_in_subtask.flag'
    is_main_agent = not os.path.exists(flag_file)

    # Get file parameters
    file_path = data.get("tool_input", {}).get("file_path")
    offset = data.get("tool_input", {}).get("offset", 0)
    limit = data.get("tool_input", {}).get("limit", 0)

    should_block, reason = check_file_size(file_path, offset, limit, is_main_agent)

    if should_block:
        print(json.dumps({
            "decision": "block",
            "reason": reason,
        }))
    else:
        print(json.dumps({"decision": "approve"}))

    sys.exit(0)


if __name__ == "__main__":
    main()
