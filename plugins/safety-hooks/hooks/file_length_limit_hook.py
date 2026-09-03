#!/usr/bin/env python3
"""
File Length Limit Hook

Blocks Edit and Write operations that would result in source code files
exceeding MAX_FILE_LINES. Uses a speed bump pattern: first attempt blocks
with a warning, second attempt (after user approval) proceeds.
"""
import json
import sys
import os
from pathlib import Path


# ============================================================================
# CONFIGURATION
# ============================================================================

# Maximum number of lines allowed in a source code file before triggering
# the refactoring prompt. 1000 lines is a reasonable default that encourages
# modular, maintainable code. Adjust based on your team's standards.
MAX_FILE_LINES = 10000

# Source code file extensions to check
SOURCE_CODE_EXTENSIONS = {
    '.py',      # Python
    '.tsx',     # TypeScript React
    '.ts',      # TypeScript
    '.jsx',     # JavaScript React
    '.js',      # JavaScript
    '.rs',      # Rust
    '.c',       # C
    '.cpp',     # C++
    '.cc',      # C++
    '.cxx',     # C++
    '.h',       # C/C++ header
    '.hpp',     # C++ header
    '.go',      # Go
    '.java',    # Java
    '.kt',      # Kotlin
    '.swift',   # Swift
    '.rb',      # Ruby
    '.php',     # PHP
    '.cs',      # C#
    '.scala',   # Scala
    '.m',       # Objective-C
    '.mm',      # Objective-C++
    '.r',       # R
    '.jl',      # Julia
}


def is_source_code_file(file_path: str) -> bool:
    """Check if file is a source code file based on extension."""
    if not file_path:
        return False
    return Path(file_path).suffix.lower() in SOURCE_CODE_EXTENSIONS


def count_lines_in_content(content: str) -> int:
    """Count number of lines in content string."""
    if not content:
        return 0
    return len(content.splitlines())


def get_resulting_line_count(
    tool_name: str,
    file_path: str,
    tool_input: dict
) -> int:
    """
    Calculate the resulting line count after the tool operation.

    For Write: count lines in new content
    For Edit: count lines in file after replacement

    ``tool_name`` is matched case-insensitively, since clients differ on
    how they spell it ("Write" vs "write").
    """
    tool_name = tool_name.lower()

    if tool_name == "write":
        # For Write, the new content is in the 'content' field
        content = tool_input.get("content", "")
        return count_lines_in_content(content)

    elif tool_name == "edit":
        # For Edit, we need to calculate the result of the replacement
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")

        # Get current file content if it exists
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    current_content = f.read()
            except Exception:
                # If we can't read the file, assume it's safe
                return 0
        else:
            # File doesn't exist yet, assume it's safe
            return 0

        # Calculate the result of the edit
        # Note: The replace_all parameter determines if all occurrences
        # are replaced
        replace_all = tool_input.get("replace_all", False)

        if replace_all:
            result_content = current_content.replace(old_string, new_string)
        else:
            # Replace only first occurrence
            result_content = current_content.replace(
                old_string,
                new_string,
                1
            )

        return count_lines_in_content(result_content)

    return 0


def check_file_length_limit(data: dict) -> tuple[bool, str | None]:
    """
    Check if file operation would exceed MAX_FILE_LINES limit.

    Returns:
        (should_block, reason)
    """
    # Fold case first: an exact-case mismatch would skip the limit check
    # entirely on clients that spell the tool name differently.
    tool_name = (data.get("tool_name") or "").lower()

    # Only check Edit and Write tools
    if tool_name not in ("edit", "write"):
        return False, None

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # Only check source code files
    if not is_source_code_file(file_path):
        return False, None

    # Calculate resulting line count
    resulting_lines = get_resulting_line_count(
        tool_name,
        file_path,
        tool_input
    )

    # If under limit, allow
    if resulting_lines <= MAX_FILE_LINES:
        return False, None

    # Check flag file for speed bump pattern
    flag_file = Path('.claude_file_length_warning.flag')

    # If flag exists, allow and clear flag
    if flag_file.exists():
        flag_file.unlink()
        return False, None

    # First attempt - block and create flag
    flag_file.touch()

    reason = f"""
**File length limit exceeded ({resulting_lines} lines > {MAX_FILE_LINES} lines).**

The resulting file `{file_path}` would be {resulting_lines} lines long.

To maintain code quality and modularity, files should be kept under {MAX_FILE_LINES} lines.

**Please pause and ask the user:**
"This operation would create a file with {resulting_lines} lines. Would you like me to:
1. Refactor the code into smaller, more modular files?
2. Proceed with the large file anyway?"

**Only retry this operation if the user approves proceeding with the large file.**
Otherwise, work on refactoring the code into smaller modules.
"""

    return True, reason


# Main execution
if __name__ == "__main__":
    try:
        data = json.load(sys.stdin)

        should_block, reason = check_file_length_limit(data)

        if should_block:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason
                }
            }))
        else:
            print(json.dumps({"decision": "approve"}))

        sys.exit(0)

    except Exception as e:
        # On error, approve to avoid breaking Claude
        print(json.dumps({
            "decision": "approve",
            "error": str(e)
        }))
        sys.exit(0)
