#!/usr/bin/env python3
"""Protect dotenv files from commands that could expose their contents."""
import fnmatch
import os
import re
import shlex
import string
from typing import List, Optional, Tuple

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
    'source', '.',
}

_SEGMENT_SEPARATORS = {';', '&&', '||', '|', '&', '(', ')', '\n'}
_DOTENV_PATH = re.compile(
    r'(?:^|/)\.env(?=$|[^A-Za-z0-9_])', re.IGNORECASE)
_BARE_ENV = ('env', './env')
_ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_ENV_VALUE_OPTS = {'-a', '--argv0', '-u', '--unset', '-C', '--chdir', '-P'}
_ENV_SHORT_VALUE_OPTS = 'auCP'
_REDIRECTS = {'<', '>', '>>', '>|', '&>', '&>>', '<>', '>&'}
_SHELLS = {'sh', 'bash', 'zsh', 'dash', 'ksh'}
_MAX_NESTING = 8
_SEARCH_PATTERN_OPTS = {'e', '--regexp'}
_SEARCH_FILE_OPTS = {
    'grep': {'f', '--exclude-from', '--file', '--include'},
    'rg': {'f', 'g', '--file', '--glob', '--iglob', '--ignore-file'},
    'ag': set(),
    'ack': set(),
}
_SEARCH_GLOB_OPTS = {
    'grep': {'--include'},
    'rg': {'g', '--glob', '--iglob'},
}
_SEARCH_SHORT_VALUE_OPTS = {
    'grep': {'A', 'B', 'C', 'D', 'd', 'm'},
    'rg': {'A', 'B', 'C', 'E', 'j', 'M', 'm', 'r', 't', 'T'},
}
_SEARCH_LONG_VALUE_OPTS = {
    'grep': {
        '--after-context', '--before-context', '--binary-files', '--context',
        '--devices', '--directories', '--exclude', '--exclude-dir', '--label',
        '--group-separator', '--max-count',
    },
    'rg': {
        '--after-context', '--before-context', '--color', '--colors',
        '--context', '--context-separator', '--dfa-size-limit', '--encoding',
        '--engine', '--field-context-separator', '--field-match-separator',
        '--hostname-bin', '--hyperlink-format', '--max-columns', '--max-count',
        '--max-depth', '--max-filesize', '--path-separator', '--pre',
        '--pre-glob', '--regex-size-limit', '--replace', '--sort', '--sortr',
        '--threads', '--type', '--type-add', '--type-clear', '--type-not',
    },
}
_POSIX_GLOB_CHARS = {
    'alnum': string.ascii_letters + string.digits,
    'alpha': string.ascii_letters,
    'ascii': ''.join(chr(code) for code in range(128)),
    'blank': ' \t',
    'cntrl': ''.join(chr(code) for code in (*range(32), 127)),
    'digit': string.digits,
    'graph': string.ascii_letters + string.digits + string.punctuation,
    'lower': string.ascii_lowercase,
    'print': ''.join(chr(code) for code in range(32, 127)),
    'punct': string.punctuation,
    'space': string.whitespace,
    'upper': string.ascii_uppercase,
    'word': string.ascii_letters + string.digits + '_',
    'xdigit': string.hexdigits,
}

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


def _names_dotenv(token: str) -> bool:
    """True when this argument NAMES a dotenv file (.env, .env.local, a/.env)."""
    stripped = token.strip('\'"')
    if _DOTENV_PATH.search(stripped):
        return True
    for operator in (':-', ':=', ':+', ':?', '-', '=', '+', '?'):
        _prefix, separator, fallback = stripped.partition(operator)
        if separator and _DOTENV_PATH.search(fallback.rstrip('}')):
            return True
    return False


