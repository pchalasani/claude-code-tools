"""Hands-free safeguards: the word cap, the long-silence note, notes.

Split out of app.py to keep it readable; ``VoiceTypeApp`` inherits these
methods, and they use its state (``_lock``, ``_session_texts``, ``cfg``,
...). Notes are short messages shown under the ghost (see overlay.py).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .config import DEFAULT_CONFIG_PATH
from .logic import collapse_repeats, strip_fillers
from .mode_menu import chord_label

if TYPE_CHECKING:
    from .app import VoiceTypeApp

#: How long a note stays under the ghost, unless cleared sooner.
NOTE_SECONDS = 30.0


class HeldNotesMixin:
    """Word cap and notes for ``VoiceTypeApp`` (see app.py)."""

    def _hold_over_cap(
        self: VoiceTypeApp, text: str, n_words: int, session: list[str]
    ) -> None:
        """Keep an over-cap release out of the app, but rescuable.

        Nothing is typed. The text is saved to ``dropped.txt`` and
        becomes the paste-again hotkey's message on its own; the next
        message starts fresh.
        """
        if self.cfg.strip_fillers:
            text = collapse_repeats(strip_fillers(text))
        with self._lock:
            if session is self._session_texts:
                self._msg_start = len(session)
                self._sent = True
            session.append(text)
        cap = self.cfg.held_max_words
        where = self._save_unsent(
            text, f"{n_words} words, over held_max_words ({cap})"
        )
        self._status(
            f"{n_words} words held, over held_max_words ({cap}): "
            f"not typed{where}"
        )
        paste = self.cfg.paste_hotkey
        self._show_note(
            f"{n_words} words not typed (over {cap})."
            + (f" {chord_label(paste)} types them." if paste else "")
        )

    def _note_kept(self: VoiceTypeApp, n_words: int, gap: float) -> None:
        """Say that words held from before a long silence are kept."""
        self._status(
            f"still holding {n_words} words from before a "
            f"{gap:.0f}s silence"
        )
        key = self.cfg.discard_hotkey
        self._show_note(
            f"Keeping {n_words} words from {gap / 60:.0f} min ago."
            + (f" {chord_label(key)} drops them." if key else "")
        )

    @staticmethod
    def _save_unsent(text: str, why: str) -> str:
        """Append ``text`` to ``dropped.txt`` next to the default config.

        Returns:
            "; saved to <path>" for the status line, or "" on failure.
        """
        path = DEFAULT_CONFIG_PATH.parent / "dropped.txt"
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"--- {stamp}: {why}\n{text}\n\n")
        except OSError:
            return ""
        return f"; saved to {path}"

    def _show_note(self: VoiceTypeApp, text: str) -> None:
        """Show ``text`` under the ghost for ``NOTE_SECONDS``."""
        with self._lock:
            self._note = text
            self._note_until = time.monotonic() + NOTE_SECONDS

    def _clear_note(self: VoiceTypeApp) -> None:
        """Hide the note: what it was about has been sent or dropped."""
        with self._lock:
            self._note, self._note_until = "", 0.0

    def current_note(self: VoiceTypeApp) -> str:
        """The note to show under the ghost now, or ""."""
        with self._lock:
            if time.monotonic() < self._note_until:
                return self._note
            return ""
