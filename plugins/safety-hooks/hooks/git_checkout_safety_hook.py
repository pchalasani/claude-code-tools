#!/usr/bin/env python3
import functools
import os
import re
import shlex
import subprocess
import sys
from typing import Dict, List, Optional, Set, Tuple

# Add plugin hooks directory to Python path for local imports
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    hooks_dir = os.path.join(PLUGIN_ROOT, 'hooks')
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

# git's own global options that consume the NEXT token as their value. They sit
# between "git" and the subcommand, so the subcommand is not always argv[1].
_GIT_GLOBAL_VALUE_OPTS = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--super-prefix", "--config-env", "--attr-source",
}

# These options print information and exit instead of dispatching a subcommand.
_GIT_TERMINAL_OPTS = {
    "-h", "-v", "--help", "--version", "--html-path", "--man-path",
    "--info-path",
}

# Options that point git at a repository this module cannot compute. Honouring
# them properly also means handling GIT_DIR / GIT_WORK_TREE and their
# composition with -C; rather than guess, mark the target unknown and let the
# caller decline to probe anything.
_OPAQUE_TARGET_OPTS = ("--work-tree", "--git-dir")

# Short checkout options whose remaining characters are their argument, rather
# than more options in the same cluster. For example, ``-bfeature`` means
# ``-b feature``; the ``f`` in ``feature`` is not ``--force``.
_CHECKOUT_SHORT_VALUE_OPTS = {"b", "B"}

# The discard check reads the options of `git reset`, `git clean` and
# `git restore` from `git <subcommand> --help-all`, which git prints from the
# same table it parses the options with. An option line looks like
#     -s, --[no-]source <tree-ish>
#     -d                    remove whole directories
#     --[no-]recurse-submodules[=<reset>]
# Option lines start a few spaces in. The description starts after two or more
# spaces, or on the next line, which starts far deeper.
_HELP_OPTION_LINE = re.compile(r"^ {1,8}-")
_HELP_OPTION_SPEC = re.compile(
    r"^(?:-(?P<short>[^-\s,])(?:,\s*)?)?"
    r"(?:--(?P<negatable>\[no-\])?(?P<long>[^\s\[=<]+))?"
    r"(?P<value>.*)$"
)

# An option with a REQUIRED value takes the rest of its token, or else the next
# token: in ``--pathspec-from-file -S`` the ``-S`` is a file name. An option
# with an OPTIONAL value takes only a value attached to it.
_REQUIRED = "required"
_OPTIONAL = "optional"

# git handles these itself, and its help does not list them.
_GIT_HELP_LONG_OPTIONS = {"--help": None, "--help-all": None}
_GIT_HELP_SHORT_OPTIONS = {"h": None}

# Shell expansions cannot be reproduced reliably after shlex has split the
# command. Tilde expansion is handled separately because it has deterministic
# local semantics for ordinary home-directory paths.
_UNRESOLVABLE_CHDIR_CHARS = frozenset("$`*?[")

# Pathspecs that mean "everything", as opposed to a named file.
_UNBOUNDED_PATHSPECS = {".", "./", "*", ":/"}
_FORCE_LONG_OPTIONS = {"--f", "--fo", "--for", "--forc", "--force"}

_ENV_VALUE_OPTS = {
    "-a", "--argv0", "-u", "--unset", "-C", "--chdir", "-S",
    "--split-string",
}
_ENV_SHORT_VALUE_OPTS = {"a", "u", "C", "P", "S"}
_REPOSITORY_ROUTING_VARS = {"GIT_DIR", "GIT_WORK_TREE"}
_LEADING_REDIRECTION = re.compile(r"^\d*[<>].+")
_LEADING_REDIRECTION_OPERATOR = re.compile(r"^\d*(?:<>|>>|>|<|>&|<&)\Z")

_DANGER_TEMPLATE = (
    "⚠️  DANGEROUS COMMAND DETECTED!\n\n{message}\n\n"
    "This command will destroy uncommitted work without warning.\n\n"
    "Safer alternatives:\n"
    "- Use 'git stash' to save changes temporarily\n"
    "- Use 'git diff' to see what would be lost\n"
    "- Use 'git restore --staged' to unstage without discarding"
)

