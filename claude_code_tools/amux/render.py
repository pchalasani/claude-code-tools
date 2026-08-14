"""Rendering: aligned rows for the picker, and a table for humans."""

from __future__ import annotations

import json

from .model import STATE_COLOR, Agent

#: Column widths for the picker rows. Pane comes first because fzf's preview
#: binding uses ``{1}`` to address it.
_W_PANE = 20
_W_STATE = 5
_W_KIND = 6
_W_NAME = 26
_W_REPO = 22


def _clip(text: str, width: int) -> str:
    """Truncate *text* to *width*, marking loss with an ellipsis."""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def _colour(state: str, text: str, colour: bool) -> str:
    """Wrap *text* in this state's ANSI colour when *colour* is enabled."""
    if not colour:
        return text
    return f"\033[{STATE_COLOR.get(state, '0')}m{text}\033[0m"


def picker_row(agent: Agent, colour: bool = True) -> str:
    """The visible part of an fzf row (no key field); see :func:`picker_lines`."""
    state = _colour(agent.state, f"{agent.state:<{_W_STATE}}", colour)
    branch = f"@{agent.branch}" if agent.branch else ""
    where = _clip(f"{agent.repo}{branch}", _W_REPO)
    return (
        f"{_clip(agent.pane, _W_PANE):<{_W_PANE}} {state} {agent.kind:<{_W_KIND}} "
        f"{_clip(agent.name or '-', _W_NAME):<{_W_NAME}} "
        f"{where:<{_W_REPO}} {agent.info}"
    )


def picker_lines(agents: list[Agent], colour: bool = True) -> str:
    """All fzf rows: a tab-separated key field followed by the visible text.

    The leading ``pane<TAB>`` field exists because tmux permits spaces in
    session names, so ``amux test:1.1`` would make fzf's whitespace-split
    ``{1}`` resolve to ``amux`` -- pointing the preview and the jump at a pane
    that does not exist. With ``--delimiter '\\t' --with-nth 2..`` the key stays
    intact and invisible.
    """
    return "\n".join(f"{a.pane}\t{picker_row(a, colour)}" for a in agents)


def pane_from_selection(line: str) -> str:
    """Recover the pane target from a selected fzf line."""
    return line.split("\t", 1)[0].strip()


def table(agents: list[Agent], colour: bool = True) -> str:
    """A bordered summary table, grouped by state with counts in the header."""
    if not agents:
        return "no agents found"

    counts: dict[str, int] = {}
    for agent in agents:
        counts[agent.state] = counts.get(agent.state, 0) + 1
    summary = "  ".join(
        _colour(state, f"{state}={counts[state]}", colour)
        for state in ("input", "busy", "bg", "idle")
        if state in counts
    )

    header = (
        f"{'PANE':<{_W_PANE}} {'STATE':<{_W_STATE}} {'KIND':<{_W_KIND}} "
        f"{'NAME':<{_W_NAME}} {'REPO@BRANCH':<{_W_REPO}} CONTEXT"
    )
    rule = "─" * min(len(header) + 20, 120)
    rows = [picker_row(a, colour) for a in agents]
    return "\n".join([f"{len(agents)} agents   {summary}", rule, header, rule, *rows])


def as_json(agents: list[Agent]) -> str:
    """Machine-readable output for scripts and other tools."""
    return json.dumps([a.to_dict() for a in agents], indent=2)
