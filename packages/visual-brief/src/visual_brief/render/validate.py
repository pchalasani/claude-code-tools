"""Validation for visual brief content documents."""

from __future__ import annotations

from typing import Any

TRUST_LABELS = {
    "verified-by-me": "Verified by me",
    "reported-by-agent": "Reported by agent",
    "unverified": "Unverified",
    "known-limitation": "Known limitation",
}


def require_text(value: Any, location: str) -> str:
    """Validate and return a non-empty text value.

    Args:
        value: Candidate text value.
        location: JSON path used in validation errors.

    Returns:
        The stripped text.

    Raises:
        ValueError: If the value is not non-empty text.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be non-empty text")
    return value.strip()


def identifier(value: Any, location: str) -> str:
    """Validate and return a stable element identifier.

    Args:
        value: Candidate identifier.
        location: JSON path used in validation errors.

    Returns:
        The validated identifier.

    Raises:
        ValueError: If the identifier is empty or contains unsafe separators.
    """
    text = require_text(value, location)
    if "/" in text or any(character.isspace() for character in text):
        raise ValueError(f"{location} must not contain whitespace or '/'")
    return text


def _validate_unique_ids(values: list[Any], location: str) -> None:
    """Require objects with unique identifiers in one collection."""
    identifiers: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"{location}[{index}] must be an object")
        identifiers.append(identifier(value.get("id"), f"{location}[{index}].id"))
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{location} ids must be unique")


def _validate_anchor(anchor: Any, location: str, expected_path: str) -> None:
    """Validate the currently supported tagged anchor union."""
    if not isinstance(anchor, dict):
        raise ValueError(f"{location} must be an object")
    kind = require_text(anchor.get("kind"), f"{location}.kind")
    if kind != "element":
        raise ValueError(
            f"{location}.kind has unknown anchor kind {kind!r}; "
            "only 'element' is supported"
        )
    path = require_text(anchor.get("path"), f"{location}.path")
    if path != expected_path:
        raise ValueError(f"{location}.path must be {expected_path!r}")


def _validate_turns(turns: Any, location: str) -> None:
    """Validate a non-empty chronological turn collection."""
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"{location} must be a non-empty list")
    for index, turn in enumerate(turns):
        turn_location = f"{location}[{index}]"
        if not isinstance(turn, dict):
            raise ValueError(f"{turn_location} must be an object")
        author = require_text(turn.get("author"), f"{turn_location}.author")
        if author not in {"human", "agent"}:
            raise ValueError(
                f"{turn_location}.author must be 'human' or 'agent'"
            )
        require_text(turn.get("text"), f"{turn_location}.text")
        require_text(turn.get("at"), f"{turn_location}.at")


def _validate_questions(
    questions: Any,
    location: str,
    expected_path: str,
) -> list[str]:
    """Validate optional threaded questions and return their identifiers."""
    if questions is None:
        return []
    if not isinstance(questions, list):
        raise ValueError(f"{location} must be a list")
    thread_ids: list[str] = []
    for index, thread in enumerate(questions):
        thread_location = f"{location}[{index}]"
        if not isinstance(thread, dict):
            raise ValueError(f"{thread_location} must be an object")
        thread_ids.append(
            identifier(thread.get("id"), f"{thread_location}.id")
        )
        _validate_anchor(
            thread.get("anchor"),
            f"{thread_location}.anchor",
            expected_path,
        )
        _validate_turns(thread.get("turns"), f"{thread_location}.turns")
    return thread_ids


def _validate_nested(node: Any, location: str) -> None:
    """Validate one recursively nestable forensic note."""
    if not isinstance(node, dict):
        raise ValueError(f"{location} must be an object")
    require_text(node.get("title"), f"{location}.title")
    require_text(node.get("body"), f"{location}.body")
    children = node.get("children", [])
    if not isinstance(children, list):
        raise ValueError(f"{location}.children must be a list")
    for index, child in enumerate(children):
        _validate_nested(child, f"{location}.children[{index}]")


def _validate_table(table: Any, location: str) -> None:
    """Validate one comparison table."""
    if not isinstance(table, dict):
        raise ValueError(f"{location} must be an object")
    require_text(table.get("caption"), f"{location}.caption")
    columns = table.get("columns")
    rows = table.get("rows")
    if not isinstance(columns, list) or not columns:
        raise ValueError(f"{location}.columns must be a non-empty list")
    if not isinstance(rows, list):
        raise ValueError(f"{location}.rows must be a list")
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(columns):
            raise ValueError(
                f"{location}.rows[{index}] must match the column count"
            )


def _validate_item(item: Any, location: str, path: str) -> list[str]:
    """Validate one lane item."""
    if not isinstance(item, dict):
        raise ValueError(f"{location} must be an object")
    identifier(item.get("id"), f"{location}.id")
    require_text(item.get("glance"), f"{location}.glance")
    require_text(item.get("explanation"), f"{location}.explanation")
    raw_trust = item.get("trust")
    trust = require_text(raw_trust, f"{location}.trust")
    if raw_trust != trust or trust not in TRUST_LABELS:
        raise ValueError(f"{location}.trust is not a recognized trust chip")
    forensics = item.get("forensics", [])
    if not isinstance(forensics, list):
        raise ValueError(f"{location}.forensics must be a list")
    for index, entry in enumerate(forensics):
        if not isinstance(entry, str):
            _validate_nested(entry, f"{location}.forensics[{index}]")
    tables = item.get("tables", [])
    if not isinstance(tables, list):
        raise ValueError(f"{location}.tables must be a list")
    for index, table in enumerate(tables):
        _validate_table(table, f"{location}.tables[{index}]")
    return _validate_questions(
        item.get("questions"),
        f"{location}.questions",
        path,
    )


def _validate_lane(lane: Any, location: str, update_id: str) -> list[str]:
    """Validate one independently collapsible lane."""
    if not isinstance(lane, dict):
        raise ValueError(f"{location} must be an object")
    identifier(lane.get("id"), f"{location}.id")
    require_text(lane.get("name"), f"{location}.name")
    items = lane.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{location}.items must be a list")
    _validate_unique_ids(items, f"{location}.items")
    lane_id = lane["id"]
    lane_path = f"{update_id}/{lane_id}"
    thread_ids = _validate_questions(
        lane.get("questions"),
        f"{location}.questions",
        lane_path,
    )
    for index, item in enumerate(items):
        item_id = item.get("id") if isinstance(item, dict) else ""
        thread_ids.extend(
            _validate_item(
                item,
                f"{location}.items[{index}]",
                f"{lane_path}/{item_id}",
            )
        )
    return thread_ids


def _validate_update(update: Any, location: str) -> list[str]:
    """Validate one timeline update."""
    if not isinstance(update, dict):
        raise ValueError(f"{location} must be an object")
    identifier(update.get("id"), f"{location}.id")
    require_text(update.get("timestamp"), f"{location}.timestamp")
    require_text(update.get("headline"), f"{location}.headline")
    require_text(update.get("summary"), f"{location}.summary")
    lanes = update.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError(f"{location}.lanes must be a list")
    _validate_unique_ids(lanes, f"{location}.lanes")
    thread_ids: list[str] = []
    for index, lane in enumerate(lanes):
        thread_ids.extend(
            _validate_lane(
                lane,
                f"{location}.lanes[{index}]",
                update["id"],
            )
        )
    return thread_ids


def validate_document(data: Any) -> dict[str, Any]:
    """Validate and return a visual brief document.

    Args:
        data: Parsed JSON content.

    Returns:
        The validated top-level dictionary.

    Raises:
        ValueError: If any value violates the visual brief schema.
    """
    if not isinstance(data, dict):
        raise ValueError("top-level JSON value must be an object")
    require_text(data.get("title"), "title")
    require_text(data.get("summary"), "summary")
    updates = data.get("updates")
    if not isinstance(updates, list) or not updates:
        raise ValueError("updates must be a non-empty list")
    _validate_unique_ids(updates, "updates")
    thread_ids: list[str] = []
    for index, update in enumerate(updates):
        thread_ids.extend(_validate_update(update, f"updates[{index}]"))
    if len(thread_ids) != len(set(thread_ids)):
        raise ValueError("question thread ids must be unique")
    return data
