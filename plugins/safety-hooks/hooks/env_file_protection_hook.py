#!/usr/bin/env python3
"""
Hook to protect .env files from being read or searched.
Blocks commands that would expose .env contents and suggests safer alternatives.
"""
import re

def check_env_file_access(command):
    """
    Check if a command attempts to read, write, or edit .env files.
    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Normalize the command
    normalized_cmd = ' '.join(command.strip().split())

    # Safe commands that may mention .env in text but don't access files
    # These commands only pass .env as string content, not as file operations
    safe_command_patterns = [
        r'^git\s+commit\b',      # git commit -m "message about .env"
        r'^git\s+tag\b',         # git tag -m "message"
        r'^gh\s+pr\s+create\b',  # gh pr create --body "..."
        r'^gh\s+issue\s+create\b',  # gh issue create --body "..."
        r'^gh\s+release\s+create\b',  # gh release create
    ]

    for pattern in safe_command_patterns:
        if re.match(pattern, normalized_cmd, re.IGNORECASE):
            return False, None

    # Patterns that indicate reading, writing, or editing .env files
    env_patterns = [
        # Direct file reading
        r'\bcat\s+.*\.env\b',
        r'\bless\s+.*\.env\b',
        r'\bmore\s+.*\.env\b',
        r'\bhead\s+.*\.env\b',
        r'\btail\s+.*\.env\b',
        
        # Editors - both reading and writing
        r'\bnano\s+.*\.env\b',
        r'\bvi\s+.*\.env\b',
        r'\bvim\s+.*\.env\b',
        r'\bemacs\s+.*\.env\b',
        r'\bcode\s+.*\.env\b',
        r'\bsubl\s+.*\.env\b',
        r'\batom\s+.*\.env\b',
        r'\bgedit\s+.*\.env\b',
        
        # Writing/modifying .env files
        r'>\s*\.env\b',  # Redirect to .env
        r'>>\s*\.env\b',  # Append to .env
        r'\becho\s+.*>\s*\.env\b',
        r'\becho\s+.*>>\s*\.env\b',
        r'\bprintf\s+.*>\s*\.env\b',
        r'\bprintf\s+.*>>\s*\.env\b',
        r'\bsed\s+.*-i.*\.env\b',  # sed in-place editing
        r'\bawk\s+.*>\s*\.env\b',
        r'\btee\s+.*\.env\b',
        r'\bcp\s+.*\.env\b',  # Copying to .env
        r'\bmv\s+.*\.env\b',  # Moving to .env
        r'\btouch\s+.*\.env\b',  # Creating .env
        
        # Searching/grepping .env files
        r'\bgrep\s+.*\.env\b',
        r'\bgrep\s+.*\s+\.env\b',
        r'\brg\s+.*\.env\b',
        r'\brg\s+.*\s+\.env\b',
        r'\bag\s+.*\.env\b',
        r'\back\s+.*\.env\b',
        r'\bfind\s+.*-name\s+["\']?\.env',
        
        # Other ways to expose .env contents
        r'\becho\s+.*\$\(.*cat\s+.*\.env.*\)',
        r'\bprintf\s+.*\$\(.*cat\s+.*\.env.*\)',
        
        # Also check for patterns without the dot (like "env" file)
        r'\bcat\s+["\']?env["\']?\s*$',
        r'\bcat\s+["\']?env["\']?\s*[;&|]',
        r'\bless\s+["\']?env["\']?\s*$',
        r'\bless\s+["\']?env["\']?\s*[;&|]',
        r'>\s*["\']?env["\']?\s*$',
        r'>>\s*["\']?env["\']?\s*$',
    ]
    
    # Check if any pattern matches
    for pattern in env_patterns:
        if re.search(pattern, normalized_cmd, re.IGNORECASE):
            reason_text = (
                "Blocked: Direct access to .env files is not allowed for security reasons.\n\n"
                "• Reading .env files could expose sensitive values\n"
                "• Writing/editing .env files should be done manually outside Claude Code\n\n"
                "For safe inspection, use the `env-safe` command:\n"
                "  • `env-safe list` - List all environment variable keys\n"
                "  • `env-safe list --status` - Show keys with defined/empty status\n"
                "  • `env-safe check KEY_NAME` - Check if a specific key exists\n"
                "  • `env-safe count` - Count variables in the file\n"
                "  • `env-safe validate` - Check .env file syntax\n"
                "  • `env-safe --help` - See all options\n\n"
                "To modify .env files, please edit them manually outside of Claude Code."
            )
            return True, reason_text
    
    return False, None


# If run as a standalone script
if __name__ == "__main__":
    import json
    import sys
    
    data = json.load(sys.stdin)
    
    # Check if this is a Bash tool call
    tool_name = data.get("tool_name")
    if tool_name != "Bash":
        print(json.dumps({
            "hookSpecificOutput": {
                "permissionDecision": "allow"
            }
        }))
        sys.exit(0)
    
    # Get the command being executed
    command = data.get("tool_input", {}).get("command", "")
    
    should_block, reason = check_env_file_access(command)
    
    if should_block:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason
            }
        }, ensure_ascii=False))
    else:
        print(json.dumps({
            "hookSpecificOutput": {
                "permissionDecision": "allow"
            }
        }))
    
    sys.exit(0)