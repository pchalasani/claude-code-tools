"""Validation for visual brief content documents."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

from visual_brief import MAX_THREAD_ID_LENGTH
from visual_brief.render.note_names import note_name, require_distinct_note_names
from visual_brief.schema import (
    CURRENT_STATE_ROOT,
    LEGACY_ANCHOR_ALIASES_FIELD,
    MAX_SUGGESTIONS,
    MAX_SUGGESTION_LABEL_LENGTH,
    MAX_SUGGESTION_MESSAGE_LENGTH,
    current_state_item_path,
    current_state_lane_path,
)

TRUST_LABELS = {
    "verified-by-me": "Verified by me",
    "reported-by-agent": "Reported by agent",
    "unverified": "Unverified",
    "known-limitation": "Known limitation",
}

_LEGACY_STATE_FIELDS = {"updated_at", "goal", "focus", "blocker", "next"}
_LEGACY_STATE_PROSE_FIELDS = ("goal", "focus", "blocker", "next")
_STRUCTURED_STATE_FIELDS = {"updated_at", "headline", "summary", "lanes"}
_STRUCTURED_STATE_OPTIONAL_FIELDS = {"questions"}
_SENTENCE_END = re.compile(r"[.!?…]$")
_BULLET = r"[\u2022\u2023\u2043\u204c\u204d\u25aa\u25cf\u25e6]"
_BULLET_MARKER = rf"(?:[-*+]|{_BULLET})"
_NUMBERED_MARKER = r"(?:\d+[.)]|\(\d+\))"
_LIST_START = re.compile(
    rf"^\s*(?:{_BULLET_MARKER}\s+|{_NUMBERED_MARKER}\s+)"
)
_INLINE_BULLET_LIST = re.compile(
    rf"(?:^|\s){_BULLET_MARKER}\s+\S.*?\s{_BULLET_MARKER}\s+\S"
)
_INLINE_UNICODE_BULLET_ITEM = re.compile(rf"(?:^|\s){_BULLET}\s+\S")
_INLINE_NUMBERED_LIST = re.compile(
    rf"(?:^|\s){_NUMBERED_MARKER}\s+\S.*?\s{_NUMBERED_MARKER}\s+\S"
)
_HEADING_START = re.compile(r"^\s*#{1,6}\s+")
_COMPACT_STATUS_TOKEN = r"(?=[\w-]*[^\W\d_])[\w-]+"
_COMPACT_STATUS_CHAIN = re.compile(
    rf"(?<![\w./>]){_COMPACT_STATUS_TOKEN}([/>]){_COMPACT_STATUS_TOKEN}"
    rf"(?:\1{_COMPACT_STATUS_TOKEN}){{1,}}(?:\.(?![\w./>])|(?![\w./>]))"
)
_SPACED_STATUS_CHAIN = re.compile(
    r"\S+(?:\s+\S+)*\s+([/>])\s+\S+(?:\s+\S+)*\s+\1\s+\S+"
)
_MIXED_STATUS_CHAIN = re.compile(
    rf"(?<![\w./>]){_COMPACT_STATUS_TOKEN}\s*(?:"
    rf"/\s*{_COMPACT_STATUS_TOKEN}(?:\s+{_COMPACT_STATUS_TOKEN})*\s*>"
    rf"\s*{_COMPACT_STATUS_TOKEN}|"
    rf">\s*{_COMPACT_STATUS_TOKEN}(?:\s+{_COMPACT_STATUS_TOKEN})*\s*/"
    rf"\s*{_COMPACT_STATUS_TOKEN})"
)
_ASCII_ARROW = re.compile(r"(?:->|<-|=>)")


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


def _require_exact_fields(
    value: dict[str, Any],
    expected: set[str],
    location: str,
) -> None:
    """Require an object to carry exactly the named fields."""
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unexpected = sorted(str(field) for field in actual - expected)
    details: list[str] = []
    if missing:
        details.append(f"missing {', '.join(missing)}")
    if unexpected:
        details.append(f"unexpected {', '.join(unexpected)}")
    raise ValueError(
        f"{location} must have exactly these fields: " + "; ".join(details)
    )


def _validate_state_prose(
    value: Any,
    location: str,
    *,
    limit: int = 240,
    sentence: bool = True,
) -> str:
    """Validate one mechanically plain current-state text field."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be non-empty text")
    if len(value) > limit:
        raise ValueError(f"{location} must be at most {limit} characters")
    if value.splitlines() != [value]:
        raise ValueError(f"{location} must be one line")
    if (
        _LIST_START.search(value)
        or _INLINE_BULLET_LIST.search(value)
        or _INLINE_UNICODE_BULLET_ITEM.search(value)
        or _INLINE_NUMBERED_LIST.search(value)
    ):
        raise ValueError(f"{location} must not be a list")
    if _HEADING_START.search(value):
        raise ValueError(f"{location} must not be a heading")
    if "|" in value:
        raise ValueError(f"{location} must not be a table")
    if "```" in value or "~~~" in value:
        raise ValueError(f"{location} must not contain a code fence")
    if _ASCII_ARROW.search(value) or any(
        "ARROW" in unicodedata.name(character, "") for character in value
    ):
        raise ValueError(f"{location} must not contain arrows")
    if (
        _COMPACT_STATUS_CHAIN.search(value)
        or _SPACED_STATUS_CHAIN.search(value)
        or _MIXED_STATUS_CHAIN.search(value)
    ):
        raise ValueError(f"{location} must not contain a status chain")
    if len(re.findall(r"\b\w+\b", value, flags=re.UNICODE)) < 4:
        raise ValueError(f"{location} must contain at least four words")
    if sentence and _SENTENCE_END.search(value) is None:
        raise ValueError(f"{location} must end in sentence punctuation")
    return value


