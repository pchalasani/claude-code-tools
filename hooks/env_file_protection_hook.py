#!/usr/bin/env python3
"""
Hook to protect .env files from being read or searched.
Blocks commands that would expose .env contents and suggests safer alternatives.
"""
import re

# Pre-compiled patterns for performance (compiled once at module load)
ENV_PATTERNS = [
    # Direct file reading
    re.compile(r'\bcat\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bless\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bmore\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bhead\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\btail\s+.*\.env\b', re.IGNORECASE),
    # Editors - both reading and writing
    re.compile(r'\bnano\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bvi\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bvim\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bemacs\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bcode\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bsubl\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\batom\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bgedit\s+.*\.env\b', re.IGNORECASE),
    # Writing/modifying .env files
    re.compile(r'>\s*\.env\b', re.IGNORECASE),
    re.compile(r'>>\s*\.env\b', re.IGNORECASE),
    re.compile(r'\becho\s+.*>\s*\.env\b', re.IGNORECASE),
    re.compile(r'\becho\s+.*>>\s*\.env\b', re.IGNORECASE),
    re.compile(r'\bprintf\s+.*>\s*\.env\b', re.IGNORECASE),
    re.compile(r'\bprintf\s+.*>>\s*\.env\b', re.IGNORECASE),
    re.compile(r'\bsed\s+.*-i.*\.env\b', re.IGNORECASE),
    re.compile(r'\bawk\s+.*>\s*\.env\b', re.IGNORECASE),
    re.compile(r'\btee\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bcp\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bmv\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\btouch\s+.*\.env\b', re.IGNORECASE),
    # Searching/grepping .env files
    re.compile(r'\bgrep\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\brg\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bag\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\back\s+.*\.env\b', re.IGNORECASE),
    re.compile(r'\bfind\s+.*-name\s+["\']?\.env', re.IGNORECASE),
    # Other ways to expose .env contents
    re.compile(r'\becho\s+.*\$\(.*cat\s+.*\.env.*\)', re.IGNORECASE),
    re.compile(r'\bprintf\s+.*\$\(.*cat\s+.*\.env.*\)', re.IGNORECASE),
    # Also check for patterns without the dot (like "env" file)
    re.compile(r'\bcat\s+["\']?env["\']?\s*$', re.IGNORECASE),
    re.compile(r'\bcat\s+["\']?env["\']?\s*[;&|]', re.IGNORECASE),
    re.compile(r'\bless\s+["\']?env["\']?\s*$', re.IGNORECASE),
    re.compile(r'\bless\s+["\']?env["\']?\s*[;&|]', re.IGNORECASE),
    re.compile(r'>\s*["\']?env["\']?\s*$', re.IGNORECASE),
    re.compile(r'>>\s*["\']?env["\']?\s*$', re.IGNORECASE),
]

def check_env_file_access(command):
    """
    Check if a command attempts to read, write, or edit .env files.
    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Normalize the command
    normalized_cmd = ' '.join(command.strip().split())

    # Check if any pattern matches
    for pattern in ENV_PATTERNS:
        if pattern.search(normalized_cmd):
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
        print(json.dumps({"decision": "approve"}))
        sys.exit(0)
    
    # Get the command being executed
    command = data.get("tool_input", {}).get("command", "")
    
    should_block, reason = check_env_file_access(command)
    
    if should_block:
        print(json.dumps({
            "decision": "block",
            "reason": reason
        }, ensure_ascii=False))
    else:
        print(json.dumps({"decision": "approve"}))
    
    sys.exit(0)