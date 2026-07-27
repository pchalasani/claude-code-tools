"""Read the document that a rendered visual brief page delivers.

The page carries the validated document as one ``application/json`` blob and
lets the front end render it, so tests assert against the delivered document
rather than against server-generated markup.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

DOCUMENT_BLOB = re.compile(
    r'<script type="application/json" id="visual-brief-document">'
    r"(?P<json>.*?)</script>",
    re.DOTALL,
)


def page_text(page: str | bytes) -> str:
    """Return one rendered page as text.

    Args:
        page: A rendered page as text or UTF-8 bytes.

    Returns:
        The page text.
    """
    return page.decode("utf-8") if isinstance(page, bytes) else page


def embedded_document(page: str | bytes) -> dict[str, Any]:
    """Return the document embedded in a rendered page.

    Args:
        page: A rendered page as text or UTF-8 bytes.

    Returns:
        The delivered document.

    Raises:
        AssertionError: If the page carries no readable document blob.
    """
    match = DOCUMENT_BLOB.search(page_text(page))
    assert match is not None, "rendered page carries no embedded document"
    value = json.loads(match.group("json"))
    assert isinstance(value, dict)
    return value


def iter_items(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield every delivered item with its anchor path.

    Args:
        document: A delivered document.

    Yields:
        Pairs of anchor path and item.
    """
    for update in document["updates"]:
        for lane in update["lanes"]:
            for item in lane["items"]:
                yield f'{update["id"]}/{lane["id"]}/{item["id"]}', item


def iter_threads(
    document: dict[str, Any],
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield every delivered question thread with its anchor path.

    Args:
        document: A delivered document.

    Yields:
        Pairs of anchor path and thread.
    """
    for update in document["updates"]:
        for lane in update["lanes"]:
            lane_path = f'{update["id"]}/{lane["id"]}'
            for thread in lane.get("questions", []):
                yield lane_path, thread
            for item in lane["items"]:
                item_path = f'{lane_path}/{item["id"]}'
                for thread in item.get("questions", []):
                    yield item_path, thread


def delivered_threads(
    page: str | bytes,
    path: str,
    texts: list[str],
) -> list[dict[str, Any]]:
    """Return a page's threads at one anchor with exactly these turn texts.

    Args:
        page: A rendered page as text or UTF-8 bytes.
        path: Anchor path to match.
        texts: Turn texts, oldest first.

    Returns:
        The matching threads in document order.
    """
    return [
        thread
        for anchor, thread in iter_threads(embedded_document(page))
        if anchor == path and turn_texts(thread) == texts
    ]


def delivered_thread(page: str | bytes, thread_id: str) -> dict[str, Any]:
    """Return one thread a rendered page delivers.

    Args:
        page: A rendered page as text or UTF-8 bytes.
        thread_id: Identifier to look for.

    Returns:
        The matching thread.
    """
    return find_thread(embedded_document(page), thread_id)


def thread_ids(document: dict[str, Any]) -> list[str]:
    """Return every delivered thread identifier in document order.

    Args:
        document: A delivered document.

    Returns:
        The thread identifiers.
    """
    return [thread["id"] for _, thread in iter_threads(document)]


def find_thread(document: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """Return one delivered thread by identifier.

    Args:
        document: A delivered document.
        thread_id: Identifier to look for.

    Returns:
        The matching thread.

    Raises:
        AssertionError: If no thread carries that identifier.
    """
    for _, thread in iter_threads(document):
        if thread["id"] == thread_id:
            return thread
    raise AssertionError(f"no delivered thread with id {thread_id!r}")


def is_awaiting(thread: dict[str, Any]) -> bool:
    """Return whether a delivered thread's newest turn is human-authored.

    Args:
        thread: A delivered thread.

    Returns:
        True when the thread awaits an agent answer.
    """
    return bool(thread["turns"]) and thread["turns"][-1]["author"] == "human"


def turn_texts(thread: dict[str, Any]) -> list[str]:
    """Return a delivered thread's turn texts, oldest first.

    Args:
        thread: A delivered thread.

    Returns:
        The turn texts in delivery order.
    """
    return [turn["text"] for turn in thread["turns"]]
