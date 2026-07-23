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
# f1-f20 only: pynput's Key defines no f21+, and every accepted name
# must also be suppressible on macOS (_NAMED_VKS below) — anything
# else is rejected at parse time instead of degrading silently.
_NAMED_KEYS = frozenset(
    [f"f{i}" for i in range(1, 21)]
    + [
        "space", "tab", "enter", "esc", "home", "end",
        "page_up", "page_down", "up", "down", "left", "right",
        "delete", "backspace",
    ]
)

# Layout-independent macOS virtual keycodes (Carbon kVK_*) for EVERY
# named key in _NAMED_KEYS, so all documented named chords get the
# suppressing event tap (character keys are probed per layout).
_NAMED_VKS = {
    "esc": 53, "enter": 36, "tab": 48, "space": 49,
    "backspace": 51, "delete": 117,
    "home": 115, "end": 119, "page_up": 116, "page_down": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96,
    "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113,
    "f16": 106, "f17": 64, "f18": 79, "f19": 80, "f20": 90,
}


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
            if name not in _NAMED_KEYS:
                # Reject unknown/unsupported named keys up front:
                # accepting them here would silently degrade to the
                # non-suppressing fallback on macOS (chord leaks into
                # the focused app) or fail at listener startup.
                raise ValueError(
                    f"unsupported named key {part!r}; supported: "
                    f"{', '.join(sorted(_NAMED_KEYS))}"
                )
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


class _SuppressingHotKeys:
    """macOS listener consuming one or more chords via a single event tap."""

    def __init__(
        self,
        bindings: list[tuple],  # (mods, terminal, callback, when|None)
    ) -> None:
        import Quartz
        from pynput import keyboard

        self._quartz = Quartz
        masks = {
            "ctrl": Quartz.kCGEventFlagMaskControl,
            "alt": Quartz.kCGEventFlagMaskAlternate,
            "cmd": Quartz.kCGEventFlagMaskCommand,
            "shift": Quartz.kCGEventFlagMaskShift,
        }
        # All chord-relevant modifier bits. Matching compares these for
        # EQUALITY, so "<ctrl>+;" does not also fire on Ctrl+Shift+;
        # (exact-chord promise); lock/synthetic flags such as Caps Lock
        # and Fn stay outside the mask and never affect chord identity.
        self._mod_mask = 0
        for m in masks.values():
            self._mod_mask |= m
        # vk -> list of (mask, callback, when); ``when`` (nullable)
        # gates matching dynamically: a False-returning ``when`` lets
        # the event pass through untouched, so e.g. Escape can cancel
        # recording without being globally stolen from other apps.
        self._by_vk: dict[int, list[tuple]] = {}
        for mods, terminal, callback, when in bindings:
            mask = 0
            for m in mods:
                mask |= masks[m]
            vk = _resolve_vk(Quartz, terminal)
            if vk is None:
                raise ValueError(
                    f"could not resolve key {terminal!r} on this "
                    "keyboard layout"
                )
            self._by_vk.setdefault(vk, []).append(
                (mask, callback, when)
            )
        self._down: set[int] = set()
        # In-flight callback threads, tracked so stop() can wait for
        # them: a toggle/paste callback must not inject keystrokes
        # after the app has reported that it stopped.
        self._threads: list[threading.Thread] = []
        self._stopping = False
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
        entries = self._by_vk.get(vk)
        if entries is None:
            return event
        if event_type == q.kCGEventKeyDown:
            if vk in self._down:
                # Autorepeat of an already-accepted chord. Swallow it
                # WITHOUT re-evaluating predicates: a conditional chord
                # (Escape-to-cancel) whose `when` turned False after
                # the first press must not start leaking repeats into
                # the focused app while its key-up is still swallowed.
                return None
            flags = q.CGEventGetFlags(event)
            for mask, callback, when in entries:
                if (flags & self._mod_mask) != mask:
                    continue  # extra or missing modifiers: not ours
                if when is not None:
                    try:
                        if not when():
                            continue  # pass through untouched
                    except Exception:
                        continue
                repeat = q.CGEventGetIntegerValueField(
                    event, q.kCGKeyboardEventAutorepeat
                )
                if not repeat and not self._stopping:
                    self._down.add(vk)
                    thread = threading.Thread(
                        target=callback, daemon=True
                    )
                    self._threads = [
                        t for t in self._threads if t.is_alive()
                    ]
                    self._threads.append(thread)
                    thread.start()
                return None  # swallow the chord (and auto-repeats)
            return event
        if vk in self._down:
            self._down.discard(vk)
            return None  # swallow the matching key-up
        return event

    def stop(self) -> None:
        """Stop the listener and wait for in-flight callbacks.

        New callbacks are refused first, then any already-started ones
        are joined (bounded), so no keystroke injection or side effect
        can land after shutdown reports completion.
        """
        self._stopping = True
        self._listener.stop()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads = [t for t in self._threads if t.is_alive()]


def _resolve_vk(quartz, terminal: str) -> int | None:  # noqa: ANN001
    """Map a parsed terminal (char or ``<name>``) to a virtual keycode."""
    if len(terminal) == 1:
        return _vk_for_char(quartz, terminal)
    return _NAMED_VKS.get(terminal.strip("<>"))


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


