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
    normalized = single_cmd.strip()
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


# Commands that run the command after them: `sudo rm`, `xargs rm`, mapped to
# their options that take a separate value. Each wrapper has its own set: `-n`
# takes a value for nice and xargs, but is a flag for sudo.
_WRAPPERS = {
    'sudo': {'-u', '-g', '-C', '-D', '-p', '-R', '-r', '-t', '-T', '-U',
             '--user', '--group', '--close-from', '--chdir', '--prompt',
             '--chroot', '--role', '--type', '--command-timeout',
             '--other-user'},
    'doas': {'-u', '-C'},
    'env': {'-u', '-C', '-S', '-P', '-a', '--unset', '--chdir',
            '--split-string', '--argv0'},
    # GNU --replace/--max-lines take optional attached values, unlike -I/-L.
    'xargs': {'-I', '-L', '-J', '-R', '-S', '-n', '-P', '-s', '-d', '-a', '-E',
              '--max-args',
              '--max-procs', '--max-chars', '--delimiter', '--arg-file'},
    'nice': {'-n', '--adjustment'},
    'time': {'-f', '-o', '--format', '--output'},
    'exec': {'-a'},
    'caffeinate': {'-t', '-w'},
    'command': set(),
    'builtin': set(),
    'nohup': set(),
}
# Options that make a wrapper look the command up instead of running it:
# `command -v rm` prints where rm is.
_LOOKUP_OPTIONS = {'command': {'-v', '-V'}, 'sudo': {'-l', '--list'}}
_FIND_EXEC = {'-exec', '-execdir', '-ok', '-okdir'}
_FD = {'fd', 'fdfind'}  # fdfind is the fd binary's name on Debian and Ubuntu
# fd short options that take a value, so a cluster ends at them: in
# `fd -ex`, x is the extension, not --exec.
_FD_SHORT_VALUE = set('etEdScjo')
_FD_VALUE = {f'-{letter}' for letter in _FD_SHORT_VALUE} | {
    '--extension', '--type', '--exclude', '--max-depth', '--size', '--color',
    '--threads', '--owner', '--changed-within', '--changed-before',
    '--ignore-file', '--base-directory', '--path-separator', '--max-results',
    '--min-depth', '--exact-depth', '--search-path',
}
_FIND_VALUE = {
    '-name', '-iname', '-path', '-ipath', '-wholename', '-iwholename',
    '-regex', '-iregex', '-lname', '-ilname', '-fstype', '-type', '-xtype',
    '-user', '-group', '-uid', '-gid', '-perm', '-size', '-links', '-inum',
    '-samefile', '-newer', '-anewer', '-cnewer', '-mtime', '-atime', '-ctime',
    '-mmin', '-amin', '-cmin', '-used', '-maxdepth', '-mindepth', '-regextype',
    '-f', '-flags', '-Btime', '-Bmin', '-fprint', '-fprint0',
    '-fls', '-printf',
}


def _exec_command(program: str, word: str, rest: list[str]) -> list[str] | None:
    """The command that find or fd runs through `word`, or None."""
    if program == 'find':
        return rest if word in _FIND_EXEC else None
    if program not in _FD:
        return None
    if word in ('--exec', '--exec-batch'):
        return rest
    for option in ('--exec=', '--exec-batch='):
        if word.startswith(option):
            return [word[len(option):], *rest]
    if word.startswith('-') and not word.startswith('--'):
        # A short cluster: `-x rm`, `-xrm`, `-Hx rm`.
        for index, letter in enumerate(word[1:], start=1):
            if letter in 'xX':
                attached = word[index + 1:]
                return [attached, *rest] if attached else rest
            if letter in _FD_SHORT_VALUE:
                break
    return None


def _is_rm_word(word: str) -> bool:
    """rm itself, or rm by path: /bin/rm, /usr/bin/rm.

    A redirection can follow the word with no space, as in `/bin/rm>log`.
    """
    word = re.split(r'[<>]', word, maxsplit=1)[0]
    return word == 'rm' or (word.startswith('/') and word.rsplit('/', 1)[-1] == 'rm')


def _takes_value(option: str, value_options: set[str]) -> bool:
    """Whether a wrapper option consumes the next word, as `-u` in `sudo -u root`.

    A short cluster ends at its first value-taking option. In `-uroot`,
    `root` is attached to `-u`; in `-nu root`, `-u` consumes the next word.
    """
    if option.startswith('--'):
        return option in value_options
    if option.startswith('-'):
        for index, letter in enumerate(option[1:], start=1):
            if f'-{letter}' in value_options:
                return index == len(option) - 1
    return False


def _command_words(words: list[str]) -> list[str]:
    """The words from the command that actually runs, past any wrappers.

    `sudo -u root env A=1 xargs -0 rm -f` becomes ['rm', '-f']. A wrapper that
    only looks the command up, as `command -v rm`, runs nothing: [].
    """
    i = 0
    while i < len(words):
        word = words[i]
        name = word.rsplit('/', 1)[-1]
        if '=' in word and not word.startswith('-') and re.match(r'^\w+=', word):
            i += 1  # VAR=value prefix
        elif name in _WRAPPERS:
            i += 1
            while i < len(words) and words[i].startswith('-'):
                option = words[i]
                if option == '-' and name != 'env':
                    break  # Only env treats a lone dash as an option.
                if option == '--':
                    i += 1
                    break
                lookup_options = _LOOKUP_OPTIONS.get(name, ())
                if option in lookup_options:
                    return []
                if not option.startswith('--'):
                    for letter in option[1:]:
                        short_option = f'-{letter}'
                        if short_option in lookup_options:
                            return []
                        if short_option in _WRAPPERS[name]:
                            break  # Remaining letters are this option's value.
                if name == 'env':
                    # -S inserts a command, rather than discarding an operand.
                    # Stop short-option parsing at the first value option.
                    split_option = re.fullmatch(r'-(?!-)[^auCSP]*S(.*)', option)
                    split_value = None
                    consumed = 1
                    if option == '--split-string' or (
                        split_option and not split_option[1]
                    ):
                        if i + 1 >= len(words):
                            return []
                        split_value = words[i + 1]
                        consumed = 2
                    elif option.startswith('--split-string='):
                        split_value = option.partition('=')[2]
                    elif split_option:
                        split_value = split_option[1]
                    if split_value is not None:
                        try:
                            split_words = shlex.split(split_value)
                        except ValueError:
                            split_words = split_value.split()
                        words[i:i + consumed] = split_words
                        continue
                i += 2 if _takes_value(option, _WRAPPERS[name]) else 1
            if name == 'nice' and i < len(words) and words[i].lstrip('-').isdigit():
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