def validate_current_state(value: Any, location: str = "current_state") -> None:
    """Validate either supported stored current-state shape.

    Args:
        value: Candidate stored state.
        location: JSON path used in validation errors.

    Raises:
        ValueError: If the state violates its exact mechanical schema.
    """
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    if set(value) == _LEGACY_STATE_FIELDS:
        _validate_legacy_current_state(value, location)
        return
    _validate_structured_current_state(value, location)


def _validate_legacy_current_state(
    value: dict[str, Any],
    location: str,
) -> None:
    """Validate the already-shipped four-claim state shape."""
    require_text(value.get("updated_at"), f"{location}.updated_at")
    for field in _LEGACY_STATE_PROSE_FIELDS:
        candidate = value.get(field)
        field_location = f"{location}.{field}"
        if field == "blocker" and candidate is None:
            continue
        require_text(candidate, field_location)


def _validate_structured_current_state(
    value: dict[str, Any],
    location: str,
) -> None:
    """Validate detailed current state and its stable conversation paths."""
    actual = set(value)
    allowed = _STRUCTURED_STATE_FIELDS | _STRUCTURED_STATE_OPTIONAL_FIELDS
    if not _STRUCTURED_STATE_FIELDS.issubset(actual) or not actual <= allowed:
        _require_exact_fields(value, _STRUCTURED_STATE_FIELDS, location)
    require_text(value.get("updated_at"), f"{location}.updated_at")
    _validate_state_prose(
        value.get("headline"),
        f"{location}.headline",
        limit=200,
        sentence=False,
    )
    _validate_state_prose(
        value.get("summary"),
        f"{location}.summary",
        limit=480,
    )
    thread_ids = _validate_questions(
        value.get("questions"),
        f"{location}.questions",
        CURRENT_STATE_ROOT,
    )
    lanes = value.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError(f"{location}.lanes must be a list")
    _validate_unique_ids(lanes, f"{location}.lanes")
    item_ids: list[str] = []
    for index, lane in enumerate(lanes):
        lane_location = f"{location}.lanes[{index}]"
        lane_threads, lane_items = _validate_state_lane(lane, lane_location)
        thread_ids.extend(lane_threads)
        item_ids.extend(lane_items)
    if len(item_ids) != len(set(item_ids)):
        raise ValueError(
            f"{location} item ids must be unique across every state lane"
        )
    if len(thread_ids) != len(set(thread_ids)):
        raise ValueError(f"{location} question thread ids must be unique")


def identifier(value: Any, location: str) -> str:
    """Validate and return a stable element identifier.

    ``/`` and ``#`` join identifiers into the row ids the page navigates by,
    so an identifier holding either could spell a row that belongs to
    something else.

    Args:
        value: Candidate identifier.
        location: JSON path used in validation errors.

    Returns:
        The validated identifier.

    Raises:
        ValueError: If the identifier is empty or contains unsafe separators.
    """
    text = require_text(value, location)
    if (
        value != text
        or "/" in text
        or "#" in text
        or any(character.isspace() for character in text)
    ):
        raise ValueError(
            f"{location} must not contain whitespace, '/' or '#'"
        )
    return text


