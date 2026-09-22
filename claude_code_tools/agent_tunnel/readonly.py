"""The read-only pin for forks the HTTP front-end creates.

A chat front-end answers people the operator invited to a channel; the HTTP
front-end answers a program, and that program names the handle. So its forks
keep the read tool preset whatever the handle was shared with and whatever
the operator configured. These helpers are what the backends consult to
apply that pin.
"""

from __future__ import annotations

from typing import Sequence


# Thread keys of the HTTP front-end. Its callers are programs that pick any
# published handle, so their forks stay read-only whatever the handle grants.
HTTP_THREAD_PREFIX = "http:"


def is_http_thread(thread_key: str) -> bool:
    """Whether this thread came from the HTTP front-end."""
    return thread_key.startswith(HTTP_THREAD_PREFIX)


# Flags that would undo a read-only pin if they arrived through
# [claude] headless_extra_args / tmux_extra_args, which are appended after
# the flags build_claude_flags produced. Split by arity: the first group
# stands alone, the second takes one or more values.
VALUELESS_PERMISSION_ARGS = (
    "--dangerously-skip-permissions",
    "--strict-mcp-config",
)
VALUED_PERMISSION_ARGS = (
    "--allowedTools",
    "--allowed-tools",
    "--disallowedTools",
    "--disallowed-tools",
    "--permission-mode",
    "--permission-prompt-tool",
    "--add-dir",
    # Settings and MCP servers can grant tools the deny list never names.
    "--settings",
    "--setting-sources",
    "--mcp-config",
)
PERMISSION_ARGS = VALUELESS_PERMISSION_ARGS + VALUED_PERMISSION_ARGS


def strip_permission_args(args: Sequence[str]) -> list[str]:
    """``args`` without any flag that could widen a read-only fork.

    A flag's values go with it, however many it takes, so
    ``["--allowedTools", "Bash", "Write"]`` leaves nothing behind for claude
    to read as a positional argument. Values are the tokens up to the next
    one beginning with ``-``.

    Args:
        args: Extra CLI arguments from the config.

    Returns:
        The arguments that are safe to append to a pinned fork's flags.
    """
    kept: list[str] = []
    dropping = False
    for arg in args:
        name = arg.split("=", 1)[0]
        if name in VALUELESS_PERMISSION_ARGS:
            dropping = False
            continue
        if name in VALUED_PERMISSION_ARGS:
            dropping = "=" not in arg
            continue
        if dropping:
            if not arg.startswith("-"):
                continue  # one of the dropped flag's values
            dropping = False
        kept.append(arg)
    return kept
