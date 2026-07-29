"""Read a verb's payload from an option, a file, or standard input.

Long answers and whole updates do not belong in shell arguments, so every
verb that takes content also takes ``--file F`` or a bare ``-``. Reading them
is wiring, and it lives here so the command modules stay about the write.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from visual_brief.writes.runfiles import CliError


def read_text_payload(
    text: str | None,
    file: str | None,
    use_stdin: bool,
) -> str:
    """Read one text payload from exactly one source.

    Args:
        text: Value of ``--text``, if given.
        file: Value of ``--file``, if given. ``-`` means standard input.
        use_stdin: Whether a bare ``-`` was given.

    Returns:
        The payload text.

    Raises:
        CliError: If no source or more than one source was given, or the
            source cannot be read.
    """
    if text is not None and (file is not None or use_stdin):
        raise CliError("give exactly one of --text, --file F, or -")
    if text is not None:
        return text
    if file is None and not use_stdin:
        raise CliError("give one of --text TEXT, --file F, or - for stdin")
    return _read_source(file, use_stdin, "text")


def read_json_payload(file: str | None, use_stdin: bool) -> Any:
    """Read one JSON payload from a file or standard input.

    Args:
        file: Value of ``--file``, if given. ``-`` means standard input.
        use_stdin: Whether a bare ``-`` was given.

    Returns:
        The parsed JSON value.

    Raises:
        CliError: If no source or more than one source was given, the source
            cannot be read, or it does not hold JSON.
    """
    if file is None and not use_stdin:
        raise CliError("give --file F, or - to read JSON from stdin")
    raw = _read_source(file, use_stdin, "JSON")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise CliError(
            f"malformed JSON at line {error.lineno}, column {error.colno}: "
            f"{error.msg}"
        ) from error


def _read_source(file: str | None, use_stdin: bool, what: str) -> str:
    """Read the one named source, or standard input."""
    if file is not None and file != "-" and use_stdin:
        raise CliError("give either --file F or -, not both")
    if file is None or file == "-":
        try:
            return sys.stdin.read()
        except (OSError, UnicodeDecodeError) as error:
            raise CliError(
                f"cannot read {what} from standard input: {error}"
            ) from error
    path = Path(file).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CliError(f"cannot read {path}: {error}") from error