def _glob_names_dotenv(pattern: str) -> bool:
    """Return whether an inclusion glob can select a dotenv basename."""
    tokens: List[str] = []
    basename_pattern = pattern.rsplit('/', 1)[-1]
    index = 0
    while index < len(basename_pattern):
        character = basename_pattern[index]
        if character == '*':
            if not tokens or tokens[-1] != '*':
                tokens.append(character)
            index += 1
            continue
        if character == '[':
            end = index + 1
            if end < len(basename_pattern) and basename_pattern[end] in '!^':
                end += 1
            if end < len(basename_pattern) and basename_pattern[end] == ']':
                end += 1
            end = basename_pattern.find(']', end)
            while (end != -1 and basename_pattern[end - 1] == ':'
                   and end + 1 < len(basename_pattern)):
                end = basename_pattern.find(']', end + 1)
            if end != -1:
                tokens.append(basename_pattern[index:end + 1])
                index = end + 1
                continue
        tokens.append(character)
        index += 1
    alphabet = {chr(code) for code in range(32, 127)}
    alphabet.update(basename_pattern)
    # Third state component: whether a non-'*' token has spelled part of
    # the '.env' core. A bare '*' can expand to anything, so a match that
    # exists only because '*' spelled out '.env' (e.g. '*.ts' matching
    # '.env.ts') does not count as the pattern naming a dotenv.
    pending = [(0, 0, False)]
    seen = set()
    while pending:
        token_index, dotenv_state, contributed = pending.pop()
        state = (token_index, dotenv_state, contributed)
        if state in seen:
            continue
        seen.add(state)
        if token_index == len(tokens):
            if dotenv_state in {4, 5} and contributed:
                return True
            continue
        token = tokens[token_index]
        if token == '*':
            pending.append((token_index + 1, dotenv_state, contributed))
        for candidate in alphabet:
            negated = token.startswith(('[!', '[^'))
            bracket_body = token[2:-1] if negated else token[1:-1]
            classes = re.findall(r'\[:([a-z]+):\]', bracket_body)
            ordinary = re.sub(r'\[:[a-z]+:\]', '', bracket_body)
            bracket_match = any(
                candidate in _POSIX_GLOB_CHARS.get(name, '')
                for name in classes
            )
            if ordinary:
                bracket_match |= fnmatch.fnmatchcase(
                    candidate, f'[{ordinary}]')
            if negated:
                bracket_match = not bracket_match
            matches = (
                token in {'*', '?'}
                or token.startswith('[') and bracket_match
                or token == candidate
            )
            if not matches:
                continue
            if dotenv_state < 4:
                expected = '.env'[dotenv_state]
                if candidate.lower() != expected:
                    continue
                next_state = dotenv_state + 1
            elif dotenv_state == 4:
                if candidate.isalnum() or candidate == '_':
                    continue
                next_state = 5
            else:
                next_state = 5
            next_token = token_index if token == '*' else token_index + 1
            next_contributed = contributed or (
                token != '*' and dotenv_state < 4)
            pending.append((next_token, next_state, next_contributed))
    return False


def shell_segments(command: str) -> Optional[List[List[str]]]:
    """
    Split a command into shell segments, respecting quotes.

    Returns a list of token lists, or None if the command cannot be tokenised.
    """
    # shlex removes quotes, so protect quoted or escaped redirect characters
    # before parsing. Otherwise a literal quoted ">" is indistinguishable from
    # an output operator in the resulting token list.
    protected = []
    quote = None
    escaped = False
    literal_substitution = 0
    literal_pending = False
    for char in command:
        if literal_substitution:
            if char == '(':
                literal_substitution += 1
            elif char == ')':
                literal_substitution -= 1
            protected.append(
                '\ue002' if char in ';&|()<>\n' else char)
            continue
        if literal_pending:
            literal_pending = False
            if char == '(':
                literal_substitution = 1
                protected.append('\ue002')
                continue
        if escaped:
            protected.append('\ue000' if char == '>' else
                             '\ue001' if char == '<' else char)
            literal_pending = char == '$'
            escaped = False
        elif char == '\\' and quote != "'":
            protected.append(char)
            escaped = True
        elif char in "'\"" and (quote is None or quote == char):
            quote = None if quote == char else char
            protected.append(char)
        elif quote and char in '<>':
            protected.append('\ue000' if char == '>' else '\ue001')
        else:
            protected.append(char)
    try:
        lexer = shlex.shlex(
            ''.join(protected), posix=True, punctuation_chars=';&|()<>\n')
        lexer.whitespace = ' \t\r'
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