def segment_identifier(value: Any, location: str) -> str:
    """Validate an identifier that becomes a ``#``-separated row-id segment.

    Question threads and forensic notes hang off a row with ``#``. The page
    invents segments of its own in that same place — an item's evidence, and
    the notes under it — and opens each with ``~``, so an identifier holding
    ``~`` there could name a row that already belongs to something else. Ids
    joined by ``/`` are under no such rule: no invented segment follows a
    slash, and narrowing the reservation keeps a document that has always
    rendered from being refused for a character it may safely use.

    Args:
        value: Candidate identifier.
        location: JSON path used in validation errors.

    Returns:
        The validated identifier.

    Raises:
        ValueError: If the identifier is unusable as a row-id segment.
    """
    text = identifier(value, location)
    if "~" in text:
        raise ValueError(f"{location} must not contain '~'")
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
    previous_at: datetime | None = None
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
        at_text = require_text(turn.get("at"), f"{turn_location}.at")
        try:
            at = datetime.fromisoformat(at_text.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(
                f"{turn_location}.at must be an ISO 8601 timestamp"
            ) from error
        if at.tzinfo is None:
            raise ValueError(
                f"{turn_location}.at must include a timezone"
            )
        if previous_at is not None and at < previous_at:
            raise ValueError(f"{location} must be chronological (oldest first)")
        previous_at = at


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
        thread_id = segment_identifier(
            thread.get("id"), f"{thread_location}.id"
        )
        if len(thread_id) > MAX_THREAD_ID_LENGTH:
            raise ValueError(
                f"{thread_location}.id must be at most "
                f"{MAX_THREAD_ID_LENGTH} characters"
            )
        thread_ids.append(thread_id)
        _validate_anchor(
            thread.get("anchor"),
            f"{thread_location}.anchor",
            expected_path,
        )
        _validate_turns(thread.get("turns"), f"{thread_location}.turns")
    return thread_ids


def _validate_nested(node: Any, location: str) -> str:
    """Validate one recursively nestable forensic note.

    Args:
        node: Candidate note.
        location: JSON path used in validation errors.

    Returns:
        The name the note answers to among its siblings: the id it declared,
        or a marked slug of its title.

    Raises:
        ValueError: If the note violates the forensic note schema.
    """
    if not isinstance(node, dict):
        raise ValueError(f"{location} must be an object")
    declared = (
        segment_identifier(node.get("id"), f"{location}.id")
        if "id" in node
        else None
    )
    title = require_text(node.get("title"), f"{location}.title")
    require_text(node.get("body"), f"{location}.body")
    _validate_children(node.get("children", []), f"{location}.children")
    return note_name(declared, title)


def _validate_children(children: Any, location: str) -> None:
    """Validate the notes nested under one note.

    Args:
        children: Candidate nested notes; nothing else may appear here.
        location: JSON path used in validation errors.

    Raises:
        ValueError: If a child is malformed or two of them answer to one name.
    """
    if not isinstance(children, list):
        raise ValueError(f"{location} must be a list")
    require_distinct_note_names(
        [
            _validate_nested(child, f"{location}[{index}]")
            for index, child in enumerate(children)
        ],
        location,
    )


def _validate_forensics(entries: Any, location: str) -> None:
    """Validate one item's forensics: raw evidence strings and nested notes.

    Args:
        entries: Candidate raw-evidence strings and nested notes.
        location: JSON path used in validation errors.

    Raises:
        ValueError: If an entry is malformed or two notes answer to one name.
    """
    if not isinstance(entries, list):
        raise ValueError(f"{location} must be a list")
    require_distinct_note_names(
        [
            _validate_nested(entry, f"{location}[{index}]")
            for index, entry in enumerate(entries)
            if not isinstance(entry, str)
        ],
        location,
    )


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


def _validate_suggestions(suggestions: Any, location: str) -> None:
    """Validate zero to three agent-authored human reply shorthands."""
    if not isinstance(suggestions, list):
        raise ValueError(f"{location} must be a list")
    if len(suggestions) > MAX_SUGGESTIONS:
        raise ValueError(
            f"{location} must contain at most {MAX_SUGGESTIONS} replies"
        )
    labels: list[str] = []
    messages: list[str] = []
    for index, suggestion in enumerate(suggestions):
        suggestion_location = f"{location}[{index}]"
        if not isinstance(suggestion, dict):
            raise ValueError(f"{suggestion_location} must be an object")
        _require_exact_fields(
            suggestion,
            {"label", "message"},
            suggestion_location,
        )
        label = require_text(
            suggestion.get("label"),
            f"{suggestion_location}.label",
        )
        message = require_text(
            suggestion.get("message"),
            f"{suggestion_location}.message",
        )
        if label.splitlines() != [label]:
            raise ValueError(f"{suggestion_location}.label must be one line")
        if len(label) > MAX_SUGGESTION_LABEL_LENGTH:
            raise ValueError(
                f"{suggestion_location}.label must be at most "
                f"{MAX_SUGGESTION_LABEL_LENGTH} characters"
            )
        if len(message) > MAX_SUGGESTION_MESSAGE_LENGTH:
            raise ValueError(
                f"{suggestion_location}.message must be at most "
                f"{MAX_SUGGESTION_MESSAGE_LENGTH} characters"
            )
        labels.append(label.casefold())
        messages.append(message)
    if len(labels) != len(set(labels)):
        raise ValueError(f"{location} labels must be unique")
    if len(messages) != len(set(messages)):
        raise ValueError(f"{location} messages must be unique")


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
    _validate_suggestions(
        item.get("suggestions", []),
        f"{location}.suggestions",
    )
    _validate_forensics(item.get("forensics", []), f"{location}.forensics")
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


def _validate_state_lane(
    lane: Any,
    location: str,
) -> tuple[list[str], list[str]]:
    """Validate one state lane using stable lane and global item anchors."""
    if not isinstance(lane, dict):
        raise ValueError(f"{location} must be an object")
    lane_id = identifier(lane.get("id"), f"{location}.id")
    require_text(lane.get("name"), f"{location}.name")
    items = lane.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{location}.items must be a list")
    _validate_unique_ids(items, f"{location}.items")
    thread_ids = _validate_questions(
        lane.get("questions"),
        f"{location}.questions",
        current_state_lane_path(lane_id),
    )
    item_ids: list[str] = []
    for index, item in enumerate(items):
        item_location = f"{location}.items[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{item_location} must be an object")
        item_id = identifier(item.get("id"), f"{item_location}.id")
        item_ids.append(item_id)
        thread_ids.extend(
            _validate_item(
                item,
                item_location,
                current_state_item_path(item_id),
            )
        )
    return thread_ids, item_ids


