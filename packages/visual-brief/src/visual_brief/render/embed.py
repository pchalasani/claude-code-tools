"""Embedding of a validated brief document into the generated page.

The document is delivered to the front end as one JSON blob rather than as
server-rendered markup. Two rules make that safe: the blob carries exactly the
fields the schema defines, so unknown payloads cannot inflate or recurse
through the page; and every character that could end the script element early
is escaped, so question text stays inert data.
"""

from __future__ import annotations

import html
import json
from typing import Any

DOCUMENT_ELEMENT_ID = "visual-brief-document"
ROOT_ELEMENT_ID = "visual-brief-root"


def escape(value: Any) -> str:
    """Return a safely escaped string representation.

    Args:
        value: Any value destined for HTML text or an attribute.

    Returns:
        The escaped text.
    """
    return html.escape(str(value), quote=True)


def embed_document(data: dict[str, Any]) -> str:
    """Serialize a validated document for an ``application/json`` element.

    Args:
        data: A validated visual brief document.

    Returns:
        JSON text with no character that can terminate the script element.
    """
    text = json.dumps(
        project_document(data),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def project_document(data: dict[str, Any]) -> dict[str, Any]:
    """Return only the schema fields of a validated document.

    Args:
        data: A validated visual brief document.

    Returns:
        A new document holding the fields the page renders.
    """
    projected: dict[str, Any] = {
        "title": data["title"],
        "summary": data["summary"],
        "updates": [_project_update(update) for update in data["updates"]],
    }
    if "current_state" in data:
        projected["current_state"] = _project_current_state(
            data["current_state"]
        )
    return projected


def _project_current_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return either supported stored current-state shape."""
    if "lanes" not in state:
        return {
            "updated_at": state["updated_at"],
            "goal": state["goal"],
            "focus": state["focus"],
            "blocker": state["blocker"],
            "next": state["next"],
        }
    projected: dict[str, Any] = {
        "updated_at": state["updated_at"],
        "headline": state["headline"],
        "summary": state["summary"],
        "lanes": [_project_lane(lane) for lane in state["lanes"]],
    }
    _attach_questions(projected, state)
    return projected


def _project_update(update: dict[str, Any]) -> dict[str, Any]:
    """Return one timeline update's schema fields."""
    return {
        "id": update["id"],
        "timestamp": update["timestamp"],
        "headline": update["headline"],
        "summary": update["summary"],
        "lanes": [_project_lane(lane) for lane in update["lanes"]],
    }


def _project_lane(lane: dict[str, Any]) -> dict[str, Any]:
    """Return one lane's schema fields, including its open preference."""
    projected: dict[str, Any] = {
        "id": lane["id"],
        "name": lane["name"],
        "items": [_project_item(item) for item in lane["items"]],
    }
    if lane.get("open", False):
        projected["open"] = True
    _attach_questions(projected, lane)
    return projected


def _project_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return one item's schema fields."""
    projected: dict[str, Any] = {
        "id": item["id"],
        "glance": item["glance"],
        "explanation": item["explanation"],
        "trust": item["trust"],
    }
    suggestions = item.get("suggestions", [])
    if suggestions:
        projected["suggestions"] = [
            {
                "label": suggestion["label"].strip(),
                "message": suggestion["message"].strip(),
            }
            for suggestion in suggestions
        ]
    forensics = item.get("forensics", [])
    if forensics:
        projected["forensics"] = [
            _project_forensic(entry) for entry in forensics
        ]
    tables = item.get("tables", [])
    if tables:
        projected["tables"] = [_project_table(table) for table in tables]
    _attach_questions(projected, item)
    return projected


def _project_forensic(entry: Any) -> Any:
    """Return one forensic entry: raw evidence text or a nested note.

    A note's declared id travels with it, because that is the name the page
    builds its row id from. Dropping it here would leave the note named by its
    title, so a later edit to that title would rename a row the reader's
    cursor is sitting on.
    """
    if isinstance(entry, str):
        return entry
    projected: dict[str, Any] = {
        "title": entry["title"],
        "body": entry["body"],
    }
    if "id" in entry:
        projected["id"] = entry["id"]
    children = entry.get("children", [])
    if children:
        projected["children"] = [
            _project_forensic(child) for child in children
        ]
    return projected


def _project_table(table: dict[str, Any]) -> dict[str, Any]:
    """Return one comparison table's schema fields.

    Cells become text here. The front end declares a table as strings and
    renders each cell as a text node, so a number or any other non-text cell
    that the schema tolerates has to arrive as the string the browser would
    otherwise fail to show.
    """
    return {
        "caption": table["caption"],
        "columns": [str(column) for column in table["columns"]],
        "rows": [[str(cell) for cell in row] for row in table["rows"]],
    }


def _attach_questions(projected: dict[str, Any], owner: dict[str, Any]) -> None:
    """Copy an owner's normalized question threads onto its projection."""
    questions = owner.get("questions")
    if questions is None:
        return
    projected["questions"] = [_project_thread(thread) for thread in questions]


def _project_thread(thread: dict[str, Any]) -> dict[str, Any]:
    """Return one question thread's schema fields."""
    anchor = thread["anchor"]
    return {
        "id": thread["id"],
        "anchor": {"kind": anchor["kind"], "path": anchor["path"]},
        "turns": [
            {"author": turn["author"], "text": turn["text"], "at": turn["at"]}
            for turn in thread["turns"]
        ],
    }
