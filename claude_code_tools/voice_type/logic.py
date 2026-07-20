"""Pure text-matching logic for wake words and stop phrases.

Kept dependency-free so it is trivially unit-testable. Matching is
case-insensitive, punctuation-insensitive, and word-boundary-aware
("claude" does not match inside "clauded").
"""

from __future__ import annotations

import re


def normalize_words(text: str) -> list[str]:
    """Lowercase ``text``, strip punctuation, and split into words."""
    return re.findall(r"[a-z0-9']+", text.lower())


def _find_subsequence(words: list[str], phrase_words: list[str]) -> int:
    """Return the index where ``phrase_words`` starts in ``words``, or -1."""
    if not phrase_words:
        return -1
    n, m = len(words), len(phrase_words)
    for i in range(n - m + 1):
        if words[i : i + m] == phrase_words:
            return i
    return -1


def contains_phrase(text: str, phrase: str) -> bool:
    """Return True if ``phrase`` occurs in ``text`` (normalized, whole words)."""
    return _find_subsequence(normalize_words(text), normalize_words(phrase)) >= 0


def is_exact_phrase(text: str, phrase: str) -> bool:
    """Return True if ``text`` is exactly ``phrase`` (normalized).

    Used for submit phrases: they only fire when the whole utterance is
    the phrase ("go"), never mid-sentence ("go to the file").
    """
    phrase_words = normalize_words(phrase)
    return bool(phrase_words) and normalize_words(text) == phrase_words


def text_after_wake_word(text: str, wake_word: str) -> str | None:
    """Return the words spoken after ``wake_word``, or None if absent.

    The remainder is reconstructed from normalized words, so the original
    casing/punctuation of the utterance is not preserved — acceptable for
    the "claude open the file" activation case.

    Args:
        text: The transcribed utterance.
        wake_word: The configured wake phrase.

    Returns:
        The (possibly empty) remainder string if the wake word was heard,
        otherwise ``None``.
    """
    words = normalize_words(text)
    phrase = normalize_words(wake_word)
    idx = _find_subsequence(words, phrase)
    if idx < 0:
        return None
    return " ".join(words[idx + len(phrase) :])
