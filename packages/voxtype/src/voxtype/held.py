"""Hands-free safeguards: the word cap, the long-silence note, notes.

Split out of app.py to keep it readable; ``VoiceTypeApp`` inherits these
methods, and they use its state (``_lock``, ``_session_texts``, ``cfg``,
...). Notes are short messages, up to two lines, shown by the ghost (see overlay.py).
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

#: Past this many times ``held_max_words``, text set aside by the cap is
#: only saved to a file: the paste-again hotkey would flood the app.
PASTE_MAX_CAPS = 2


class HeldNotesMixin:
    """Word cap and notes for ``VoiceTypeApp`` (see app.py)."""

    def _cap_release(
        self: VoiceTypeApp,
        pieces: list[str],
        session: list[str],
        keep_last: bool,
    ) -> list[str]:
        """Apply ``held_max_words`` to released ``pieces``.

        Returns the pieces to type: all of them under the cap. Over it,
        a stray recording must not flood the app, but the words just
        spoken must not vanish with it: with ``keep_last`` (a spoken
        release) only the last piece is typed, the rest set aside (see
        ``_hold_over_cap``). A last piece over the cap itself is set
        aside too, leaving nothing to type.
        """
        cap = self.cfg.held_max_words
        if not 0 < cap < len(" ".join(pieces).split()):
            return pieces
        keep = pieces[-1:] if keep_last else []
        if len(" ".join(keep).split()) > cap:
            keep = []
        rest = pieces[: len(pieces) - len(keep)]
        self._hold_over_cap(" ".join(rest), session, bool(keep))
        return keep

    def _hold_over_cap(
        self: VoiceTypeApp, text: str, session: list[str], more: bool
    ) -> None:
        """Keep over-cap ``text`` out of the app, but rescuable.

        It is saved to ``dropped.txt``. Unless it is far over the cap,
        it also starts the paste-again hotkey's message; ``more`` means
        the rest of that message is about to be typed.
        """
        n_words = len(text.split())
        if self.cfg.strip_fillers:
            text = collapse_repeats(strip_fillers(text))
        cap = self.cfg.held_max_words
        with self._lock:
            # A pause and resume since it arrived started a new session:
            # then the paste-again hotkey cannot reach it.
            current = session is self._session_texts
            pasteable = current and n_words <= PASTE_MAX_CAPS * cap
            if current:
                self._msg_start = len(session)
                self._sent = not more
            session.append(text)
            if current and not pasteable:
                self._msg_start = len(session)
        where = self._save_unsent(
            text, f"{n_words} words, over held_max_words ({cap})"
        )
        self._status(
            f"{n_words} words held, over held_max_words ({cap}): "
            f"not typed{where}"
        )
        did = "typed only your last part" if more else "nothing typed"
        rest = "The rest is" if more else "It is"
        self._show_note(
            f"Over {cap:,} words: {did}."
            + (f"\n{rest} saved in dropped.txt." if where else "")
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
            + (
                f"\n{chord_label(key)} drops them, or they go with your "
                "message."
                if key
                else ""
            )
        )

    def _note_over_cap(self: VoiceTypeApp) -> None:
        """Warn, before any release, that held words passed the cap."""
        key = self.cfg.discard_hotkey
        self._show_note(
            f"Over {self.cfg.held_max_words:,} words recorded, "
            "maybe by accident."
            + (
                f"\nTo clear them, press {chord_label(key)} before you speak."
                if key
                else ""
            )
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
