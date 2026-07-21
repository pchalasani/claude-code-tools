"""Pure text-matching logic for wake words and stop phrases.

Kept dependency-free so it is trivially unit-testable. Matching is
case-insensitive, punctuation-insensitive, and word-boundary-aware
("claude" does not match inside "clauded").
"""

from __future__ import annotations

import re


# A word is a run of Unicode letters/digits (underscore excluded), with
# apostrophes kept only INSIDE the word: "don't" stays whole, while the
# quotes in "'claude'" are stripped. ``\w`` is Unicode-aware, so accented
# ("café") and non-Latin ("クロード") words survive normalization.
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")


def normalize_words(text: str) -> list[str]:
    """Casefold ``text``, strip punctuation, and split into words.

    Unicode-aware: accented and non-Latin words are preserved whole
    (never ASCII-stripped into false matches like "café" vs "caf"),
    and casefolding handles non-ASCII case pairs correctly.
    """
    return _WORD_RE.findall(text.casefold())


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


FILLER_WORDS = ("uh", "um", "uhm", "umm", "erm", "mmm", "hmm")

# Leading/trailing punctuation runs around a whitespace-delimited token
# (underscore is a word char in \w, so it is listed explicitly).
_TOKEN_EDGE_PUNCT_RE = re.compile(r"^[\W_]+|[\W_]+$")


def strip_fillers(text: str) -> str:
    """Remove standalone filler words (uh, um, ...) from ``text``.

    A filler is standalone when a whitespace-delimited token is nothing
    but the filler plus surrounding punctuation: "um,", "Um...",
    "(um)", and '"um"' are all stripped (punctuation and all), while
    fillers embedded in real words ("umbrella", "gum") are untouched.
    Returns "" if the utterance was nothing but fillers.
    """
    kept = [
        token
        for token in text.split()
        if _TOKEN_EDGE_PUNCT_RE.sub("", token).lower()
        not in FILLER_WORDS
    ]
    return " ".join(kept)


# A word repeated 3+ times consecutively (dictation stutter like
# "I I I think"); optional comma/period between repeats. Two repeats
# are left alone — "very very good" is legitimate emphasis.
_REPEAT_RE = re.compile(
    r"\b([\w']+)(?:[,.]?\s+\1\b){2,}", re.IGNORECASE
)


def collapse_repeats(text: str) -> str:
    """Collapse a word stuttered 3+ times in a row down to one.

    "I I I think" -> "I think"; "no, no, no, fine" -> "no, fine";
    "very very good" is untouched (only two repeats). Keeps the first
    occurrence's casing.
    """
    return _REPEAT_RE.sub(r"\1", text)


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