def _backtick_commands(command: str) -> List[str]:
    """Return executable backtick bodies, ignoring single-quoted backticks."""
    bodies = []
    start = None
    single_quoted = False
    double_quoted = False
    escaped = False
    for index, char in enumerate(command):
        if escaped:
            escaped = False
        elif char == '\\' and not single_quoted:
            escaped = True
        elif char == "'" and not double_quoted:
            single_quoted = not single_quoted
        elif char == '"' and not single_quoted:
            double_quoted = not double_quoted
        elif char == '`' and not single_quoted:
            if start is None:
                start = index + 1
            else:
                bodies.append(command[start:index])
                start = None
    return bodies


def _substitution_commands(command: str) -> List[str]:
    """Return executable command-substitution bodies in this shell text."""
    bodies = []
    index = 0
    single_quoted = False
    double_quoted = False
    escaped = False
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == '\\' and not single_quoted:
            escaped = True
        elif char == "'" and not double_quoted:
            single_quoted = not single_quoted
        elif char == '"' and not single_quoted:
            double_quoted = not double_quoted
        elif (not single_quoted
              and command.startswith(('$(', '<(', '>('), index)
              and (not double_quoted or command.startswith('$(', index))
              and not command.startswith('$((', index)):
            depth = 1
            cursor = index + 2
            inner_quote = None
            inner_escaped = False
            while cursor < len(command) and depth:
                inner = command[cursor]
                if inner_escaped:
                    inner_escaped = False
                elif inner == '\\' and inner_quote != "'":
                    inner_escaped = True
                elif inner in "'\"" and (inner_quote is None
                                           or inner_quote == inner):
                    inner_quote = None if inner_quote == inner else inner
                elif inner_quote is None:
                    if inner == '(':
                        depth += 1
                    elif inner == ')':
                        depth -= 1
                cursor += 1
            if depth == 0:
                bodies.append(command[index + 2:cursor - 1])
                index = cursor - 1
        index += 1
    return bodies


def _heredoc_body(
        command: str, start: int) -> Optional[Tuple[int, int, int, bool]]:
    """Return body and delimiter bounds for one simple heredoc."""
    index = start + 2
    strip_tabs = index < len(command) and command[index] == '-'
    if strip_tabs:
        index += 1
    while index < len(command) and command[index] in ' \t':
        index += 1
    delimiter_parts = []
    quoted = False
    while index < len(command) and command[index] not in ' \t\r\n;&|<>()':
        character = command[index]
        if character in "'\"":
            quoted = True
            end = command.find(character, index + 1)
            if end == -1:
                return None
            delimiter_parts.append(command[index + 1:end])
            index = end + 1
        elif character == '\\':
            if index + 1 >= len(command):
                return None
            quoted = True
            delimiter_parts.append(command[index + 1])
            index += 2
        else:
            delimiter_parts.append(character)
            index += 1
    delimiter = ''.join(delimiter_parts)
    if not delimiter:
        return None
    body_start = command.find('\n', index)
    if body_start == -1:
        return None
    body_start += 1
    line_start = body_start
    while line_start <= len(command):
        line_end = command.find('\n', line_start)
        if line_end == -1:
            line_end = len(command)
        line = command[line_start:line_end].removesuffix('\r')
        if strip_tabs:
            line = line.lstrip('\t')
        if line == delimiter:
            body_end = min(line_end + 1, len(command))
            return body_start, line_start, body_end, quoted
        if line_end == len(command):
            return None
        line_start = line_end + 1
    return None


