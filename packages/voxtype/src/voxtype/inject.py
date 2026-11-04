"""Keystroke injection and audio feedback.

Typing goes through pynput's ``keyboard.Controller``, which synthesizes
key events into whatever application currently has focus. On macOS the
process running voxtype (your terminal) needs Accessibility permission
(System Settings > Privacy & Security > Accessibility).

Characters that need Shift (capitals, ``?``, ``!``, ...) are typed as Shift
plus their real key. pynput would otherwise send them as key code 0 (the A
key) with the character attached, and apps that read the key code rather
than the attached character, such as Rex build 1696, insert "a" instead.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

_SYSTEM_SOUNDS = Path("/System/Library/Sounds")
# Pause between typed text and its Enter (see Typist.press_enter): a
# base, plus more per character since a long piece takes the receiving
# app longer to take in, capped so a submit never stalls for long.
_ENTER_SETTLE_S = 0.3
_ENTER_SETTLE_PER_CHAR_S = 0.0015
_ENTER_SETTLE_MAX_S = 2.0


class Typist:
    """Types text into the focused application via synthetic key events."""

    def __init__(self) -> None:
        from pynput.keyboard import Controller

        self._keyboard = Controller()
        self._typed_at = 0.0
        self._settle_s = 0.0
        # Looked up per take: the keyboard layout can change in between.
        self._shifted_lookup = _shifted_keycodes

    def type_text(self, text: str) -> None:
        """Type ``text`` at the current cursor position.

        A line break is typed as Shift+Enter, never a bare Enter: Enter
        would send a chat prompt or run a shell command partway through
        the text, while Shift+Enter inserts a new line in chat inputs
        and most editors. (Raw transcripts never contain line breaks;
        only structured LLM cleanup output does.)
        """
        # 128 key translations, well under a millisecond per take.
        lookup = getattr(self, "_shifted_lookup", None)
        shifted = lookup(self._keyboard) if lookup else {}
        # Every line-break form, including a lone "\r" (which pynput
        # would also type as a bare Enter).
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for i, line in enumerate(lines):
            if i:
                self.press_shift_enter()
            if line:
                self._type_line(line, shifted)
        self._typed_at = time.monotonic()
        self._settle_s = min(
            _ENTER_SETTLE_S + _ENTER_SETTLE_PER_CHAR_S * len(text),
            _ENTER_SETTLE_MAX_S,
        )

    def _type_line(self, line: str, shifted: dict[str, int]) -> None:
        """Type one line: plain runs as text, shifted characters as Shift+key."""
        from pynput.keyboard import Key, KeyCode

        for kind, value in plan_keystrokes(line, shifted):
            if kind == "text":
                self._keyboard.type(value)
            else:
                with self._keyboard.pressed(Key.shift):
                    self._keyboard.tap(KeyCode.from_vk(value))

    def press_shift_enter(self) -> None:
        """Insert a line break without submitting (Shift+Enter)."""
        from pynput.keyboard import Key

        with self._keyboard.pressed(Key.shift):
            self._keyboard.tap(Key.enter)

    def press_enter(self) -> None:
        """Press Enter in the focused application.

        Right after typing, wait a moment first: apps such as Claude Code
        treat a fast burst of keystrokes as a paste and swallow an Enter
        that arrives inside it, so the text shows up but is not sent.
        """
        from pynput.keyboard import Key

        wait = self._settle_s - (time.monotonic() - self._typed_at)
        if wait > 0:
            time.sleep(wait)
        self._keyboard.tap(Key.enter)


def plan_keystrokes(line: str, shifted: dict[str, int]) -> list[tuple[str, object]]:
    """Split a line into typed runs and Shift+key presses.

    Args:
        line: Text without line breaks.
        shifted: Character -> key code for characters typed with Shift on
            the current keyboard layout.

    Returns:
        ``("text", run)`` items for runs pynput can type as they are, and
        ``("shift", keycode)`` items for each shifted character.
    """
    plan: list[tuple[str, object]] = []
    run = ""
    for ch in line:
        if ch in shifted:
            if run:
                plan.append(("text", run))
                run = ""
            plan.append(("shift", shifted[ch]))
        else:
            run += ch
    if run:
        plan.append(("text", run))
    return plan


def _shifted_keycodes(keyboard: object) -> dict[str, int]:
    """Characters the current macOS layout types with Shift, and their keys.

    Only characters pynput cannot type directly are included, so plain
    keys keep pynput's own handling, and dead keys (e.g. Shift+6 on U.S.
    International, which would accent the next letter) are left out.
    Empty off macOS or on any failure, which leaves typing exactly as
    before.
    """
    if sys.platform != "darwin":
        return {}
    try:
        from pynput._util.darwin import keycode_context, keycode_to_string

        direct = getattr(keyboard, "_mapping", {}) or {}
        shift_state = 0x0200 >> 8  # UCKeyTranslate takes (modifiers >> 8)
        out: dict[str, int] = {}
        with keycode_context() as context:
            for vk in range(128):
                ch = keycode_to_string(context, vk, shift_state)
                if len(ch) == 1 and ch.isprintable() and ch not in direct \
                        and ch not in out and not _is_dead_key(context, vk, shift_state):
                    out[ch] = vk
        return out
    except Exception:  # noqa: BLE001 - typing must keep working
        return {}


def _is_dead_key(context: tuple, vk: int, modifier_state: int) -> bool:
    """True if pressing ``vk`` with these modifiers starts a dead-key sequence."""
    import ctypes

    from pynput._util.darwin import CarbonExtra

    keyboard_type, layout_data = context
    dead_key_state = ctypes.c_uint32()
    length = ctypes.c_uint8()
    chars = (ctypes.c_uint16 * 4)()
    CarbonExtra.UCKeyTranslate(
        layout_data, vk, 0,  # kUCKeyActionDown
        modifier_state, keyboard_type, 0,  # dead keys processed
        ctypes.byref(dead_key_state), 4, ctypes.byref(length), chars)
    return dead_key_state.value != 0


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
