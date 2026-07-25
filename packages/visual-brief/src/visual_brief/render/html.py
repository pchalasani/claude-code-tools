"""Escaping and element builders for visual brief pages."""

from __future__ import annotations

import html
from typing import Any

from .threads import thread_is_awaiting
from .validate import TRUST_LABELS


def escape(value: Any) -> str:
    """Return a safely escaped string representation."""
    return html.escape(str(value), quote=True)


def _question_form(
    anchor_id: str,
    form_id: str,
    parent_id: str | None = None,
) -> str:
    """Render one safely attributed question or reply form."""
    anchor = escape(anchor_id)
    parent = (
        f' data-parent-id="{escape(parent_id)}"' if parent_id is not None else ""
    )
    form_class = "question-box reply-box" if parent_id else "question-box"
    label = "Reply to this thread" if parent_id else "Ask about this section"
    return (
        f'<form class="{form_class}" id="{escape(form_id)}" '
        f'data-anchor-id="{anchor}"{parent}>'
        f'<label for="{escape(form_id)}-text">{label}</label>'
        f'<textarea id="{escape(form_id)}-text" required '
        f'placeholder="What would you like clarified?"></textarea>'
        '<div class="actions"><button class="submit" type="submit">Send '
        'question</button></div><div class="status" aria-live="polite"></div>'
        "</form>"
    )


def _ask_button(form_id: str, label: str) -> str:
    """Render a question affordance."""
    return (
        f'<button class="ask-button" type="button" '
        f'data-target="{escape(form_id)}" '
        f'aria-controls="{escape(form_id)}" aria-expanded="false" '
        f'aria-label="{escape(label)}">?</button>'
    )


def _render_nested(node: dict[str, Any], control_id: str) -> str:
    """Render a recursively nestable forensic note."""
    nested = "".join(
        _render_nested(child, f"{control_id}-{index}")
        for index, child in enumerate(node.get("children", []))
    )
    return (
        '<details class="nested"><summary '
        f'aria-controls="{escape(control_id)}">{escape(node["title"])}</summary>'
        f'<div class="nested-body" id="{escape(control_id)}">'
        f'{escape(node["body"])}{nested}</div></details>'
    )


def _render_table(table: dict[str, Any]) -> str:
    """Render one comparison table."""
    columns = table["columns"]
    headers = "".join(f"<th>{escape(column)}</th>" for column in columns)
    rendered_rows: list[str] = []
    for row in table["rows"]:
        cells = "".join(
            f'<td class="{"wrong" if str(cell).startswith("WRONG") else ""}">'
            f"{escape(cell)}</td>"
            for cell in row
        )
        rendered_rows.append(f"<tr>{cells}</tr>")
    return (
        '<div class="table-wrap"><table>'
        f'<caption>{escape(table["caption"])}</caption>'
        f"<thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rendered_rows)}</tbody></table></div>"
    )


def _render_questions(
    questions: list[dict[str, Any]] | None,
    anchor_id: str,
) -> str:
    """Render chronological question threads."""
    if questions is None:
        return ""
    rendered: list[str] = []
    for thread in questions:
        thread_id = thread["id"]
        turns = thread["turns"]
        awaiting = thread_is_awaiting(thread)
        body_id = f"thread-body-{thread_id}"
        focus_id = f"{anchor_id}#{thread_id}"
        first_human = next(
            (turn["text"] for turn in turns if turn["author"] == "human"),
            "Conversation",
        )
        rendered_turns = "".join(
            '<div class="turn '
            f'{escape(turn["author"])}"><div class="turn-meta">'
            f'<span>{escape(turn["author"].upper())}</span>'
            f'<time>{escape(turn["at"])}</time></div>'
            f'<p>{escape(turn["text"])}</p></div>'
            for turn in turns
        )
        badge = (
            '<span class="chip answered">Awaiting answer</span>'
            if awaiting
            else ""
        )
        open_attr = " open" if awaiting else ""
        awaiting_attr = " data-awaiting" if awaiting else ""
        reply_id = f"reply-{thread_id}"
        rendered.append(
            f'<details class="thread"{open_attr}{awaiting_attr}>'
            f'<summary data-nav-kind="thread" '
            f'data-focus-id="{escape(focus_id)}" '
            f'aria-controls="{escape(body_id)}">'
            f'<span class="thread-title">{escape(first_human)}</span>'
            f"{badge}</summary>"
            f'<div class="thread-body" id="{escape(body_id)}">'
            f"{rendered_turns}"
            f"{_question_form(anchor_id, reply_id, thread_id)}"
            "</div></details>"
        )
    return "".join(rendered)


def _has_awaiting(questions: list[dict[str, Any]] | None) -> bool:
    """Return whether a question collection awaits an agent answer."""
    return any(thread_is_awaiting(thread) for thread in questions or [])


