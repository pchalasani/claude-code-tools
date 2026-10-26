#!/usr/bin/env python3
"""
Safety hook to block direct use of the 'rm' command.

This hook intercepts Bash tool calls and blocks any command that would
execute 'rm', including:
    - Direct invocations: rm, /bin/rm, /usr/bin/rm
    - Commands chained with operators: echo ok | rm foo, cmd && rm bar
    - Commands hidden in subshells: echo $(rm foo), cat `rm bar`

Instead of deletion, users are guided to move files to a TRASH directory.
"""
import re
import sys
import os

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from command_utils import extract_all_commands


def _is_rm_command(single_cmd: str) -> bool:
    """
    Check if a single command (not compound) is an rm invocation.

    Detects:
        - rm (bare command)
        - rm <args> (with arguments)
        - /bin/rm, /usr/bin/rm, etc. (absolute paths)

    Args:
        single_cmd: A single bash command (already split from compound).

    Returns:
        True if this command would invoke rm, False otherwise.
    """
    normalized = ' '.join(single_cmd.strip().split())
    if not normalized:
        return False

    words = _command_words(normalized.split())
    if not words:
        return False
    if _is_rm_word(words[0]):
        return True
    # find and fd run rm through their exec options. find also deletes by
    # itself with -delete.
    program = words[0].rsplit('/', 1)[-1]
    exec_options = _EXEC_OPTIONS.get(program, set())
    for i, word in enumerate(words):
        if word == '-delete' and program == 'find':
            return True
        if word in exec_options and i + 1 < len(words) and _is_rm_word(words[i + 1]):
            return True
    return False


# Commands that run the command after them: `sudo rm`, `xargs rm`.
_WRAPPERS = {'sudo', 'env', 'command', 'builtin', 'exec', 'nohup', 'time',
             'nice', 'xargs', 'doas', 'caffeinate'}
# Options of those wrappers that take a separate value.
_WRAPPER_VALUE_OPTIONS = {'-u', '-g', '-n', '-I', '-L', '-P', '-s', '-E',
                          '-d', '-a', '-C', '-S', '--user', '--group',
                          '--max-args', '--max-procs', '--delimiter',
                          '--arg-file', '--replace'}
# Options that take the command to run: `find -exec rm`, `fd -x rm`.
_FD_EXEC = {'-x', '--exec', '-X', '--exec-batch'}
_EXEC_OPTIONS = {
    'find': {'-exec', '-execdir', '-ok', '-okdir'},
    'fd': _FD_EXEC,
    'fdfind': _FD_EXEC,  # the fd binary's name on Debian and Ubuntu
}


def _is_rm_word(word: str) -> bool:
    """rm itself, or rm by path: /bin/rm, /usr/bin/rm."""
    return word == 'rm' or bool(re.match(r'^/\S*/rm$', word))


def _command_words(words: list[str]) -> list[str]:
    """The words from the command that actually runs, past any wrappers.

    `sudo -u root env A=1 xargs -0 rm -f` becomes ['rm', '-f'].
    """
    i = 0
    while i < len(words):
        word = words[i]
        if '=' in word and not word.startswith('-') and re.match(r'^\w+=', word):
            i += 1  # VAR=value prefix
        elif word.rsplit('/', 1)[-1] in _WRAPPERS:
            i += 1
            while i < len(words) and words[i].startswith('-'):
                i += 2 if words[i] in _WRAPPER_VALUE_OPTIONS else 1
            if word.rsplit('/', 1)[-1] in ('nice',) and i < len(words) and words[i].lstrip('-').isdigit():
                i += 1
        else:
            break
    return words[i:]


def check_rm_command(command: str) -> tuple[bool, str | None]:
    """
    Check if a command contains rm that should be blocked.

    Uses extract_all_commands() to find all commands including those
    chained with shell operators (&&, ||, ;, |, &) and those hidden
    inside subshells ($() or backticks).

    Args:
        command: A bash command string, possibly compound with subshells.

    Returns:
        Tuple of (should_block, reason). If should_block is True, reason
        contains guidance for the user on the preferred approach.
    """
    # Extract all commands including subshells and chained commands
    all_commands = extract_all_commands(command)

    for cmd in all_commands:
        if _is_rm_command(cmd):
            reason_text = (
                "Instead of using 'rm':\n "
                "- MOVE files using `mv` to the TRASH directory in the CURRENT folder (create it if needed), \n"
                "- Add an entry in a markdown file called 'TRASH-FILES.md' in the current directory, "
                "  where you show a one-liner with the file name, where it moved, and the reason to trash it, e.g.:\n\n"
                "```\n"
                "test_script.py - moved to TRASH/ - temporary test script\n"
                "data/junk.txt - moved to TRASH/ - data file we don't need\n"
                "```"
            )
            return True, reason_text

    return False, None


# If run as a standalone script (Claude Code hook entry point)
if __name__ == "__main__":
    import json

    data = json.load(sys.stdin)

    # Only intercept Bash tool calls. Fold case first: an exact-case
    # mismatch would approve the command without checking it.
    tool_name = (data.get("tool_name") or "").lower()
    if tool_name != "bash":
        print(json.dumps({"decision": "approve"}))
        sys.exit(0)

    # Get the command being executed
    command = data.get("tool_input", {}).get("command", "")

    should_block, reason = check_rm_command(command)

    if should_block:
        print(json.dumps({
            "decision": "block",
            "reason": reason
        }, ensure_ascii=False))
    else:
        print(json.dumps({"decision": "approve"}))

    sys.exit(0)