def _without_heredoc_bodies(command: str) -> Tuple[str, List[str]]:
    """Blank heredoc bodies and return bodies whose expansions execute."""
    executable_bodies = []
    pieces = []
    copied_to = 0
    index = 0
    quote = None
    while index < len(command):
        character = command[index]
        if character == '\\' and quote != "'":
            index += 2
            continue
        if character in "'\"":
            quote = None if quote == character else character \
                if quote is None else quote
            index += 1
            continue
        if (quote is None and command.startswith('<<', index)
                and not command.startswith('<<<', index)):
            heredoc = _heredoc_body(command, index)
            if heredoc is not None:
                body_start, delimiter_start, body_end, quoted = heredoc
                pieces.append(command[copied_to:body_start])
                blanked = ''.join(
                    '\n' if char == '\n' else ' '
                    for char in command[body_start:body_end]
                )
                pieces.append(blanked)
                if not quoted:
                    executable_bodies.append(
                        command[body_start:delimiter_start])
                copied_to = body_end
                index = body_end
                continue
        index += 1
    pieces.append(command[copied_to:])
    return ''.join(pieces), executable_bodies


def _split_env_command(segment: List[str], index: int) -> Optional[List[str]]:
    """Return the effective command assembled by env -S, when present."""
    cursor = index + 1
    while cursor < len(segment) and segment[cursor].startswith('-'):
        option = segment[cursor]
        if option in ('-S', '--split-string'):
            if cursor + 1 >= len(segment):
                return []
            try:
                words = shlex.split(segment[cursor + 1])
            except ValueError:
                words = [segment[cursor + 1]]
            return [segment[index]] + words + segment[cursor + 2:]
        if option.startswith('--split-string='):
            try:
                words = shlex.split(option.split('=', 1)[1])
            except ValueError:
                words = [option.split('=', 1)[1]]
            return [segment[index]] + words + segment[cursor + 1:]
        if option.startswith('-') and not option.startswith('--'):
            option_chars = option[1:]
            split_index = option_chars.find('S')
            value_positions = [
                option_chars.find(char) for char in _ENV_SHORT_VALUE_OPTS]
            if any(0 <= position < split_index for position in value_positions):
                split_index = -1
            if split_index >= 0:
                value = option_chars[split_index + 1:]
                remainder = segment[cursor + 1:]
                if not value:
                    if not remainder:
                        return []
                    value = remainder[0]
                    remainder = remainder[1:]
                try:
                    words = shlex.split(value)
                except ValueError:
                    words = [value]
                return [segment[index]] + words + remainder
        if (option in _ENV_VALUE_OPTS or not option.startswith('--')
                and option[-1:] in _ENV_SHORT_VALUE_OPTS):
            cursor += 1
        cursor += 1
    return None


