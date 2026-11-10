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


def copied_context(raw: str, cleaned: str, context: str) -> bool:
    """True if ``cleaned`` repeats a run of ``context`` the speaker did
    not say.

    A run counts as copied when most of its words are absent from
    ``raw``: a phrase the speaker repeated, with a misheard name in it
    spelled as in the context, is a correction, not a copy.
    """
    if not context:
        return False

    def runs(words: list[str]) -> set[tuple[str, ...]]:
        return {
            tuple(words[i:i + _COPY_RUN])
            for i in range(len(words) - _COPY_RUN + 1)
        }

    said = set(_words(raw))
    return any(
        2 * sum(w not in said for w in run) > _COPY_RUN
        for run in runs(_words(cleaned)) & runs(_words(context))
    )
