"""Recently typed text, given to cleanup as context.

Cerebras cleanup sees one take at a time. The text typed just before it
(usually the speaker's previous message or two to the same chat) helps
it spell names and terms the same way and resolve unclear words.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque

#: Typed text older than this many seconds is not context.
CONTEXT_MAX_AGE = 1800.0

_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
# A run of this many words from the context, absent from the take,
# means the cleanup copied the context into its output.
_COPY_RUN = 6


class RecentText:
    """The last few typed texts, with when each was typed."""

    def __init__(self, keep: int = 20) -> None:
        self._items: deque[tuple[float, str]] = deque(maxlen=keep)
        self._lock = threading.Lock()

    def add(self, text: str) -> None:
        """Record ``text`` as just typed."""
        if text.strip():
            with self._lock:
                self._items.append((time.monotonic(), text.strip()))

    def recent(self, max_words: int) -> str:
        """Up to the last ``max_words`` words typed lately, or ""."""
        if max_words <= 0:
            return ""
        cutoff = time.monotonic() - CONTEXT_MAX_AGE
        with self._lock:
            texts = [t for at, t in self._items if at >= cutoff]
        words: list[str] = []
        for text in reversed(texts):
            words[:0] = text.split()
            if len(words) >= max_words:
                break
        return " ".join(words[-max_words:])


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD_RE.findall(text)]


def _common_in_order(a: tuple[str, ...], b: list[str]) -> int:
    """Length of the longest common subsequence of ``a`` and ``b``."""
    prev = [0] * (len(b) + 1)
    for x in a:
        row = [0]
        for j, y in enumerate(b):
            row.append(prev[j] + 1 if x == y else max(prev[j + 1], row[j]))
        prev = row
    return prev[-1]


def copied_context(raw: str, cleaned: str, context: str) -> bool:
    """True if ``cleaned`` repeats a run of ``context`` the speaker did
    not say.

    A run the speaker said nearly word for word is not a copy: a phrase
    repeated with a misheard name ("jay von" for "Jevon") spelled as in
    the context is a correction. Nearly means all but one of its words,
    in order, within a stretch of ``raw`` at most two words longer.
    """
    if not context:
        return False

    def runs(words: list[str]) -> set[tuple[str, ...]]:
        return {
            tuple(words[i:i + _COPY_RUN])
            for i in range(len(words) - _COPY_RUN + 1)
        }

    said = _words(raw)
    exact = runs(said)
    # Where each three-word sequence of the take starts. A run said with
    # one word changed has one half (three words) said exactly.
    where: dict[tuple[str, ...], list[int]] = {}
    for i in range(len(said) - 2):
        where.setdefault(tuple(said[i:i + 3]), []).append(i)
    half = _COPY_RUN // 2

    def nearly_said(run: tuple[str, ...]) -> bool:
        windows = set()
        for size in range(_COPY_RUN - 1, _COPY_RUN + 3):
            for at in where.get(run[:half], ()):
                windows.add((at, at + size))
            for at in where.get(run[half:], ()):
                windows.add((max(0, at + half - size), at + half))
        return any(
            _common_in_order(run, said[a:b]) >= _COPY_RUN - 1
            for a, b in windows
        )

    return any(
        run not in exact and not nearly_said(run)
        for run in runs(_words(cleaned)) & runs(_words(context))
    )
