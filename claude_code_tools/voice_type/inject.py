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

_ACTIVATE_SOUND = Path("/System/Library/Sounds/Pop.aiff")
_DEACTIVATE_SOUND = Path("/System/Library/Sounds/Bottle.aiff")


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


def play_sound(activate: bool) -> None:
    """Play a short system sound (macOS only; silently no-ops elsewhere)."""
    if sys.platform != "darwin":
        return
    sound = _ACTIVATE_SOUND if activate else _DEACTIVATE_SOUND
    if not sound.exists():
        return
    subprocess.Popen(
        ["afplay", str(sound)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
