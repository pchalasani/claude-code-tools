"""Escaping and element builders for visual brief pages."""

from __future__ import annotations

import html
from typing import Any

from .validate import TRUST_LABELS


def escape(value: Any) -> str:
    """Return a safely escaped string representation."""
    return html.escape(str(value), quote=True)


def _question_form(anchor_id: str, form_id: str) -> str:
    """Render one hidden inline question form."""
    anchor = escape(anchor_id)
    return (
        f'<form class="question-box" id="{escape(form_id)}" '
        f'data-anchor-id="{anchor}">'
        f'<label for="{escape(form_id)}-text">Ask about this section</label>'
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
        f'aria-label="{escape(label)}">?</button>'
    )


def _render_nested(node: dict[str, Any]) -> str:
    """Render a recursively nestable forensic note."""
    nested = "".join(_render_nested(child) for child in node.get("children", []))
    return (
        '<details class="nested"><summary>'
        f'{escape(node["title"])}</summary><div class="nested-body">'
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


def _render_questions(questions: list[dict[str, Any]] | None) -> str:
    """Render answered question-and-answer pairs."""
    if questions is None:
        return ""
    rendered: list[str] = []
    for pair in questions:
        rendered.append(
            '<div class="qa"><p class="qa-q"><span class="qa-label">'
            f'QUESTION</span>{escape(pair["question"])}</p>'
            f'<p class="qa-a"><span class="qa-label">ANSWER</span>'
            f'{escape(pair["answer"])}</p></div>'
        )
    return "".join(rendered)


def _has_answer(questions: list[dict[str, Any]] | None) -> bool:
    """Return whether a question collection has an answer."""
    return any(pair.get("answer") for pair in questions or [])


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
            else _render_nested(entry)
            for entry in forensics
        ]
        evidence = (
            '<details class="forensics"><summary>Raw evidence and deeper '
            f"forensics</summary>{''.join(parts)}</details>"
        )
    table_html = "".join(
        _render_table(table) for table in item.get("tables", [])
    )
    questions = _render_questions(item.get("questions"))
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
    answered = _has_answer(item.get("questions"))
    answered_open = " open" if answered else ""
    answered_badge = (
        '<span class="chip answered">Answered</span>' if answered else ""
    )
    return (
        f'<div class="item-shell" id="{escape(anchor_id)}">'
        f'<details class="item"{answered_open}>'
        "<summary>"
        f'<span class="item-head"><span class="glance">{escape(glance)}</span>'
        f"{answered_badge}"
        f'<span class="chip {trust}">{TRUST_LABELS[trust]}</span>'
        "</span></summary>"
        f'<div class="explanation">{escape(item["explanation"])}</div>'
        f"{table_html}{evidence}{questions}"
        f'<div class="signals">{signals}</div>'
        '<div class="status" aria-live="polite"></div></details>'
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
    questions = _render_questions(lane.get("questions"))
    item_answered = any(
        _has_answer(item.get("questions")) for item in lane.get("items", [])
    )
    lane_answered = item_answered or _has_answer(lane.get("questions"))
    open_attr = " open" if (lane.get("open", False) or lane_answered) else ""
    name = lane["name"]
    return (
        f'<section class="lane-shell" id="{escape(anchor_id)}"><details '
        f'class="lane"{open_attr}><summary><span class="lane-name">'
        f"{escape(name)}</span></summary>{rendered_items}{questions}</details>"
        f'{_ask_button(form_id, f"Ask about {name}")}'
        f"{_question_form(anchor_id, form_id)}</section>"
    )


def _render_update(update: dict[str, Any], newest: bool) -> str:
    """Render one timeline update."""
    update_id = update["id"]
    rendered_lanes = "".join(
        _render_lane(lane, update_id) for lane in update["lanes"]
    )
    return (
        f'<details class="update" id="{escape(update_id)}"'
        f'{" open" if newest else ""}><summary><span class="update-head">'
        f'<span class="update-title">{escape(update["headline"])}</span>'
        f'<time class="time">{escape(update["timestamp"])}</time>'
        "</span></summary>"
        '<div class="update-body"><p class="update-summary">'
        f'{escape(update["summary"])}</p>{rendered_lanes}</div></details>'
    )


def render_timeline(updates: list[dict[str, Any]]) -> str:
    """Render updates newest first, opening only the newest update."""
    chronological = list(reversed(updates))
    return "".join(
        _render_update(update, index == 0)
        for index, update in enumerate(chronological)
    )