def check_permissions() -> list[str]:
    """Report missing macOS permissions that silently kill hotkeys.

    macOS attributes keystroke access to the LAUNCH CONTEXT (the
    terminal app — or tmux, when launched inside tmux), and a context
    without Input Monitoring gets a dead event tap with no error: the
    app looks healthy but hotkeys simply never fire. Observed in the
    field when a rebuilt venv/new launch context lost the prior grant.

    Returns human-readable warnings (empty ONLY when every check ran
    and every permission is granted — an unavailable module/symbol or
    a probe that throws produces a "could not verify" warning instead
    of silently passing) and asks macOS to register/prompt for the
    missing access so the user can grant it in System Settings.
    """
    if sys.platform != "darwin":
        return []
    warnings: list[str] = []
    warnings.extend(_check_input_monitoring())
    warnings.extend(_check_accessibility())
    return warnings


def _check_input_monitoring() -> list[str]:
    """Probe the Input Monitoring grant; never silently swallow failure."""
    try:
        import Quartz
    except Exception as e:  # pragma: no cover - environment-specific
        return [
            "could not verify Input Monitoring permission (Quartz "
            f"bridge unavailable: {e!r}) — hotkeys may silently not "
            "work. Check that pyobjc is installed correctly."
        ]
    preflight = getattr(Quartz, "CGPreflightListenEventAccess", None)
    if preflight is None:
        return [
            "could not verify Input Monitoring permission (this "
            "macOS/pyobjc lacks CGPreflightListenEventAccess) — "
            "hotkeys may silently not work."
        ]
    try:
        granted = bool(preflight())
    except Exception as e:
        return [
            "could not verify Input Monitoring permission (the "
            f"CGPreflightListenEventAccess probe failed: {e!r}) — "
            "hotkeys may silently not work."
        ]
    if granted:
        return []
    warnings = [
        "Input Monitoring permission MISSING for this launch "
        "context — hotkeys will NOT work. Grant it in System "
        "Settings > Privacy & Security > Input Monitoring "
        "(to your terminal app, and to tmux if you launched "
        "inside tmux), then restart voxtype."
    ]
    # Nonfatal: registration just makes the app appear in System
    # Settings so the user can flip the toggle — but a failure is
    # still reported, never silently suppressed.
    try:
        Quartz.CGRequestListenEventAccess()
    except Exception as e:
        warnings.append(
            "requesting Input Monitoring registration failed "
            f"({e!r}); add your terminal app manually in System "
            "Settings > Privacy & Security > Input Monitoring."
        )
    return warnings


def _check_accessibility() -> list[str]:
    """Probe the Accessibility grant; never silently swallow failure."""
    try:
        from ApplicationServices import AXIsProcessTrusted
    except Exception as e:  # pragma: no cover - environment-specific
        return [
            "could not verify Accessibility permission "
            f"(ApplicationServices bridge unavailable: {e!r}) — "
            "typing into other apps may silently not work. Check "
            "that pyobjc is installed correctly."
        ]
    try:
        trusted = bool(AXIsProcessTrusted())
    except Exception as e:
        return [
            "could not verify Accessibility permission (the "
            f"AXIsProcessTrusted probe failed: {e!r}) — typing into "
            "other apps may silently not work."
        ]
    if trusted:
        return []
    warnings = [
        "Accessibility permission MISSING for this launch "
        "context — typing into other apps will NOT work. "
        "Grant it in System Settings > Privacy & Security > "
        "Accessibility, then restart voxtype."
    ]
    # Nonfatal: the prompt request registers the app in System
    # Settings (and may show the system grant dialog) so the user can
    # flip the toggle — a failure is still reported, never silently
    # suppressed.
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        AXIsProcessTrustedWithOptions(
            {kAXTrustedCheckOptionPrompt: True}
        )
    except Exception as e:
        warnings.append(
            "requesting Accessibility registration failed "
            f"({e!r}); add your terminal app manually in System "
            "Settings > Privacy & Security > Accessibility."
        )
    return warnings


def start_hotkeys(bindings: list[tuple]):  # noqa: ANN201
    """Start one global listener for several chords; returns ``stop()``-able.

    Each binding is ``(hotkey, callback)`` or ``(hotkey, callback,
    when)`` — a nullable predicate polled at keypress time: when it
    returns False the chord passes through to the focused app
    untouched (used for context-dependent keys like Escape-to-cancel).

    On macOS every matched chord is fully suppressed. Elsewhere -- or
    when suppression isn't possible for a key -- falls back to
    pynput's observing ``GlobalHotKeys`` (apps may also receive the
    keys; ``when`` is then checked inside the callback).

    Raises:
        ValueError: If any hotkey string is malformed.
    """
    parsed = []
    for binding in bindings:
        hk, cb = binding[0], binding[1]
        when = binding[2] if len(binding) > 2 else None
        mods, term = parse_hotkey(hk)  # validate on every platform
        parsed.append((mods, term, cb, when))
    if sys.platform == "darwin":
        try:
            return _SuppressingHotKeys(parsed)
        except ValueError as e:
            print(
                f"[voxtype] {e}; falling back to non-suppressing "
                "hotkeys (chords may leak keystrokes)",
                file=sys.stderr,
            )
    from pynput import keyboard

    # Rebuild canonical pynput syntax (GlobalHotKeys doesn't know our
    # bracket-less spellings); gate conditionals inside the callback.
    def _gated(cb, when):  # noqa: ANN001, ANN202
        if when is None:
            return cb
        return lambda: when() and cb()

    canonical = {
        "+".join(
            [f"<{m}>" for m in sorted(mods)]
            + [term if len(term) > 1 else term]
        ): _gated(cb, when)
        for mods, term, cb, when in parsed
    }
    hotkeys = keyboard.GlobalHotKeys(canonical)
    hotkeys.daemon = True
    hotkeys.start()
    return hotkeys


def start_hotkey(hotkey: str, callback: Callable[[], None]):  # noqa: ANN201
    """Single-chord convenience wrapper around ``start_hotkeys``."""
    return start_hotkeys([(hotkey, callback)])
