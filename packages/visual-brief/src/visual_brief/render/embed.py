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
    return {
        "title": data["title"],
        "summary": data["summary"],
        "updates": [_project_update(update) for update in data["updates"]],
    }


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
    """Return one forensic entry: raw evidence text or a nested note."""
    if isinstance(entry, str):
        return entry
    projected: dict[str, Any] = {
        "title": entry["title"],
        "body": entry["body"],
    }
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
