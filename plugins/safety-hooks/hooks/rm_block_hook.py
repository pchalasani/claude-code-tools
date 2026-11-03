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
import shlex
import sys
import os

# Add the hooks directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from command_utils import extract_all_commands
from shell_wrappers import (
    _command_words, _takes_value, _strip_literal_redirections,
    _exec_command, _FD, _FD_VALUE, _FIND_VALUE,
)



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
    normalized = _strip_literal_redirections(single_cmd.strip())
    if not normalized:
        return False

    try:
        # The extractor splits find's escaped semicolon, leaving a final
        # backslash token. Remove that token so quoted exec operands survive.
        parsed = shlex.split(normalized.removesuffix(' \\'))
    except ValueError:
        # The compound-command extractor can leave an incomplete shell word.
        parsed = normalized.split()
    words = _command_words(parsed)
    if not words:
        return False
    if _is_rm_word(words[0]):
        return True
    # find and fd run rm through their exec options. find also deletes by
    # itself with -delete.
    program = words[0].rsplit('/', 1)[-1]
    i = 1
    while i < len(words):
        word = words[i]
        if word == '-delete' and program == 'find':
            return True
        if program in _FD and word == '--':
            break  # All remaining words are positional operands.
        executed = _exec_command(program, word, words[i + 1:])
        if executed is not None:
            executed = _command_words(executed)
            if executed and _is_rm_word(executed[0]):
                return True
            # Exec arguments belong to the child command, not find/fd.
            if program in _FD:
                break
            i += 1
            while i < len(words) and words[i] not in (';', r'\;'):
                if words[i] == '+' and words[i - 1] == '{}':
                    break
                i += 1
        elif program == 'find' and word == '-fprintf':
            i += 2  # Output filename and format.
        elif program == 'find' and (
            word in _FIND_VALUE or re.fullmatch(r'-newer[aBcm][aBcmMt]', word)
        ):
            i += 1
        elif program in _FD and _takes_value(word, _FD_VALUE):
            i += 1
        i += 1
    return False


def _is_rm_word(word: str) -> bool:
    """rm itself, or rm by path: /bin/rm, /usr/bin/rm.

    A redirection can follow the word with no space, as in `/bin/rm>log`.
    """
    word = re.split(r'[<>]', word, maxsplit=1)[0]
    return word == 'rm' or (word.startswith('/') and word.rsplit('/', 1)[-1] == 'rm')


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