_UNVERIFIED_REPOSITORY_MESSAGE = (
    "Could not safely resolve the target repository to verify its status.\n"
    "Please manually check 'git status' in the target repository before "
    "proceeding."
)


def _danger(message: str) -> str:
    return _DANGER_TEMPLATE.format(message=message)


def _tokenize(command: str) -> List[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _raw_tokens(command: str) -> Optional[List[str]]:
    """Tokenize while retaining quotes and escapes around token contents."""
    try:
        lexer = shlex.shlex(command, posix=False)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return None


def _is_assignment(token: str) -> bool:
    """Return whether a token is a simple shell assignment word."""
    name, separator, _ = token.partition("=")
    return bool(
        separator
        and name
        and (name[0].isalpha() or name[0] == "_")
        and all(char.isalnum() or char == "_" for char in name)
    )


def _is_repository_routing_assignment(token: str) -> bool:
    """Return whether an assignment changes which repository Git targets."""
    return token.partition("=")[0] in _REPOSITORY_ROUTING_VARS


def _env_short_value_option(
    token: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Return the first value-taking short env option and attached value."""
    if not token.startswith("-") or token.startswith("--"):
        return None, None
    cluster = token[1:]
    for position, option in enumerate(cluster):
        if option in _ENV_SHORT_VALUE_OPTS:
            return option, cluster[position + 1:] or None
    return None, None


def _expand_env_split(
    tokens: List[str],
) -> Tuple[Optional[List[str]], bool]:
    """Expand an ``env -S`` command string into its effective argv."""
    index = 0
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    if index >= len(tokens) or os.path.basename(tokens[index]) != "env":
        return tokens, False

    index += 1
    while index < len(tokens):
        token = tokens[index]
        value: Optional[str] = None
        consumed = 1
        if token in ("-S", "--split-string"):
            if index + 1 >= len(tokens):
                return None, False
            value = tokens[index + 1]
            consumed = 2
        elif token.startswith("--split-string="):
            value = token.split("=", 1)[1]
        elif token.startswith("-") and not token.startswith("--"):
            option, attached = _env_short_value_option(token)
            if option == "S":
                value = attached
                if not value:
                    if index + 1 >= len(tokens):
                        return None, False
                    value = tokens[index + 1]
                    consumed = 2
            elif option is not None:
                if attached is None:
                    if index + 1 >= len(tokens):
                        return None, False
                    index += 2
                else:
                    index += 1
                continue
        if value is not None:
            try:
                expanded = shlex.split(value)
            except ValueError:
                return None, False
            return tokens[:index] + expanded + tokens[index + consumed:], True
        if _is_assignment(token):
            index += 1
            continue
        if token in _ENV_VALUE_OPTS:
            if index + 1 >= len(tokens):
                return None, False
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return tokens, False


def _executable_index(tokens: List[str]) -> int:
    index = 0
    while index < len(tokens):
        if _LEADING_REDIRECTION.match(tokens[index]):
            index += 1
            continue
        if _LEADING_REDIRECTION_OPERATOR.match(tokens[index]):
            index += 2
            continue
        break
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    if index >= len(tokens) or os.path.basename(tokens[index]) != "env":
        return index
    index += 1
    while index < len(tokens):
        token = tokens[index]
        if _is_assignment(token):
            index += 1
            continue
        if token in _ENV_VALUE_OPTS:
            index += 2
            continue
        option, attached = _env_short_value_option(token)
        if option is not None:
            index += 1 if attached is not None else 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return index


def _split_compound_command(command: str) -> List[str]:
    """Split on shell operators outside quotes and backslash escapes."""
    subcommands: List[str] = []
    start = 0
    quote: Optional[str] = None
    escaped = False
    index = 0

    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif char in ("'", '"'):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif (quote is None and char == "&" and index > 0
              and command[index - 1] in "<>"):
            pass
        elif quote is None and char in ";&|\n":
            subcommand = command[start:index].strip()
            if subcommand:
                subcommands.append(subcommand)
            if char in "&|" and command[index:index + 2] == char * 2:
                index += 1
            start = index + 1
        index += 1

    subcommand = command[start:].strip()
    if subcommand:
        subcommands.append(subcommand)
    return subcommands


def _resolve_chdir(
    current_dir: str,
    value: str,
    expand_tilde: Optional[bool] = False,
) -> Optional[str]:
    """Resolve one ``git -C`` value without evaluating shell syntax."""
    if value.startswith("~") and expand_tilde is None:
        return None
    expanded = os.path.expanduser(value) if expand_tilde else value
    if expand_tilde and expanded.startswith("~"):
        return None
    if any(char in expanded for char in _UNRESOLVABLE_CHDIR_CHARS):
        return None
    if expanded.startswith(("<(", ">(")):
        return None
    if os.path.isabs(expanded):
        return expanded
    return os.path.join(current_dir, expanded)


def _env_chdir(
    tokens: List[str],
    raw_tokens: Optional[List[str]],
) -> Optional[str]:
    """Resolve a supported ``env -C`` prefix before its command."""
    current: Optional[str] = os.getcwd()
    index = 0
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    if index >= len(tokens) or os.path.basename(tokens[index]) != "env":
        return current

    index += 1
    while index < len(tokens):
        token = tokens[index]
        if _is_assignment(token):
            index += 1
            continue
        if token == "--":
            break
        name, separator, attached = token.partition("=")
        value: Optional[str] = attached if separator else None
        value_index = index
        if name in ("-S", "--split-string"):
            break
        if name in ("-C", "--chdir"):
            if value is None:
                index += 1
                if index >= len(tokens):
                    return None
                value = tokens[index]
                value_index = index
            if current is not None:
                raw_value = raw_tokens[value_index] if raw_tokens else None
                expand_tilde = raw_value.startswith("~") if raw_value else None
                current = _resolve_chdir(current, value, expand_tilde)
            index += 1
            continue
        if token.startswith("-C") and token != "-C":
            value = token[2:]
            if current is not None:
                current = _resolve_chdir(current, value)
            index += 1
            continue
        option, attached = _env_short_value_option(token)
        if option is not None:
            value = attached
            if value is None:
                index += 1
                if index >= len(tokens):
                    return None
                value = tokens[index]
            if option == "C" and current is not None:
                current = _resolve_chdir(current, value)
            if option == "S":
                break
            index += 1
            continue
        if token in _ENV_VALUE_OPTS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return current


def parse_git_checkout(
    command: str,
    subcommand: str = "checkout",
) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Split ``git [global-opts] checkout <args...>`` into (args, repo_dir).
    Pass ``subcommand`` to split another subcommand, such as ``reset``.

    Returns ``(None, None)`` when the command is not a git checkout at all.

    Returns ``(args, None)`` when it IS a git checkout but the repository it
    would act on cannot be determined -- an unexpanded ``-C`` value such as
    ``-C "$REPO"``, or ``--git-dir`` / ``--work-tree``. Checks that read only
    the command still apply; nothing may probe a directory on that basis,
    least of all the caller's own, which is a different repository.
    """
    tokens = _tokenize(command)
    raw_tokens = _raw_tokens(command)
    if raw_tokens is not None and len(raw_tokens) != len(tokens):
        raw_tokens = None
    chdir = _env_chdir(tokens, raw_tokens)
    tokens, env_split = _expand_env_split(tokens)
    if tokens is None:
        return None, None
    if env_split:
        raw_tokens = None
        chdir = _env_chdir(tokens, raw_tokens)
    if raw_tokens is not None and len(raw_tokens) != len(tokens):
        raw_tokens = None
    executable = _executable_index(tokens)
    if executable >= len(tokens) or os.path.basename(tokens[executable]) != "git":
        return None, None
    if any(
        _is_repository_routing_assignment(token)
        for token in tokens[:executable]
    ):
        chdir = None

    opaque = False
    i = executable + 1
    while i < len(tokens):
        token = tokens[i]
        if token in _GIT_TERMINAL_OPTS:
            return None, None
        if token.split("=", 1)[0] in _OPAQUE_TARGET_OPTS:
            opaque = True
        if token in _GIT_GLOBAL_VALUE_OPTS:
            # git applies repeated -C cumulatively, each relative to the last.
            if token == "-C" and i + 1 < len(tokens):
                value = tokens[i + 1]
                if chdir is not None:
                    raw_value = raw_tokens[i + 1] if raw_tokens else None
                    expand_tilde = (
                        raw_value.startswith("~") if raw_value else None
                    )
                    chdir = _resolve_chdir(chdir, value, expand_tilde)
            i += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            if chdir is not None:
                chdir = _resolve_chdir(chdir, token[2:])
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        break

    if i >= len(tokens) or tokens[i] != subcommand:
        return None, None

    args = tokens[i + 1:]
    if opaque or chdir is None or not os.path.isdir(chdir):
        return args, None
    return args, chdir


def _option_flags(tokens: List[str]) -> Set[str]:
    """Return option flags without parsing attached option arguments as flags."""
    flags: Set[str] = set()
    for token in tokens:
        if token.startswith("--"):
            name = token.split("=", 1)[0]
            flags.add("--force" if name in _FORCE_LONG_OPTIONS else name)
        elif token.startswith("-") and len(token) > 1:
            for char in token[1:]:
                flags.add("-" + char)
                if char in _CHECKOUT_SHORT_VALUE_OPTS:
                    break
    return flags


def check_git_checkout_command(command: str) -> Tuple[bool, Optional[str]]:
    """
    Check if a git checkout command is safe to execute.
    Handles compound commands (e.g., "cd /path && git checkout branch").

    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Check each subcommand in compound commands
    for subcmd in _split_compound_command(command):
        for check in (_check_single_git_checkout_command,
                      _check_single_git_discard_command):
            result = check(subcmd)
            should_block, reason = result
            if should_block:
                return result

    return False, None


def _check_single_git_discard_command(
    command: str,
) -> Tuple[bool, Optional[str]]:
    """
    Block `git reset --hard`, `git clean -f` and `git restore` of the working
    tree, which discard uncommitted changes and untracked files.

    Git accepts any unambiguous prefix of a long option, so `--har` is
    `--hard`. The check reads git's own option list and blocks a long option
    that is not a full name.
    """
    for subcommand in ("reset", "restore", "clean"):
        args, _ = parse_git_checkout(command, subcommand)
        if args is None:
            continue
        head = args[:args.index("--")] if "--" in args else args
        problem, options = _read_options(subcommand, head)
        if problem:
            return True, problem
        names = [name for name, _ in options]
        if subcommand == "reset" and "--hard" in names:
            return True, _danger(
                "'git reset --hard' DISCARDS all uncommitted changes!")
        # `git restore <path>` is the newer spelling of
        # `git checkout -- <path>` and discards the same edits.
        if (subcommand == "restore"
                and not {"-h", "--help"} & set(names)
                and _restore_writes_worktree(names)):
            return True, _danger(
                "'git restore' DISCARDS uncommitted changes in the working "
                "tree!")
        if subcommand == "clean" and _clean_deletes(names):
            return True, _danger(
                "'git clean -f' DELETES untracked files, which git cannot "
                "recover!")
        return False, None
    return False, None


def _parse_git_help(
    text: str,
) -> Tuple[Dict[str, Optional[str]], Dict[str, Optional[str]]]:
    """
    Return the long and short options in `git <subcommand> -h` output.

    Each one maps an option to the value it takes: None, _REQUIRED or
    _OPTIONAL. A ``--[no-]`` option also adds its ``--no-`` form, which takes
    no value.
    """
    long_options: Dict[str, Optional[str]] = {}
    short_options: Dict[str, Optional[str]] = {}
    for line in text.splitlines():
        if not _HELP_OPTION_LINE.match(line):
            continue
        spec = re.split(r"\s{2,}", line.strip(), maxsplit=1)[0]
        match = _HELP_OPTION_SPEC.match(spec)
        value = match.group("value").strip()
        kind = (None if not value
                else _OPTIONAL if value.startswith("[") else _REQUIRED)
        if match.group("short"):
            short_options[match.group("short")] = kind
        if match.group("long"):
            long_options["--" + match.group("long")] = kind
            if match.group("negatable"):
                long_options["--no-" + match.group("long")] = None
    return long_options, short_options


@functools.lru_cache(maxsize=None)
def _git_options(
    subcommand: str,
) -> Optional[Tuple[Dict[str, Optional[str]], Dict[str, Optional[str]]]]:
    """Return the options that `git <subcommand>` lists, or None."""
    try:
        result = subprocess.run(
            ["git", subcommand, "--help-all"], capture_output=True,
            text=True, timeout=10, env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.SubprocessError):
        return None
    long_options, short_options = _parse_git_help(
        result.stdout + result.stderr)
    if not long_options and not short_options:
        return None
    return long_options, short_options


def _read_options(
    subcommand: str, head: List[str],
) -> Tuple[Optional[str], List[Tuple[str, str]]]:
    """
    Return the options in `head` in order, each with its value.

    The options are read as git reads them: an option with a value takes it,
    so in ``-e -n`` the ``-n`` is a pattern, and ``--end-of-options`` ends the
    options as ``--`` does. The first item of the result is a message when an
    option is not one that git lists by its full name.
    """
    known = _git_options(subcommand)
    if known is None:
        return ("Could not read git's option list ('git {0} -h'), so this "
                "check cannot read the 'git {0}' command."
                .format(subcommand)), []
    listed_long, listed_short = known
    long_options = {**_GIT_HELP_LONG_OPTIONS, **listed_long}
    short_options = {**_GIT_HELP_SHORT_OPTIONS, **listed_short}
    options: List[Tuple[str, str]] = []
    tokens = iter(head)
    for token in tokens:
        if token == "--end-of-options":
            break
        if token.startswith("--"):
            name, attached, value = token.partition("=")
            if name not in long_options:
                return _not_a_full_name(subcommand, name, listed_long), []
            if long_options[name] == _REQUIRED and not attached:
                value = next(tokens, "")
            options.append((name, value))
        elif token.startswith("-") and len(token) > 1:
            for index, char in enumerate(token[1:], start=1):
                if char not in short_options:
                    return ("'git {0} -h' lists no option '-{1}'. Write each "
                            "option as git lists it.".format(subcommand, char)
                            ), []
                kind = short_options[char]
                if kind is None:
                    options.append(("-" + char, ""))
                    continue
                value = token[index + 1:]
                if not value and kind == _REQUIRED:
                    value = next(tokens, "")
                options.append(("-" + char, value))
                break
    return None, options


def _not_a_full_name(
    subcommand: str, name: str, listed_long: Dict[str, Optional[str]],
) -> str:
    """Return the message for a long option that is not a full name."""
    matches = sorted(option for option in listed_long if option.startswith(name))
    if len(matches) == 1:
        return ("Git reads '{0}' as '{1}', because it accepts any unambiguous "
                "prefix of an option name. This check reads only full names. "
                "Spell the option out: '{1}'.".format(name, matches[0]))
    if matches:
        return ("'{0}' is the start of more than one option: {1}. Spell out "
                "the option you mean.".format(
                    name, ", ".join("'{0}'".format(m) for m in matches)))
    return ("'git {0} -h' lists no option '{1}'. Write each option as git "
            "lists it.".format(subcommand, name))


def _clean_deletes(names: List[str]) -> bool:
    """
    Return True if `git clean` with these options deletes files.

    The last of ``--dry-run`` and ``--no-dry-run`` wins. Force counts once it
    is given: ``-f --no-force`` still deletes when ``clean.requireForce`` is
    false.
    """
    force = dry_run = False
    for name in names:
        if name in ("-f", "--force"):
            force = True
        elif name in ("-n", "--dry-run"):
            dry_run = True
        elif name == "--no-dry-run":
            dry_run = False
    return force and not dry_run


def _restore_writes_worktree(names: List[str]) -> bool:
    """
    Return True unless the `git restore` options name only the index.

    `git restore` restores the working tree unless `--staged` is given
    without `--worktree`. `--staged` alone only unstages, so every edit
    stays on disk. The last of each option and its ``--no-`` form wins.
    """
    staged = worktree = False
    for name in names:
        if name in ("-S", "--staged"):
            staged = True
        elif name == "--no-staged":
            staged = False
        elif name in ("-W", "--worktree"):
            worktree = True
        elif name == "--no-worktree":
            worktree = False
    return worktree or not staged


def _check_single_git_checkout_command(
    command: str,
) -> Tuple[bool, Optional[str]]:
    """
    Check a single (non-compound) git checkout command.
    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Check if it's a git checkout command. This also recognises the
    # `git -C <dir> checkout ...` form, which a "starts with git checkout"
    # test misses entirely.
    args, repo_dir = parse_git_checkout(command)
    if args is None:
        return False, None

    # Everything before a `--` separator is options and refs; everything after
    # is pathspecs, even if it looks like an option.
    if "--" in args:
        separator = args.index("--")
        head, pathspecs = args[:separator], args[separator + 1:]
    else:
        separator = None
        head, pathspecs = args, []

    flags = _option_flags(head)

    if "-h" in flags or "--help" in flags:
        return False, None

    # ALWAYS block these dangerous patterns.
    #
    # -f is checked BEFORE the -b allowance below. Testing for "-b" first --
    # and as a substring of the whole command -- is what let `git checkout -f
    # -b <name>` and `git checkout . -b <name>` through, and disarmed the guard
    # for any command merely containing "-b", e.g. `git checkout --
    # src/my-button.ts`.
    if "-f" in flags or "--force" in flags:
        return True, _danger(
            "'git checkout -f' FORCES checkout and DISCARDS all uncommitted changes!")

    if separator is not None:
        if any(p in _UNBOUNDED_PATHSPECS for p in pathspecs):
            return True, _danger(
                "This will DISCARD ALL changes in current directory!")
        return True, _danger(
            "This will overwrite your local file with version from another "
            "branch/commit!"
        )

    positionals = [a for a in args if not a.startswith("-")]
    if any(p in _UNBOUNDED_PATHSPECS for p in positionals):
        return True, _danger(
            "'git checkout .' will DISCARD ALL changes in current directory!")

    # Creating a branch carries uncommitted work across rather than discarding
    # it, so it stays exempt from the uncommitted-changes prompt below.
    if "-b" in flags:
        return False, None

    # Everything above reads only the COMMAND. Everything below reads a
    # REPOSITORY, and repo_dir is None when we could not work out which one.
    # Probing the caller's own repo instead would report a verdict, and a list
    # of at-risk files, belonging to a repository the command never touches.
    if repo_dir is None:
        return True, _UNVERIFIED_REPOSITORY_MESSAGE

    try:
        # First, check if there are any uncommitted changes
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=repo_dir
        )
        if status_result.returncode != 0:
            return True, _UNVERIFIED_REPOSITORY_MESSAGE

        has_changes = bool(status_result.stdout.strip())

        # Get more detailed status if there are changes
        if has_changes:
            # Get the list of modified files
            diff_result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True,
                text=True,
                cwd=repo_dir
            )

            unstaged_result = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True,
                text=True,
                cwd=repo_dir
            )

            # Count changes
            all_changes = status_result.stdout.strip().split('\n')
            modified_files = [f for f in all_changes if f.strip()]
            num_changes = len(modified_files)

            # Build warning message
            warning = (
                f"WARNING: You have {num_changes} uncommitted change(s) that "
                "may be lost!\n\n"
            )

            if modified_files:
                warning += "Modified files:\n"
                for change in modified_files[:10]:  # Show first 10
                    warning += f"  {change}\n"
                if num_changes > 10:
                    warning += f"  ... and {num_changes - 10} more\n"

            warning += "\nOptions:\n"
            warning += "1. Stash changes: git stash\n"
            warning += "2. Commit changes: git commit -am 'your message'\n"
            warning += "3. Discard changes: git restore <files>\n"
            warning += "4. Use 'git switch' instead for safer branch switching\n"

            # Special warning for checkout .
            if "checkout ." in command or "checkout -- ." in command:
                warning += (
                    "\n⚠️  DANGER: 'git checkout .' will DISCARD ALL "
                    "local changes!"
                )

            return True, warning

    except Exception as error:
        # If we can't determine status, err on the side of caution
        reason = (
            f"Could not verify repository status: {str(error)}\n"
            "Please manually check 'git status' before proceeding."
        )
        return True, reason

    # No uncommitted changes, safe to proceed
    return False, None


# If run as a standalone script
if __name__ == "__main__":
    import json
    import sys

    data = json.load(sys.stdin)

    # Check if this is a Bash tool call. Fold case first: an exact-case
    # mismatch would approve the command without checking it.
    tool_name = (data.get("tool_name") or "").lower()
    if tool_name != "bash":
        print(json.dumps({"decision": "approve"}))
        sys.exit(0)

    # Get the command being executed
    command = data.get("tool_input", {}).get("command", "")

    should_block, reason = check_git_checkout_command(command)

    if should_block:
        print(json.dumps({
            "decision": "block",
            "reason": reason
        }))
    else:
        print(json.dumps({"decision": "approve"}))

    sys.exit(0)