def _validate_update(update: Any, location: str) -> list[str]:
    """Validate one timeline update."""
    if not isinstance(update, dict):
        raise ValueError(f"{location} must be an object")
    identifier(update.get("id"), f"{location}.id")
    require_text(update.get("timestamp"), f"{location}.timestamp")
    require_text(update.get("headline"), f"{location}.headline")
    require_text(update.get("summary"), f"{location}.summary")
    update_id = update["id"]
    thread_ids = _validate_questions(
        update.get("questions"),
        f"{location}.questions",
        update_id,
    )
    lanes = update.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError(f"{location}.lanes must be a list")
    _validate_unique_ids(lanes, f"{location}.lanes")
    for index, lane in enumerate(lanes):
        thread_ids.extend(
            _validate_lane(
                lane,
                f"{location}.lanes[{index}]",
                update["id"],
            )
        )
    return thread_ids


def validate_publish_briefing(
    value: Any,
    location: str = "briefing",
) -> None:
    """Validate the stricter agent-authored briefing contract.

    Args:
        value: Candidate direct publish payload.
        location: JSON path used in validation errors.

    Raises:
        ValueError: If the briefing is malformed, cryptic, or outside the
            one-to-six lane range.
    """
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    _validate_state_prose(
        value.get("headline"),
        f"{location}.headline",
        limit=200,
        sentence=False,
    )
    _validate_state_prose(
        value.get("summary"),
        f"{location}.summary",
        limit=480,
    )
    lanes = value.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError(f"{location}.lanes must be a list")
    if not 1 <= len(lanes) <= 6:
        raise ValueError(f"{location}.lanes must contain 1 to 6 lanes")
    _validate_update(value, location)


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
    _validate_legacy_anchor_aliases(
        data.get(LEGACY_ANCHOR_ALIASES_FIELD),
    )
    thread_ids: list[str] = []
    if "current_state" in data:
        validate_current_state(data["current_state"])
        state = data["current_state"]
        if isinstance(state, dict) and "lanes" in state:
            thread_ids.extend(
                _validate_questions(
                    state.get("questions"),
                    "current_state.questions",
                    CURRENT_STATE_ROOT,
                )
            )
            for index, lane in enumerate(state.get("lanes", [])):
                lane_threads, _ = _validate_state_lane(
                    lane,
                    f"current_state.lanes[{index}]",
                )
                thread_ids.extend(lane_threads)
    updates = data.get("updates")
    if not isinstance(updates, list):
        raise ValueError("updates must be a list")
    _validate_unique_ids(updates, "updates")
    for index, update in enumerate(updates):
        thread_ids.extend(_validate_update(update, f"updates[{index}]"))
    if len(thread_ids) != len(set(thread_ids)):
        raise ValueError("question thread ids must be unique")
    return data


def _validate_legacy_anchor_aliases(value: Any) -> None:
    """Validate optional retired-current-state anchor mappings."""
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(
            f"{LEGACY_ANCHOR_ALIASES_FIELD} must be an object"
        )
    for source, target in value.items():
        if not isinstance(source, str) or not source:
            raise ValueError(
                f"{LEGACY_ANCHOR_ALIASES_FIELD} keys must be non-empty text"
            )
        require_text(
            target,
            f"{LEGACY_ANCHOR_ALIASES_FIELD}[{source!r}]",
        )
