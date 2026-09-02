"""Shared typed shapes and identities for visual brief documents."""

from __future__ import annotations

from typing import NotRequired, TypedDict

# Authored briefing ids cannot contain '/', so no ordinary path can use this root.
CURRENT_STATE_ROOT = "//current-state"
LEGACY_ANCHOR_ALIASES_FIELD = "legacy_anchor_aliases"
MAX_SUGGESTIONS = 3
MAX_SUGGESTION_LABEL_LENGTH = 40
MAX_SUGGESTION_MESSAGE_LENGTH = 20_000


def current_state_lane_path(lane_id: str) -> str:
    """Return the stable anchor for one current-state lane."""
    return f"{CURRENT_STATE_ROOT}/lanes/{lane_id}"


def current_state_item_path(item_id: str) -> str:
    """Return the lane-independent anchor for one current-state item."""
    return f"{CURRENT_STATE_ROOT}/items/{item_id}"


def legacy_anchor_aliases(document: object) -> dict[str, str]:
    """Return the stored aliases for anchors retired during migration."""
    if not isinstance(document, dict):
        return {}
    aliases = document.get(LEGACY_ANCHOR_ALIASES_FIELD)
    if not isinstance(aliases, dict):
        return {}
    return {
        source: target
        for source, target in aliases.items()
        if isinstance(source, str) and isinstance(target, str)
    }


class Turn(TypedDict):
    """One chronological conversation turn."""

    author: str
    text: str
    at: str


class Anchor(TypedDict):
    """One element anchor."""

    kind: str
    path: str


class Thread(TypedDict):
    """One tool-owned conversation."""

    id: str
    anchor: Anchor
    turns: list[Turn]


class NestedNote(TypedDict):
    """One recursively nestable forensic note."""

    id: NotRequired[str]
    title: str
    body: str
    children: NotRequired[list[NestedNote]]


class Table(TypedDict):
    """One comparison table."""

    caption: str
    columns: list[str]
    rows: list[list[str]]


class SuggestedReply(TypedDict):
    """One optional shorthand for a useful human response."""

    label: str
    message: str


class Item(TypedDict):
    """Visible item content in a briefing or legacy current state."""

    id: str
    glance: str
    explanation: str
    trust: str
    suggestions: NotRequired[list[SuggestedReply]]
    forensics: NotRequired[list[str | NestedNote]]
    tables: NotRequired[list[Table]]
    questions: NotRequired[list[Thread]]


class Lane(TypedDict):
    """Visible lane content in a briefing or legacy current state."""

    id: str
    name: str
    open: NotRequired[bool]
    items: list[Item]
    questions: NotRequired[list[Thread]]


class Update(TypedDict):
    """One stored briefing in the append-only ledger."""

    id: str
    timestamp: str
    headline: str
    summary: str
    lanes: list[Lane]
    questions: NotRequired[list[Thread]]


class StructuredCurrentState(TypedDict):
    """Legacy structured state kept for read compatibility."""

    updated_at: str
    headline: str
    summary: str
    lanes: list[Lane]
    questions: NotRequired[list[Thread]]


class LegacyCurrentState(TypedDict):
    """Already-shipped four-claim state kept for read compatibility."""

    updated_at: str
    goal: str
    focus: str
    blocker: str | None
    next: str


CurrentState = StructuredCurrentState | LegacyCurrentState


class BriefDocument(TypedDict):
    """The top-level stored and delivered document shape."""

    title: str
    summary: str
    current_state: NotRequired[CurrentState]
    legacy_anchor_aliases: NotRequired[dict[str, str]]
    updates: list[Update]