def _command_position(
        segment: List[str], stop_at_env: bool = False) -> Optional[int]:
    """Resolve the command word after assignments and supported wrappers."""
    cursor = 0
    while cursor < len(segment):
        while cursor < len(segment) and _ASSIGNMENT.match(segment[cursor]):
            cursor += 1
        if cursor >= len(segment):
            return None
        command_word = os.path.basename(segment[cursor]).lower()
        if command_word in ('command', 'exec', 'nohup'):
            cursor += 1
            while cursor < len(segment) and segment[cursor].startswith('-'):
                option = segment[cursor]
                cursor += 1
                if command_word == 'exec' and option == '-a':
                    cursor += 1
            continue
        if command_word == 'sudo':
            cursor += 1
            while cursor < len(segment) and segment[cursor].startswith('-'):
                option = segment[cursor]
                cursor += 1
                value_index = next((index for index, char
                                    in enumerate(option[1:])
                                    if char in 'CDghpRrtTUu'), None)
                if (option in (
                        '--chdir', '--group', '--host',
                        '--other-user', '--prompt', '--role', '--type',
                        '--user') or not option.startswith('--')
                        and value_index == len(option) - 2):
                    cursor += 1
            continue
        if command_word == 'time':
            cursor += 1
            while cursor < len(segment) and segment[cursor].startswith('-'):
                option = segment[cursor]
                cursor += 1
                if option in ('-f', '-o', '--format', '--output'):
                    cursor += 1
            continue
        if command_word == 'nice':
            cursor += 1
            while cursor < len(segment) and segment[cursor].startswith('-'):
                option = segment[cursor]
                cursor += 1
                if option in ('-n', '--adjustment'):
                    cursor += 1
            continue
        if command_word == 'timeout':
            cursor += 1
            while cursor < len(segment) and segment[cursor].startswith('-'):
                option = segment[cursor]
                cursor += 1
                if option in ('-k', '-s', '--kill-after', '--signal'):
                    cursor += 1
            cursor += 1
            continue
        if command_word == 'xargs':
            cursor += 1
            while cursor < len(segment) and segment[cursor].startswith('-'):
                option = segment[cursor]
                cursor += 1
                if option in ('-a', '-d', '-E', '-I', '-L', '-n', '-P',
                              '-s', '--arg-file', '--delimiter', '--eof',
                              '--replace', '--max-lines', '--max-args',
                              '--max-procs', '--max-chars'):
                    cursor += 1
            continue
        if command_word == 'env':
            if stop_at_env:
                return cursor
            cursor += 1
            while cursor < len(segment):
                token = segment[cursor]
                if _ASSIGNMENT.match(token):
                    cursor += 1
                elif token.startswith('-'):
                    cursor += 1
                    if (token in _ENV_VALUE_OPTS
                            or not token.startswith('--')
                            and token[-1:] in _ENV_SHORT_VALUE_OPTS):
                        cursor += 1
                else:
                    break
            continue
        return cursor
    return None


def _command_word(token: str) -> str:
    """Return a normalized command word, preserving the dot builtin."""
    return '.' if token == '.' else os.path.basename(token).lower()


def _normalize_search_option(command_word: str, name: str) -> str:
    """Expand the unambiguous GNU grep abbreviations relevant to file access."""
    if command_word == 'grep':
        for option, shortest in (
                ('--include', '--inc'), ('--exclude-from', '--exclude-f'),
                ('--regexp', '--reg')):
            if name.startswith(shortest) and option.startswith(name):
                return option
    return name


def _search_glob_names_dotenv(command_word: str, pattern: str) -> bool:
    """Apply ripgrep-only exclusion semantics before matching a search glob."""
    positive = not (command_word == 'rg' and pattern.startswith('!'))
    parts = pattern.split('/') if command_word == 'rg' else [pattern]
    return positive and any(_glob_names_dotenv(part) for part in parts)


def _search_accesses_dotenv(command_word: str, args: List[str]) -> bool:
    """Check grep-like file operands without mistaking the pattern for a file."""
    file_options = _SEARCH_FILE_OPTS[command_word]
    glob_options = _SEARCH_GLOB_OPTS.get(command_word, set())
    short_value_options = _SEARCH_SHORT_VALUE_OPTS.get(command_word, set())
    long_value_options = _SEARCH_LONG_VALUE_OPTS.get(command_word, set())
    pattern_supplied = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == '--':
            index += 1
            while index < len(args):
                if not pattern_supplied:
                    pattern_supplied = True
                elif _names_dotenv(args[index]):
                    return True
                index += 1
            break
        if arg.startswith('--'):
            name, separator, value = arg.partition('=')
            name = _normalize_search_option(command_word, name)
            if name in file_options:
                if not separator:
                    index += 1
                    if index >= len(args):
                        return False
                    value = args[index]
                names_dotenv = (
                    _search_glob_names_dotenv(command_word, value)
                    if name in glob_options else _names_dotenv(value)
                )
                if names_dotenv:
                    return True
                if name in {'--file'}:
                    pattern_supplied = True
            elif name in _SEARCH_PATTERN_OPTS:
                if not separator:
                    index += 1
                pattern_supplied = True
            elif name in long_value_options and not separator:
                index += 1
            index += 1
            continue
        if arg.startswith('-') and arg != '-':
            cluster = arg[1:]
            for position, option in enumerate(cluster):
                if option not in {'e', 'f', 'g'} | short_value_options:
                    continue
                value = cluster[position + 1:]
                if not value:
                    index += 1
                    if index >= len(args):
                        return False
                    value = args[index]
                if option in file_options:
                    names_dotenv = (
                        _search_glob_names_dotenv(command_word, value)
                        if option in glob_options else _names_dotenv(value)
                    )
                    if names_dotenv:
                        return True
                if option in {'e', 'f'}:
                    pattern_supplied = True
                break
            index += 1
            continue
        if not pattern_supplied:
            pattern_supplied = True
        elif _names_dotenv(arg):
            return True
        index += 1
    return False


