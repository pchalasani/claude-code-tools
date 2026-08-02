"""Focused folded-identity tests for saved and queued human turns."""

from visual_brief.writes.queue_view import (
    DocumentView,
    QueueRecord,
    document_view,
    is_folded,
)

ANCHOR = "update/lane/item"
PARENT = "q-saved-thread"
TEXT = "Is this the same turn?"
TIMESTAMP = "2026-08-01T12:00:00Z"


def _saved_reply_view() -> DocumentView:
    """Return a document view containing one saved reply identity."""
    document = {
        "updates": [
            {
                "id": "update",
                "lanes": [
                    {
                        "id": "lane",
                        "items": [
                            {
                                "id": "item",
                                "questions": [
                                    {
                                        "id": PARENT,
                                        "turns": [
                                            {
                                                "author": "agent",
                                                "text": "What should I clarify?",
                                            },
                                            {
                                                "author": "human",
                                                "text": TEXT,
                                                "at": TIMESTAMP,
                                            },
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    return document_view(document)


def _record(parent_id: str | None) -> QueueRecord:
    """Return the queued identity that differs only by parent."""
    return QueueRecord(ANCHOR, TEXT, TIMESTAMP, parent_id)


def test_root_question_does_not_match_saved_reply() -> None:
    """A root turn cannot fold against an otherwise identical reply."""
    assert not is_folded(_record(None), _saved_reply_view())


def test_reply_with_same_parent_matches_saved_reply() -> None:
    """The exact saved reply identity remains folded."""
    assert is_folded(_record(PARENT), _saved_reply_view())
