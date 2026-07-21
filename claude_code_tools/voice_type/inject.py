"""Keystroke injection and audio feedback.

Typing goes through pynput's ``keyboard.Controller``, which synthesizes
key events into whatever application currently has focus. On macOS the
process running voice-type (your terminal) needs Accessibility permission
(System Settings > Privacy & Security > Accessibility).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SYSTEM_SOUNDS = Path("/System/Library/Sounds")


class Typist:
    """Types text into the focused application via synthetic key events."""

    def __init__(self) -> None:
        from pynput.keyboard import Controller

        self._keyboard = Controller()

    def type_text(self, text: str) -> None:
        """Type ``text`` at the current cursor position."""
        self._keyboard.type(text)

    def press_enter(self) -> None:
        """Press Enter in the focused application."""
        from pynput.keyboard import Key

        self._keyboard.tap(Key.enter)


def play_sound(name: str) -> None:
    """Play a named macOS system sound or a sound file path.

    ``name`` is either a system sound name (e.g. "Glass", "Bottle" —
    see /System/Library/Sounds) or an absolute path to an audio file.
    Silently no-ops off macOS, on empty names, and on missing files.
    """
    if sys.platform != "darwin" or not name:
        return
    sound = (
        Path(name)
        if "/" in name
        else _SYSTEM_SOUNDS / f"{name}.aiff"
    )
    if not sound.exists():
        return
    subprocess.Popen(
        ["afplay", str(sound)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def copy_to_clipboard(text: str) -> None:
    """Put ``text`` on the macOS clipboard (no-op elsewhere/on failure)."""
    if sys.platform != "darwin" or not text:
        return
    try:
        subprocess.run(
            ["pbcopy"], input=text.encode(), check=False, timeout=5
        )
    except Exception:
        pass
