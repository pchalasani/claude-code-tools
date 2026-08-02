"""Shared typed shapes and identities for visual brief documents."""

from __future__ import annotations

from typing import NotRequired, TypedDict

# Authored update ids cannot contain '/', so no dated path can use this root.
CURRENT_STATE_ROOT = "//current-state"


def current_state_lane_path(lane_id: str) -> str:
    """Return the stable anchor for one current-state lane."""
    return f"{CURRENT_STATE_ROOT}/lanes/{lane_id}"


def current_state_item_path(item_id: str) -> str:
    """Return the lane-independent anchor for one current-state item."""
    return f"{CURRENT_STATE_ROOT}/items/{item_id}"


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


class Item(TypedDict):
    """Visible item content shared by state and dated updates."""

    id: str
    glance: str
    explanation: str
    trust: str
    forensics: NotRequired[list[str | NestedNote]]
    tables: NotRequired[list[Table]]
    questions: NotRequired[list[Thread]]


class Lane(TypedDict):
    """Visible lane content shared by state and dated updates."""

    id: str
    name: str
    open: NotRequired[bool]
    items: list[Item]
    questions: NotRequired[list[Thread]]


class PublishState(TypedDict):
    """Replaceable structured state supplied by a normal publish."""

    headline: str
    summary: str
    lanes: list[Lane]


class StructuredCurrentState(PublishState):
    """Stored structured state, including tool-owned root conversations."""

    updated_at: str
    questions: NotRequired[list[Thread]]


class LegacyCurrentState(TypedDict):
    """Already-shipped four-claim state kept for read compatibility."""

    updated_at: str
    goal: str
    focus: str
    blocker: str | None
    next: str


CurrentState = StructuredCurrentState | LegacyCurrentState


class PublishEnvelope(TypedDict):
    """The two values one atomic publish carries."""

    current_state: PublishState
    changes: dict[str, object]


class BriefDocument(TypedDict):
    """The top-level stored and delivered document shape."""

    title: str
    summary: str
    current_state: NotRequired[CurrentState]
    updates: list[dict[str, object]]
