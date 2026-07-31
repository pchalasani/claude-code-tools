#!/usr/bin/env python3
"""
Hook to protect .env files from being read or searched.
Blocks commands that would expose .env contents and suggests safer alternatives.
"""
import os
import re
import shlex

# Safe commands that may mention .env in text but don't access files
# These commands only pass .env as string content, not as file operations
SAFE_COMMAND_PATTERNS = [
    r'^git\s+commit\b',      # git commit -m "message about .env"
    r'^git\s+tag\b',         # git tag -m "message"
    r'^gh\s+pr\s+create\b',  # gh pr create --body "..."
    r'^gh\s+issue\s+create\b',  # gh issue create --body "..."
    r'^gh\s+release\s+create\b',  # gh release create
]

BLOCK_REASON = (
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

# Command words that can expose or overwrite a file's contents. Only a shell
# segment's COMMAND WORD is looked up here, never a word appearing anywhere in
# the string.
READER_COMMANDS = {
    'cat', 'less', 'more', 'head', 'tail', 'nano', 'vi', 'vim', 'emacs',
    'code', 'subl', 'atom', 'gedit', 'grep', 'rg', 'ag', 'ack', 'tee',
    'cp', 'mv', 'touch', 'sed', 'awk', 'find', 'bat', 'xxd', 'od', 'strings',
}

_SEGMENT_SEPARATORS = {';', '&&', '||', '|', '&', '(', ')'}
_DOTENV_PATH = re.compile(r'(?:^|/)\.env(?:\.[A-Za-z0-9_.-]+)?$')
_BARE_ENV = ('env', './env')
_ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_REDIRECT_TO_DOTENV = re.compile(
    r'>>?\s*["\']?(?:[^\s;|&]*/)?\.env(?:\.[A-Za-z0-9_.-]+)?\b')

# The original whole-string patterns, kept only as the fallback for a command
# that cannot be tokenised. They over-block, which is the right direction to
# fail in when the command's shape is unknown.
LEGACY_ENV_PATTERNS = [
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
    r'>\s*\.env\b',
    r'>>\s*\.env\b',
    r'\becho\s+.*>\s*\.env\b',
    r'\becho\s+.*>>\s*\.env\b',
    r'\bprintf\s+.*>\s*\.env\b',
    r'\bprintf\s+.*>>\s*\.env\b',
    r'\bsed\s+.*-i.*\.env\b',
    r'\bawk\s+.*>\s*\.env\b',
    r'\btee\s+.*\.env\b',
    r'\bcp\s+.*\.env\b',
    r'\bmv\s+.*\.env\b',
    r'\btouch\s+.*\.env\b',

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


def _names_dotenv(token):
    """True when this argument NAMES a dotenv file (.env, .env.local, a/.env)."""
    return bool(_DOTENV_PATH.search(token.strip('\'"')))


def shell_segments(command):
    """
    Split a command into shell segments, respecting quotes.

    Returns a list of token lists, or None if the command cannot be tokenised.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None

    segments, current = [], []
    for token in tokens:
        if token in _SEGMENT_SEPARATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def check_env_file_access(command):
    """
    Check if a command attempts to read, write, or edit .env files.
    Returns tuple: (should_block: bool, reason: str or None)

    Each shell segment's command word is resolved, and the question asked is
    whether one of ITS OWN arguments names a dotenv file -- rather than whether
    a command name and an env-looking string both appear somewhere in the same
    line, which does not require the two to be related.
    """
    # Normalize the command
    normalized_cmd = ' '.join(command.strip().split())

    for pattern in SAFE_COMMAND_PATTERNS:
        if re.match(pattern, normalized_cmd, re.IGNORECASE):
            return False, None

    # Redirection into a dotenv file is a write whatever the command word is.
    if _REDIRECT_TO_DOTENV.search(normalized_cmd):
        return True, BLOCK_REASON

    segments = shell_segments(normalized_cmd)
    if segments is None:
        for pattern in LEGACY_ENV_PATTERNS:
            if re.search(pattern, normalized_cmd, re.IGNORECASE):
                return True, BLOCK_REASON
        return False, None

    for segment in segments:
        index = 0
        # Leading VAR=value assignments precede the command word.
        while index < len(segment) and _ASSIGNMENT.match(segment[index]):
            index += 1
        if index >= len(segment):
            continue

        command_word = os.path.basename(segment[index].strip('\'"'))
        if command_word not in READER_COMMANDS:
            continue
        args = segment[index + 1:]

        if command_word == 'find':
            # find takes a dotenv path as a comparison operand (-newer, -cnewer)
            # without reading it. Only -name names the file it will surface.
            for position, arg in enumerate(args):
                if arg != '-name' or position + 1 >= len(args):
                    continue
                operand = args[position + 1]
                if _names_dotenv(operand) or operand.strip('\'"') in _BARE_ENV:
                    return True, BLOCK_REASON
            continue

        if any(_names_dotenv(arg) for arg in args):
            return True, BLOCK_REASON

        # A file literally named `env` counts too, but only for cat/less and
        # only as the final argument -- as the original patterns had it. A bare
        # `env` anywhere in any reader's arguments would deny `grep env
        # package.json`, which is an ordinary string search.
        if command_word in ('cat', 'less') and args \
                and args[-1].strip('\'"') in _BARE_ENV:
            return True, BLOCK_REASON

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
                "hookEventName": "PreToolUse",
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
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow"
            }
        }))

    sys.exit(0)
