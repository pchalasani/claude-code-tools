"""Safe positional rewriting for legacy argparse command delegates."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


class MissingPositionalError(ValueError):
    """Raised when passthrough arguments omit the required positional."""


def positional_index(
    arguments: Sequence[str],
    *,
    value_options: Iterable[str] = (),
) -> int:
    """Find the first positional while respecting options with values.

    Args:
        arguments: Raw passthrough argument tokens.
        value_options: Long or short options that consume the next token.

    Returns:
        Index of the first positional token.

    Raises:
        MissingPositionalError: If no positional token is present.
    """
    valued = set(value_options)
    index = 0
    after_separator = False
    while index < len(arguments):
        argument = arguments[index]
        if after_separator:
            return index
        if argument == "--":
            after_separator = True
            index += 1
            continue
        if argument in valued:
            index += 2
            continue
        if any(
            argument.startswith(f"{option}=")
            for option in valued
            if option.startswith("--")
        ):
            index += 1
            continue
        if any(
            argument.startswith(option) and argument != option
            for option in valued
            if option.startswith("-") and not option.startswith("--")
        ):
            index += 1
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return index
    raise MissingPositionalError("A session argument is required.")


def replace_positional(
    arguments: Sequence[str],
    replacement: str,
    *,
    value_options: Iterable[str] = (),
) -> list[str]:
    """Replace the first positional token without reordering options.

    Args:
        arguments: Raw passthrough argument tokens.
        replacement: Resolved session path to substitute.
        value_options: Long or short options that consume the next token.

    Returns:
        A copied argument list containing the replacement.
    """
    rewritten = list(arguments)
    rewritten[positional_index(rewritten, value_options=value_options)] = replacement
    return rewritten


def option_value(
    arguments: Sequence[str],
    *names: str,
) -> str | None:
    """Return the last value supplied for any named option.

    Args:
        arguments: Raw argument tokens.
        names: Equivalent option spellings to inspect.

    Returns:
        The last explicit value, or None when absent.
    """
    found: str | None = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in names and index + 1 < len(arguments):
            found = arguments[index + 1]
            index += 2
            continue
        for name in names:
            prefix = f"{name}="
            if name.startswith("--") and argument.startswith(prefix):
                found = argument[len(prefix) :]
        index += 1
    return found


def remove_options(
    arguments: Sequence[str],
    *names: str,
) -> list[str]:
    """Remove named value options while preserving every other token.

    Args:
        arguments: Raw argument tokens.
        names: Equivalent option spellings to remove.

    Returns:
        Filtered argument tokens.
    """
    remaining: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in names:
            index += 2
            continue
        if any(
            name.startswith("--") and argument.startswith(f"{name}=") for name in names
        ):
            index += 1
            continue
        remaining.append(argument)
        index += 1
    return remaining
