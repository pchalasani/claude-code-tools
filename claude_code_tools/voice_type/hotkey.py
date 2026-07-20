"""Global hotkey handling that suppresses the chord on macOS.

pynput's ``GlobalHotKeys`` only observes keystrokes -- the chord still
reaches the focused app, so a toggle like ``<ctrl>+;`` can also type
";" into whatever you're dictating into. On macOS we instead run a
pynput ``Listener`` with a ``darwin_intercept`` that swallows exactly
the hotkey chord (including auto-repeats and the matching key-up) and
passes every other event through untouched. Non-mac platforms fall
back to the observing ``GlobalHotKeys`` behavior.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable

_MODIFIERS = {
    "ctrl": "ctrl",
    "alt": "alt",
    "option": "alt",
    "cmd": "cmd",
    "super": "cmd",
    "shift": "shift",
}

# Named keys accepted without angle brackets ("ctrl+f5" == "<ctrl>+<f5>").
_NAMED_KEYS = frozenset(
    [f"f{i}" for i in range(1, 25)]
    + [
        "space", "tab", "enter", "esc", "home", "end",
        "page_up", "page_down", "up", "down", "left", "right",
        "delete", "backspace",
    ]
)


def parse_hotkey(hotkey: str) -> tuple[frozenset[str], str]:
    """Split a pynput-style hotkey into (modifiers, terminal key).

    Args:
        hotkey: e.g. ``"<ctrl>+;"`` or ``"<ctrl>+<alt>+d"``.

    Returns:
        A pair of (canonical modifier names, terminal character). The
        terminal may also be a ``<named>`` key like ``<f5>``, returned
        with its angle brackets intact.

    Raises:
        ValueError: If the hotkey is malformed (no terminal key, more
            than one terminal key, or an unknown modifier).
    """
    mods: set[str] = set()
    terminal: str | None = None
    for part in hotkey.strip().lower().split("+"):
        part = part.strip()
        if part.startswith("<") and part.endswith(">") and len(part) > 2:
            name = part[1:-1]
            if name in _MODIFIERS:
                mods.add(_MODIFIERS[name])
                continue
            key = part  # named non-modifier key, e.g. <f5>
        elif part in _MODIFIERS:
            # bracket-less spelling: "ctrl+;" == "<ctrl>+;"
            mods.add(_MODIFIERS[part])
            continue
        elif len(part) == 1:
            key = part
        elif part in _NAMED_KEYS:
            key = f"<{part}>"  # bare named key, e.g. "f5"
        else:
            raise ValueError(f"cannot parse hotkey part {part!r}")
        if terminal is not None:
            raise ValueError(
                f"hotkey {hotkey!r} has more than one non-modifier key"
            )
        terminal = key
    if terminal is None:
        raise ValueError(f"hotkey {hotkey!r} has no non-modifier key")
    return frozenset(mods), terminal


class _SuppressingHotKey:
    """macOS hotkey listener that consumes the chord via an event tap."""

    def __init__(
        self, mods: frozenset[str], char: str, callback: Callable[[], None]
    ) -> None:
        import Quartz
        from pynput import keyboard

        self._quartz = Quartz
        self._callback = callback
        masks = {
            "ctrl": Quartz.kCGEventFlagMaskControl,
            "alt": Quartz.kCGEventFlagMaskAlternate,
            "cmd": Quartz.kCGEventFlagMaskCommand,
            "shift": Quartz.kCGEventFlagMaskShift,
        }
        self._mask = 0
        for m in mods:
            self._mask |= masks[m]
        vk = _vk_for_char(Quartz, char)
        if vk is None:
            raise ValueError(
                f"could not resolve key {char!r} on this keyboard layout"
            )
        self._vk = vk
        self._down = False
        self._listener = keyboard.Listener(
            darwin_intercept=self._intercept
        )
        self._listener.daemon = True
        self._listener.start()

    def _intercept(self, event_type: int, event):  # noqa: ANN001, ANN202
        q = self._quartz
        if event_type not in (q.kCGEventKeyDown, q.kCGEventKeyUp):
            return event
        vk = q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode)
        if vk != self._vk:
            return event
        if event_type == q.kCGEventKeyDown:
            flags = q.CGEventGetFlags(event)
            if (flags & self._mask) != self._mask:
                return event
            repeat = q.CGEventGetIntegerValueField(
                event, q.kCGKeyboardEventAutorepeat
            )
            if not repeat:
                self._down = True
                threading.Thread(
                    target=self._callback, daemon=True
                ).start()
            return None  # swallow the chord (and its auto-repeats)
        if self._down:
            self._down = False
            return None  # swallow the matching key-up
        return event

    def stop(self) -> None:
        self._listener.stop()


def _vk_for_char(quartz, char: str) -> int | None:  # noqa: ANN001
    """Find the virtual keycode producing ``char`` in the current layout.

    Probes synthetic (never posted) keyboard events for each keycode and
    reads back the character they would produce.
    """
    for vk in range(128):
        event = quartz.CGEventCreateKeyboardEvent(None, vk, True)
        _, s = quartz.CGEventKeyboardGetUnicodeString(event, 4, None, None)
        if s == char:
            return vk
    return None


def record_hotkey(timeout: float = 15.0) -> str | None:
    """Capture one key chord from the keyboard; return its config string.

    Waits for the user to press modifiers plus one non-modifier key and
    formats the result in the notation ``parse_hotkey`` accepts (e.g.
    ``"<ctrl>+;"``). Returns None if nothing was pressed in ``timeout``
    seconds. Requires the same Input Monitoring permission as the
    hotkey listener itself.
    """
    from pynput import keyboard

    mod_names = {
        keyboard.Key.ctrl: "ctrl",
        keyboard.Key.ctrl_l: "ctrl",
        keyboard.Key.ctrl_r: "ctrl",
        keyboard.Key.alt: "alt",
        keyboard.Key.alt_l: "alt",
        keyboard.Key.alt_r: "alt",
        keyboard.Key.alt_gr: "alt",
        keyboard.Key.cmd: "cmd",
        keyboard.Key.cmd_l: "cmd",
        keyboard.Key.cmd_r: "cmd",
        keyboard.Key.shift: "shift",
        keyboard.Key.shift_l: "shift",
        keyboard.Key.shift_r: "shift",
    }
    order = ("ctrl", "alt", "shift", "cmd")
    held: set[str] = set()
    result: dict[str, str] = {}
    listener_box: dict = {}

    def on_press(key):  # noqa: ANN001, ANN202
        if key in mod_names:
            held.add(mod_names[key])
            return None
        listener = listener_box.get("l")
        base = listener.canonical(key) if listener else key
        char = getattr(base, "char", None)
        if isinstance(char, str) and len(char) == 1 and char.isprintable():
            terminal = char.lower()
        else:
            name = getattr(key, "name", None)
            if not name:
                return None
            terminal = f"<{name}>"
        parts = [f"<{m}>" for m in order if m in held]
        result["chord"] = "+".join(parts + [terminal])
        return False  # stop the listener

    def on_release(key):  # noqa: ANN001, ANN202
        if key in mod_names:
            held.discard(mod_names[key])
        return None

    with keyboard.Listener(
        on_press=on_press, on_release=on_release
    ) as listener:
        listener_box["l"] = listener
        listener.join(timeout)
    return result.get("chord")


def start_hotkey(hotkey: str, callback: Callable[[], None]):  # noqa: ANN201
    """Start a global hotkey listener; returns an object with ``stop()``.

    On macOS the chord is fully suppressed (the focused app never sees
    it). Elsewhere -- or when suppression isn't possible, e.g. a named
    terminal key like ``<f5>`` -- falls back to pynput's observing
    ``GlobalHotKeys``, in which case the app may also receive the keys.

    Raises:
        ValueError: If the hotkey string is malformed.
    """
    mods, terminal = parse_hotkey(hotkey)  # validate on every platform
    if sys.platform == "darwin" and len(terminal) == 1:
        try:
            return _SuppressingHotKey(mods, terminal, callback)
        except ValueError as e:
            print(
                f"[voice-type] {e}; falling back to non-suppressing "
                "hotkey (the chord may leak keystrokes)",
                file=sys.stderr,
            )
    from pynput import keyboard

    hotkeys = keyboard.GlobalHotKeys({hotkey: callback})
    hotkeys.daemon = True
    hotkeys.start()
    return hotkeys
