"""Retry a speech piece that decodes to nothing, joined to the one before.

Speech detection sometimes splits "okay, show me" into "okay" and
"show me", and the short second piece then decodes to empty text, so
the show command is lost. Decoded together with the piece before it,
the words come back; the text the earlier piece already gave is
removed, leaving only the new words.
"""

from __future__ import annotations

import time
from typing import Callable

import numpy as np

from .logic import normalize_words

Transcribe = Callable[[np.ndarray, int], str]

#: Only a piece this soon after the previous one is retried.
MAX_GAP_SECONDS = 3.0
#: Only a previous piece this short is joined (it is decoded again).
MAX_PREVIOUS_SECONDS = 4.0
#: Silence put between the two pieces when they do not overlap.
JOIN_SILENCE_SECONDS = 0.2


def join_audio(
    first: np.ndarray, second: np.ndarray, rate: int
) -> np.ndarray:
    """``first`` then ``second``, without repeating audio they share.

    The second piece starts with up to half a second recorded before
    its speech (see ``preroll``), which can be the end of the first
    piece; those samples are identical, so the overlap is found exactly.
    """
    probe = 32
    nonzero = np.flatnonzero(second)
    if nonzero.size and second.size - nonzero[0] >= probe:
        at = int(nonzero[0])
        head = second[at:at + probe]
        tail = first[-int(rate * 1.0):]
        if tail.size >= probe:
            windows = np.lib.stride_tricks.sliding_window_view(tail, probe)
            hits = np.flatnonzero((windows == head).all(axis=1))
            if hits.size:
                cut = tail.size - int(hits[-1]) + at
                if cut <= second.size:
                    return np.concatenate([first, second[cut:]])
    gap = np.zeros(int(rate * JOIN_SILENCE_SECONDS), np.float32)
    return np.concatenate([first, gap, second])


def new_words(earlier: str, joined: str) -> str:
    """The words of ``joined`` after those of ``earlier``, or "".

    Empty unless ``joined`` starts with the same words as ``earlier``
    (ignoring case and punctuation): if the joined decode heard the
    earlier piece differently, nothing is trusted.
    """
    before = normalize_words(earlier)
    tokens = joined.split()
    words = normalize_words(joined)
    if not before or words[: len(before)] != before:
        return ""
    # Map the count of normalized words back onto the original tokens.
    seen = 0
    for i, token in enumerate(tokens):
        if seen == len(before):
            return " ".join(tokens[i:])
        seen += len(normalize_words(token))
    return ""


class EmptyRetry:
    """Wraps ``transcribe``: an empty decode soon after a short piece
    is decoded again joined to that piece, returning only new words."""

    def __init__(self, report: Callable[[str], None]) -> None:
        self._report = report
        self._previous: tuple[np.ndarray, str, float] | None = None

    def wrap(self, transcribe: Transcribe) -> Transcribe:
        """``transcribe`` with the retry."""

        def retrying(samples: np.ndarray, sample_rate: int) -> str:
            text = transcribe(samples, sample_rate)
            if not np.size(samples) or not np.abs(samples).max() > 0:
                return text  # the engine's warm-up or keepalive
            previous, self._previous = self._previous, None
            now = time.monotonic()
            if text:
                if np.size(samples) <= MAX_PREVIOUS_SECONDS * sample_rate:
                    self._previous = (np.asarray(samples), text, now)
                return text
            if previous is None or now - previous[2] > MAX_GAP_SECONDS:
                return text
            earlier, said, _ = previous
            joined = transcribe(
                join_audio(earlier, np.asarray(samples), sample_rate),
                sample_rate,
            )
            found = new_words(said, joined)
            if found:
                self._report(
                    f'empty piece, decoded with the one before: "{found}"'
                )
            return found

        return retrying