def _script_reader_accesses_dotenv(
    command_word: str,
    args: List[str],
) -> bool:
    """Distinguish sed/awk programs from the files they operate on."""
    script_supplied = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == '--':
            index += 1
            continue
        if arg.startswith('--'):
            name, separator, value = arg.partition('=')
            is_script_file = name == '--file'
            is_script_text = name in {'--expression', '--source'}
            takes_other_value = command_word == 'awk' and name == '--assign'
            if is_script_file or is_script_text or takes_other_value:
                if not separator:
                    index += 1
                    if index >= len(args):
                        return False
                    value = args[index]
                if is_script_file and _names_dotenv(value):
                    return True
                if is_script_file or is_script_text:
                    script_supplied = True
            index += 1
            continue
        if arg.startswith('-') and arg != '-':
            cluster = arg[1:]
            if command_word == 'sed' and cluster.startswith('i'):
                index += 1
                continue
            value_options = {'e', 'f'}
            if command_word == 'awk':
                value_options.update({'F', 'v'})
            for position, option in enumerate(cluster):
                if option not in value_options:
                    continue
                value = cluster[position + 1:]
                if not value:
                    index += 1
                    if index >= len(args):
                        return False
                    value = args[index]
                if option == 'f' and _names_dotenv(value):
                    return True
                if option in {'e', 'f'}:
                    script_supplied = True
                break
            index += 1
            continue
        if command_word == 'awk' and _ASSIGNMENT.match(arg):
            index += 1
            continue
        if not script_supplied:
            script_supplied = True
        elif _names_dotenv(arg):
            return True
        index += 1
    return False


