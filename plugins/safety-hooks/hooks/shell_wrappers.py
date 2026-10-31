"""Shared parsing of command wrappers and their literal arguments."""

import re


# Commands that run the command after them: `sudo rm`, `xargs rm`, mapped to
# their options that take a separate value. Each wrapper has its own set: `-n`
# takes a value for nice and xargs, but is a flag for sudo.
_WRAPPERS = {
    'sudo': {'-u', '-g', '-C', '-D', '-p', '-R', '-r', '-t', '-T', '-U', '-h',
             '--user', '--group', '--close-from', '--chdir', '--prompt',
             '--chroot', '--role', '--type', '--command-timeout',
             '--other-user', '--host'},
    'doas': {'-u', '-C', '-a'},
    'env': {'-u', '-C', '-S', '-P', '-a', '--unset', '--chdir',
            '--split-string', '--argv0'},
    # GNU --replace/--max-lines take optional attached values, unlike -I/-L.
    'xargs': {'-I', '-L', '-J', '-R', '-S', '-n', '-P', '-s', '-d', '-a', '-E',
              '--max-args',
              '--max-procs', '--max-chars', '--delimiter', '--arg-file',
              '--process-slot-var'},
    'timeout': {'-s', '-k', '--signal', '--kill-after'},
    'gtimeout': {'-s', '-k', '--signal', '--kill-after'},
    'nice': {'-n', '--adjustment'},
    'time': {'-f', '-o', '--format', '--output'},
    'exec': {'-a'},
    'caffeinate': {'-t', '-w'},
    'command': set(),
    'builtin': set(),
    'nohup': set(),
}
# These modes do not execute the supplied command: lookup, policy queries,
# or editing a file whose name may happen to be rm.
_LOOKUP_OPTIONS = {
    'command': {'-v', '-V'},
    'sudo': {'-l', '--list', '-e', '--edit'},
    'doas': {'-C'},
}


def _takes_value(
    option: str, value_options: set[str], optional_short: str = ''
) -> bool:
    """Whether a wrapper option consumes the next word, as `-u` in `sudo -u root`.

    A short cluster ends at its first value-taking option. In `-uroot`,
    `root` is attached to `-u`; in `-nu root`, `-u` consumes the next word.
    Optional short values are attached-only and also end the cluster.
    """
    if option.startswith('--'):
        return option in value_options
    if option.startswith('-'):
        for index, letter in enumerate(option[1:], start=1):
            if letter in optional_short:
                return False
            if f'-{letter}' in value_options:
                return index == len(option) - 1
    return False



def _split_env_string(value: str) -> list[str]:
    """Split literal env -S text using its own quote and escape rules.

    Runtime environment-variable expansion is not evaluated by this scanner.
    """
    escapes = {'f': '\f', 'n': '\n', 'r': '\r', 't': '\t', 'v': '\v',
               '#': '#', '$': '$', '_': ' ', '"': '"', "'": "'", '\\': '\\'}
    words: list[str] = []
    word: list[str] = []
    quote = None
    started = False
    index = 0
    while index < len(value):
        character = value[index]
        if character == '\\':
            index += 1
            if index == len(value):
                raise ValueError('Trailing env split-string escape')
            escaped = value[index]
            if quote == "'" and escaped not in "'\\":
                word.extend(('\\', escaped))
            elif escaped == 'c' and quote is None:
                break
            elif escaped == '_' and quote is None:
                if started:
                    words.append(''.join(word))
                word = []
                started = False
                index += 1
                continue
            elif escaped in escapes:
                word.append(escapes[escaped])
            else:
                raise ValueError('Invalid env split-string escape')
            started = True
        elif quote is None and character in ' \t\n\r\v\f':
            if started:
                words.append(''.join(word))
            word = []
            started = False
        elif quote is None and character == '#' and not started:
            break
        elif character in "'\"":
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            else:
                word.append(character)
            started = True
        else:
            word.append(character)
            started = True
        index += 1
    if quote is not None:
        raise ValueError('Unclosed env split-string quote')
    if started:
        words.append(''.join(word))
    return words


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
                            split_words = _split_env_string(split_value)
                        except ValueError:
                            split_words = split_value.split()
                        words[i:i + consumed] = split_words
                        continue
                takes_value = _takes_value(
                    option, _WRAPPERS[name], 'iel' if name == 'xargs' else ''
                )
                i += 2 if takes_value else 1
            if name == 'nice' and i < len(words) and words[i].lstrip('-').isdigit():
                i += 1
            if name in ('timeout', 'gtimeout'):
                i += 1  # Required duration precedes the command.
        else:
            break
    return words[i:]


def _strip_literal_redirections(command: str) -> str:
    """Remove literal redirections while preserving quoted argument text."""
    # Separate an attached &> from the command, without splitting >&1.
    tokens = list(re.finditer(
        r'''(?:(?!&>)[^\s'"<>\\]|\\.|'[^']*'|"(?:[^"\\]|\\.)*")+|&(?=>)|[<>]+''',
        command, re.DOTALL,
    ))
    kept: list[re.Match[str]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if re.fullmatch(r'[<>]+', token[0]):
            if kept and kept[-1].end() == token.start() and (
                    (kept[-1][0].isascii() and kept[-1][0].isdigit())
                    or re.fullmatch(r'\{[a-zA-Z_][a-zA-Z_0-9]*\}', kept[-1][0])
                    or (kept[-1][0] == '&' and token[0].startswith('>'))):
                kept.pop()  # A numeric/named descriptor, or '&' in '&>'/'&>>'.
            index += 2  # Operator and its destination word.
        else:
            kept.append(token)
            index += 1
    return ' '.join(token[0] for token in kept)


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
