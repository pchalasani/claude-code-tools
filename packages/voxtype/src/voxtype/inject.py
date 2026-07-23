"""Keystroke injection and audio feedback.

Typing goes through pynput's ``keyboard.Controller``, which synthesizes
key events into whatever application currently has focus. On macOS the
process running voxtype (your terminal) needs Accessibility permission
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


def _resolve_sound(name: str) -> Path | None:
    """Map a sound name or path to an existing file, or None."""
    if not name:
        return None
    p = Path(name) if "/" in name else _SYSTEM_SOUNDS / f"{name}.aiff"
    return p if p.exists() else None


def play_sound(name: str) -> None:
    """Play a sound by spawning ``afplay`` (the fallback path).

    Higher-latency than :class:`SoundPlayer` because afplay's own
    CoreAudio startup delays audio-out ~100 ms; used only when the
    preloaded AudioServices path is unavailable. No-ops off macOS / on
    missing files.
    """
    sound = _resolve_sound(name) if sys.platform == "darwin" else None
    if sound is None:
        return
    subprocess.Popen(
        ["afplay", str(sound)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class SoundPlayer:
    """Instant start/stop sounds via CoreAudio ``AudioServices``.

    Spawning ``afplay`` per sound delays audio-out ~100 ms behind the
    in-process overlay, so the chime lands visibly after the waveform
    pill. AudioServices plays a *preloaded* system-sound id in well
    under a millisecond with no run loop, keeping sound and pill in
    sync. Sounds are registered once at construction; playback is
    thread-safe and asynchronous. Falls back to ``afplay`` when the
    framework can't be loaded, and no-ops off macOS.
    """

    def __init__(self, *names: str) -> None:
        self._ids: dict[str, int] = {}
        self._at = None
        self._cf = None
        self._ctypes = None
        if sys.platform == "darwin":
            try:
                import ctypes
                import ctypes.util

                self._ctypes = ctypes
                at = ctypes.CDLL(ctypes.util.find_library("AudioToolbox"))
                cf = ctypes.CDLL(
                    ctypes.util.find_library("CoreFoundation")
                )
                cf.CFURLCreateFromFileSystemRepresentation.restype = (
                    ctypes.c_void_p
                )
                cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_char_p,
                    ctypes.c_long,
                    ctypes.c_bool,
                ]
                cf.CFRelease.argtypes = [ctypes.c_void_p]
                at.AudioServicesCreateSystemSoundID.restype = (
                    ctypes.c_int32
                )
                at.AudioServicesCreateSystemSoundID.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_uint32),
                ]
                at.AudioServicesPlaySystemSound.argtypes = [
                    ctypes.c_uint32
                ]
                self._at, self._cf = at, cf
            except Exception:
                self._at = None
        for name in names:
            self._sid(name)  # preload so the first play is instant

    def _sid(self, name: str) -> int | None:
        """Return the (cached) system-sound id for ``name``, or None."""
        if not name or self._at is None:
            return None
        if name in self._ids:
            return self._ids[name]
        path = _resolve_sound(name)
        if path is None:
            return None
        try:
            raw = str(path).encode()
            url = self._cf.CFURLCreateFromFileSystemRepresentation(
                None, raw, len(raw), False
            )
            if not url:
                return None
            sid = self._ctypes.c_uint32(0)
            err = self._at.AudioServicesCreateSystemSoundID(
                url, self._ctypes.byref(sid)
            )
            self._cf.CFRelease(url)
            if err != 0 or sid.value == 0:
                return None
            self._ids[name] = sid.value
            return sid.value
        except Exception:
            return None

    def play(self, name: str) -> None:
        """Play ``name`` instantly; fall back to afplay if needed."""
        if not name or sys.platform != "darwin":
            return
        sid = self._sid(name)
        if sid is not None:
            try:
                self._at.AudioServicesPlaySystemSound(sid)
                return
            except Exception:
                pass
        play_sound(name)


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