def _reader_accesses_dotenv(command_word: str, args: List[str]) -> bool:
    """Check whether one protected reader's own arguments name a dotenv."""
    if command_word == 'find':
        for root in args:
            if root in {'-H', '-L', '-P', '-D', '--'} or root.startswith('-O'):
                continue
            if root.startswith('-') or root in {'!', '('}:
                break
            if _names_dotenv(root):
                return True
        path_predicates = {
            '-name', '-iname', '-path', '-ipath', '-wholename', '-iwholename',
        }
        # A disjunction (-o/-or/,) can re-select what a negated predicate
        # excluded (find . ! -name '.env' -o -exec cat {} \;), so negation
        # is only honoured when the expression has no disjunction.
        has_disjunction = any(arg in {'-o', '-or', ','} for arg in args)
        # An action left of the negation runs before the exclusion filters
        # (find . -exec cat {} \; ! -name '.env' cats every file, dotenv
        # included), so negation is only honoured with no earlier action.
        action_tokens = {
            '-exec', '-execdir', '-ok', '-okdir', '-delete',
            '-fprintf', '-fprint', '-fls',
        }
        # These output actions write to their FILE operand, so naming a
        # dotenv there truncates it: find . -fprintf .env '%p'
        for position, arg in enumerate(args[:-1]):
            if arg in {'-fprintf', '-fprint', '-fls'} and _names_dotenv(
                    args[position + 1]):
                return True
        for position, arg in enumerate(args[:-1]):
            pattern = args[position + 1]
            # A predicate under an odd number of negations (-not/! -path X)
            # excludes matches rather than selecting them, so it cannot be
            # used to read a dotenv. Even counts (! ! -name X) still select.
            negations = 0
            scan = position - 1
            while scan >= 0 and args[scan] in {'-not', '!'}:
                negations += 1
                scan -= 1
            # Fail closed on ambiguous negation: a '-...' token right
            # before the chain (other than a conjunction) may be a value
            # predicate whose operand is the '!' itself, as in
            # find . -printf '!' -name '.env' -exec cat {} \;
            unambiguous = (
                scan < 0
                or not args[scan].startswith('-')
                or args[scan] in {'-a', '-and', '(', ')'}
            ) and not (
                # -fprintf FILE FORMAT: with FILE before the chain, the
                # chain's first '!' is the two-operand FORMAT, as in
                # find . -fprintf /tmp/list '!' -name '.env' -delete
                scan >= 1 and args[scan - 1] == '-fprintf'
            )
            no_prior_action = not any(
                early in action_tokens for early in args[:position])
            if (negations % 2 and unambiguous and not has_disjunction
                    and no_prior_action):
                continue
            if arg in path_predicates and (
                    _names_dotenv(pattern)
                    or _glob_names_dotenv(pattern)):
                return True
            if arg in {'-regex', '-iregex'}:
                literal_dot_pattern = pattern.replace('[.]', '.')
                literal_dot_pattern = literal_dot_pattern.replace(r'\.', '.')
                literal_dot_pattern = re.sub(r'^\^?\.\*', '', literal_dot_pattern)
                if _names_dotenv(literal_dot_pattern):
                    return True
        return False
    if command_word in _SEARCH_FILE_OPTS:
        return _search_accesses_dotenv(command_word, args)
    if command_word in {'sed', 'awk'}:
        return _script_reader_accesses_dotenv(command_word, args)
    if any(_names_dotenv(arg) for arg in args):
        return True
    return bool(command_word in ('cat', 'less') and args
                and args[-1].lower() in _BARE_ENV)


def _find_exec_accesses_dotenv(args: List[str], depth: int) -> bool:
    """Check commands supplied to find's -exec and related actions."""
    for index, arg in enumerate(args):
        if arg in ('-exec', '-execdir', '-ok', '-okdir'):
            end = index + 1
            while end < len(args) and args[end] not in (';', '+'):
                end += 1
            if _segment_accesses_dotenv(args[index + 1:end], depth):
                return True
    return False


def _nesting_fallback(command: str) -> bool:
    """Iteratively inspect nested command text after recursive parsing is capped."""
    pending = [command]
    for _ in range(256):
        if not pending:
            return False
        current, heredoc_bodies = _without_heredoc_bodies(pending.pop())
        pending.extend(_backtick_commands(current))
        pending.extend(_substitution_commands(current))
        for body in heredoc_bodies:
            pending.extend(_backtick_commands(body))
            pending.extend(_substitution_commands(body))
        segments = shell_segments(current)
        if segments is None:
            normalized = ' '.join(current.strip().split())
            if any(re.search(pattern, normalized, re.IGNORECASE)
                   for pattern in LEGACY_ENV_PATTERNS):
                return True
            continue
        for segment in segments:
            for index, token in enumerate(segment[:-1]):
                if token in _REDIRECTS:
                    destination = segment[index + 1]
                    if (_names_dotenv(destination)
                            or destination.lower() in _BARE_ENV):
                        return True
            env_index = _command_position(segment, stop_at_env=True)
            if (env_index is not None
                    and _command_word(segment[env_index]) == 'env'):
                effective = _split_env_command(segment, env_index)
                if effective is not None:
                    pending.append(shlex.join(effective))
            index = _command_position(segment)
            if index is not None:
                command_word = _command_word(segment[index])
                args = segment[index + 1:]
                wrapped_by_xargs = any(
                    _command_word(token) == 'xargs'
                    for token in segment[:index]
                )
                if command_word in READER_COMMANDS:
                    if wrapped_by_xargs:
                        return True
                    if _reader_accesses_dotenv(command_word, args):
                        return True
                if command_word == 'find' and _find_exec_accesses_dotenv(
                        args, _MAX_NESTING):
                    return True
                if command_word in _SHELLS:
                    for position, arg in enumerate(args):
                        if (arg.startswith('-') and not arg.startswith('--')
                                and 'c' in arg[1:]
                                and position + 1 < len(args)):
                            pending.append(args[position + 1])
                if command_word == 'eval' and args:
                    pending.append(' '.join(args))
    normalized = ' '.join(' '.join(pending).strip().split())
    return any(re.search(pattern, normalized, re.IGNORECASE)
               for pattern in LEGACY_ENV_PATTERNS)