def _render_item(
    item: dict[str, Any],
    scope: str,
) -> str:
    """Render an item with glance, explanation, and optional evidence."""
    anchor_id = f'{scope}/{item["id"]}'
    glance = item["glance"]
    trust = item["trust"]
    form_id = f"ask-{anchor_id}"
    evidence = ""
    forensics = item.get("forensics", [])
    if forensics:
        parts = [
            f"<pre>{escape(entry)}</pre>"
            if isinstance(entry, str)
            else _render_nested(entry, f"{anchor_id}-nested-{index}")
            for index, entry in enumerate(forensics)
        ]
        evidence_id = f"{anchor_id}-forensics"
        evidence = (
            '<details class="forensics"><summary '
            f'aria-controls="{escape(evidence_id)}">Raw evidence and deeper '
            f'forensics</summary><div id="{escape(evidence_id)}">'
            f"{''.join(parts)}</div></details>"
        )
    table_html = "".join(
        _render_table(table) for table in item.get("tables", [])
    )
    questions = _render_questions(item.get("questions"), anchor_id)
    signals = "".join(
        f'<button class="signal" type="button" '
        f'data-anchor-id="{escape(anchor_id)}" '
        f'data-signal="{signal}">{label}</button>'
        for signal, label in (
            ("too-dense", "Too dense"),
            ("show-evidence", "Show evidence"),
            ("go-deeper", "Go deeper"),
            ("skip", "Skip"),
        )
    )
    awaiting = _has_awaiting(item.get("questions"))
    item_open = " open" if awaiting else ""
    waiting_badge = (
        '<span class="chip answered">Awaiting answer</span>' if awaiting else ""
    )
    body_id = f"item-body-{anchor_id}"
    return (
        f'<div class="item-shell" id="{escape(anchor_id)}">'
        f'<details class="item"{item_open}>'
        f'<summary data-nav-kind="item" data-focus-id="{escape(anchor_id)}" '
        f'aria-controls="{escape(body_id)}">'
        f'<span class="item-head"><span class="glance">{escape(glance)}</span>'
        f"{waiting_badge}"
        f'<span class="chip {trust}">{TRUST_LABELS[trust]}</span>'
        "</span></summary>"
        f'<div class="item-body" id="{escape(body_id)}">'
        f'<div class="explanation">{escape(item["explanation"])}</div>'
        f"{table_html}{evidence}{questions}"
        f'<div class="signals">{signals}</div>'
        '<div class="status" aria-live="polite"></div></div></details>'
        f'{_ask_button(form_id, f"Ask about {glance}")}'
        f"{_question_form(anchor_id, form_id)}</div>"
    )


def _render_lane(
    lane: dict[str, Any],
    update_id: str,
) -> str:
    """Render one independently collapsible lane."""
    anchor_id = f'{update_id}/{lane["id"]}'
    form_id = f"ask-{anchor_id}"
    rendered_items = "".join(
        _render_item(item, anchor_id) for item in lane["items"]
    )
    questions = _render_questions(lane.get("questions"), anchor_id)
    item_awaiting = any(
        _has_awaiting(item.get("questions")) for item in lane.get("items", [])
    )
    lane_awaiting = item_awaiting or _has_awaiting(lane.get("questions"))
    open_attr = " open" if (lane.get("open", False) or lane_awaiting) else ""
    name = lane["name"]
    body_id = f"lane-body-{anchor_id}"
    return (
        f'<section class="lane-shell" id="{escape(anchor_id)}"><details '
        f'class="lane"{open_attr}><summary data-nav-kind="lane" '
        f'data-focus-id="{escape(anchor_id)}" '
        f'aria-controls="{escape(body_id)}">'
        f'<span class="lane-name">{escape(name)}</span></summary>'
        f'<div class="lane-body" id="{escape(body_id)}">'
        f"{rendered_items}{questions}</div></details>"
        f'{_ask_button(form_id, f"Ask about {name}")}'
        f"{_question_form(anchor_id, form_id)}</section>"
    )


def _render_update(update: dict[str, Any], newest: bool) -> str:
    """Render one timeline update."""
    update_id = update["id"]
    rendered_lanes = "".join(
        _render_lane(lane, update_id) for lane in update["lanes"]
    )
    awaiting = any(
        _has_awaiting(lane.get("questions"))
        or any(
            _has_awaiting(item.get("questions"))
            for item in lane.get("items", [])
        )
        for lane in update["lanes"]
    )
    opened = newest or awaiting
    body_id = f"update-body-{update_id}"
    return (
        f'<details class="update" id="{escape(update_id)}"'
        f'{" open" if opened else ""}><summary '
        f'data-focus-id="{escape(update_id)}" '
        f'aria-controls="{escape(body_id)}">'
        '<span class="update-head">'
        f'<span class="update-title">{escape(update["headline"])}</span>'
        f'<time class="time">{escape(update["timestamp"])}</time>'
        "</span></summary>"
        f'<div class="update-body" id="{escape(body_id)}">'
        '<p class="update-summary">'
        f'{escape(update["summary"])}</p>{rendered_lanes}</div></details>'
    )


def render_timeline(updates: list[dict[str, Any]]) -> str:
    """Render updates newest first, opening only the newest update."""
    chronological = list(reversed(updates))
    return "".join(
        _render_update(update, index == 0)
        for index, update in enumerate(chronological)
    )
