"""Data model for amux: one record per tmux pane running a coding agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Kind = Literal["claude", "codex"]
State = Literal["input", "busy", "bg", "idle"]

#: Sort order for the picker: the agent waiting on YOU comes first.
STATE_RANK: dict[str, int] = {"input": 0, "busy": 1, "bg": 2, "idle": 3}

#: ANSI colour per state, applied only when rendering to a terminal.
STATE_COLOR: dict[str, str] = {
    "input": "1;31",  # bold red   - stopped, asking you something
    "busy": "1;32",  # bold green - mid-turn
    "bg": "1;36",  # bold cyan  - monitors/subagents running
    "idle": "2;37",  # dim        - at prompt, nothing pending
}


@dataclass
class Agent:
    """A coding agent running inside a tmux pane.

    Attributes:
        pane: tmux target, e.g. ``sasy:1.4``.
        session: tmux session name.
        kind: Which harness is running (``claude`` or ``codex``).
        state: What it is doing right now; see :data:`STATE_RANK`.
        name: Agent session name (from argv ``--resume``/``--name``, the pane
            title, or an on-screen separator), or ``""`` if undiscoverable.
        cwd: Pane working directory.
        repo: Basename of the enclosing git worktree, or ``""``.
        branch: Current git branch, or ``""``.
        model: Model string parsed from the harness footer, e.g. ``opus-5``.
        info: One-line context lifted from the pane's footer.
        pid: PID of the agent process itself (not the pane's shell).
    """

    pane: str
    session: str
    kind: Kind
    state: State = "idle"
    name: str = ""
    cwd: str = ""
    repo: str = ""
    branch: str = ""
    model: str = ""
    info: str = ""
    pid: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def rank(self) -> int:
        """Urgency rank used for sorting (lower sorts first)."""
        return STATE_RANK.get(self.state, 9)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict (used by the cache)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Agent | None:
        """Rebuild an :class:`Agent` from :meth:`to_dict` output.

        Returns ``None`` for a record that is the right shape but the wrong
        types -- a hand-edited or truncated cache with ``"pane": null`` would
        otherwise reach the renderer and crash on ``len(None)``.
        """
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        for field_name in ("pane", "session", "kind", "state"):
            value = known.get(field_name)
            if value is not None and not isinstance(value, str):
                return None
        for field_name in ("name", "cwd", "repo", "branch", "model", "info"):
            if not isinstance(known.get(field_name, ""), str):
                return None
        if known.get("pane") is None or known.get("session") is None:
            return None
        if not isinstance(known.get("pid", 0), int):
            return None
        # Literal[...] is a type-checker hint only; a hand-edited cache can
        # still carry kind="vim" or state="waiting" and render as an agent.
        if known.get("kind") not in ("claude", "codex"):
            return None
        if known.get("state", "idle") not in STATE_RANK:
            return None
        return cls(**known)