def _segment_accesses_dotenv(segment: List[str], depth: int = 0) -> bool:
    """Check one segment by scanning for executable protected-reader tokens."""
    for index, token in enumerate(segment[:-1]):
        if token in _REDIRECTS:
            destination = segment[index + 1]
            if (_names_dotenv(destination)
                    or destination.lower() in _BARE_ENV):
                return True
    env_index = _command_position(segment, stop_at_env=True)
    if (env_index is not None
            and _command_word(segment[env_index]) == 'env'):
        effective = _split_env_command(segment, env_index)
        if effective is not None and _segment_accesses_dotenv(
                effective, depth + 1):
            return True
    index = _command_position(segment)
    if index is not None:
        command_word = _command_word(segment[index])
        args = segment[index + 1:]
        wrapped_by_xargs = any(
            _command_word(token) == 'xargs'
            for token in segment[:index]
        )
        if command_word in _SHELLS:
            for position, arg in enumerate(args):
                if (arg.startswith('-') and not arg.startswith('--')
                        and 'c' in arg[1:]):
                    command_index = position + 1
                    if (command_index < len(args)
                            and check_env_file_access(
                                args[command_index], depth + 1)[0]):
                        return True
        if command_word == 'eval' and args:
            args = args[1:] if args[0] == '--' else args
            if check_env_file_access(' '.join(args), depth + 1)[0]:
                return True
        if command_word == 'find' and _find_exec_accesses_dotenv(args, depth):
            return True
        if command_word in READER_COMMANDS:
            if wrapped_by_xargs:
                return True
            if _reader_accesses_dotenv(command_word, args):
                return True
    return False


def check_env_file_access(
        command: str, depth: int = 0) -> Tuple[bool, Optional[str]]:
    """
    Check if a command attempts to read, write, or edit .env files.
    Returns tuple: (should_block: bool, reason: str or None)

    Each shell segment's command word is resolved, and the question asked is
    whether one of ITS OWN arguments names a dotenv file -- rather than whether
    a command name and an env-looking string both appear somewhere in the same
    line, which does not require the two to be related.
    """
    if depth >= _MAX_NESTING:
        if _nesting_fallback(command):
            return True, BLOCK_REASON
        return False, None
    command, heredoc_bodies = _without_heredoc_bodies(command)
    nested_commands = _backtick_commands(command)
    nested_commands.extend(_substitution_commands(command))
    for body in heredoc_bodies:
        nested_commands.extend(_backtick_commands(body))
        nested_commands.extend(_substitution_commands(body))
    for nested_command in nested_commands:
        if check_env_file_access(nested_command, depth + 1)[0]:
            return True, BLOCK_REASON
    segments = shell_segments(command)
    if segments is None:
        normalized_cmd = ' '.join(command.strip().split())
        for pattern in LEGACY_ENV_PATTERNS:
            if re.search(pattern, normalized_cmd, re.IGNORECASE):
                return True, BLOCK_REASON
        return False, None

    for segment in segments:
        if _segment_accesses_dotenv(segment, depth):
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
