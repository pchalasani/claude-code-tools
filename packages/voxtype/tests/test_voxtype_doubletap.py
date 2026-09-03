"""Tests for the ghost opacity knob and the double-tap modifier hotkey.

Covers config validation for the three new keys, the pure double-tap
state machine, the macOS event-tap integration (with stubbed Quartz and
pynput so no real tap is ever installed), and the app-level wiring that
passes opacity to the overlay and the double-tap binding to the listener.
"""

from __future__ import annotations

import inspect
import sys
import threading
import types
from types import SimpleNamespace

import pytest

from voxtype.config import (
    VALID_DOUBLE_TAP_KEYS,
    Config,
    load_config,
    sample_config,
)

# -- config ----------------------------------------------------------------


@pytest.mark.parametrize("value", [0.1, 0.6, 1.0, 1])
def test_overlay_opacity_accepts_range(value: float) -> None:
    Config(overlay_opacity=value).validate()


@pytest.mark.parametrize(
    "value",
    [0.0, 0.05, 1.5, -1.0, float("nan"), float("inf"), "0.5", True, None],
)
def test_overlay_opacity_rejects_bad_values(value: object) -> None:
    with pytest.raises(ValueError, match="overlay_opacity"):
        Config(overlay_opacity=value).validate()


@pytest.mark.parametrize("key", ["", *VALID_DOUBLE_TAP_KEYS])
def test_double_tap_key_accepts_valid_names(key: str) -> None:
    Config(double_tap_key=key).validate()


@pytest.mark.parametrize(
    "value", ["middle_alt", "alt", "<alt>", "RIGHT_ALT", 3, None]
)
def test_double_tap_key_rejects_bad_values(value: object) -> None:
    with pytest.raises(ValueError, match="double_tap_key"):
        Config(double_tap_key=value).validate()


@pytest.mark.parametrize("value", [50, 400, 2000, 400.0])
def test_double_tap_ms_accepts_range(value: float) -> None:
    Config(double_tap_ms=value).validate()


@pytest.mark.parametrize(
    "value", [49, 2001, -1, float("nan"), float("inf"), "400", True, None]
)
def test_double_tap_ms_rejects_bad_values(value: object) -> None:
    with pytest.raises(ValueError, match="double_tap_ms"):
        Config(double_tap_ms=value).validate()


def test_double_tap_key_names_match_hotkey_table() -> None:
    """Config's allowlist and the hotkey module's keycode table must
    name exactly the same keys, or a validated config could still fail
    at listener start."""
    from voxtype.hotkey import DOUBLE_TAP_KEYS

    assert set(VALID_DOUBLE_TAP_KEYS) == set(DOUBLE_TAP_KEYS)
    assert "right_alt" in DOUBLE_TAP_KEYS
    # Left and right must map to DIFFERENT keycodes and flag bits.
    assert DOUBLE_TAP_KEYS["right_alt"] != DOUBLE_TAP_KEYS["left_alt"]


def test_sample_config_documents_new_keys(tmp_path) -> None:  # noqa: ANN001
    text = sample_config()
    for key in ("overlay_opacity", "double_tap_key", "double_tap_ms"):
        assert key in text
    path = tmp_path / "config.toml"
    path.write_text(text)
    assert load_config(path) == Config()  # sample states the defaults


def test_load_config_reads_new_keys(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "config.toml"
    path.write_text(
        'overlay_opacity = 0.6\ndouble_tap_key = "right_alt"\n'
        "double_tap_ms = 300\n"
    )
    cfg = load_config(path)
    assert cfg.overlay_opacity == 0.6
    assert cfg.double_tap_key == "right_alt"
    assert cfg.double_tap_ms == 300


# -- double-tap state machine -----------------------------------------------


def _detector(window: float = 0.4):  # noqa: ANN202
    from voxtype.hotkey import DoubleTapDetector

    return DoubleTapDetector(vk=61, window=window)


def test_double_tap_fires_once_on_second_release() -> None:
    d = _detector()
    assert d.feed(61, True, 0.00) is False
    assert d.feed(61, False, 0.05) is False
    assert d.feed(61, True, 0.20) is False  # second press: not yet
    assert d.feed(61, False, 0.25) is True  # second release: fire
    # Fully reset: a lone third tap does not fire again.
    assert d.feed(61, True, 0.30) is False
    assert d.feed(61, False, 0.35) is False


def test_double_tap_too_slow_does_not_fire_but_rearms() -> None:
    d = _detector(window=0.4)
    d.feed(61, True, 0.0)
    d.feed(61, False, 0.1)
    # Second press 0.5 s after the first: outside the window.
    assert d.feed(61, True, 0.5) is False
    assert d.feed(61, False, 0.55) is False
    # ...but that late tap counts as a NEW first tap.
    assert d.feed(61, True, 0.7) is False
    assert d.feed(61, False, 0.75) is True


def test_double_tap_window_measured_press_to_press() -> None:
    d = _detector(window=0.4)
    d.feed(61, True, 0.0)
    d.feed(61, False, 0.35)  # long-ish first tap
    assert d.feed(61, True, 0.39) is False
    assert d.feed(61, False, 0.45) is True  # 2nd press was within 0.4 s


def test_other_key_between_taps_resets() -> None:
    d = _detector()
    d.feed(61, True, 0.0)
    d.feed(61, False, 0.05)
    d.feed(0x00, True, 0.10)  # some letter key
    d.feed(0x00, False, 0.12)
    assert d.feed(61, True, 0.20) is False
    assert d.feed(61, False, 0.25) is False  # the sequence was broken


def test_chord_during_second_hold_does_not_fire() -> None:
    """Option-Option-then-a-letter is the user typing an Option chord,
    not a double tap: the second release must NOT toggle recording."""
    d = _detector()
    d.feed(61, True, 0.0)
    d.feed(61, False, 0.05)
    d.feed(61, True, 0.15)
    d.feed(0x00, True, 0.20)  # a key pressed while Option is held
    d.feed(0x00, False, 0.22)
    assert d.feed(61, False, 0.30) is False


def test_other_modifier_never_fires() -> None:
    d = _detector()
    for t in (0.0, 0.05, 0.1, 0.15):
        assert d.feed(58, t % 0.1 == 0, t) is False  # left option


# -- macOS event-tap integration (stubbed Quartz + pynput) ------------------


class _FakeQuartz:
    kCGEventKeyDown = 10
    kCGEventKeyUp = 11
    kCGEventFlagsChanged = 12
    kCGKeyboardEventKeycode = 9
    kCGKeyboardEventAutorepeat = 8
    kCGEventFlagMaskShift = 1 << 17
    kCGEventFlagMaskControl = 1 << 18
    kCGEventFlagMaskAlternate = 1 << 19
    kCGEventFlagMaskCommand = 1 << 20

    @staticmethod
    def CGEventGetIntegerValueField(event, field):  # noqa: ANN001, ANN205
        return event.vk if field == 9 else event.repeat

    @staticmethod
    def CGEventGetFlags(event):  # noqa: ANN001, ANN205
        return event.flags


class _FakeListener:
    def __init__(self, darwin_intercept=None) -> None:  # noqa: ANN001
        self.intercept = darwin_intercept
        self.daemon = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def join(self, *a) -> None:  # noqa: ANN002
        pass


def _install_fakes(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setitem(sys.modules, "Quartz", _FakeQuartz)
    pynput = types.ModuleType("pynput")
    keyboard = types.ModuleType("pynput.keyboard")
    keyboard.Listener = _FakeListener
    pynput.keyboard = keyboard
    monkeypatch.setitem(sys.modules, "pynput", pynput)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", keyboard)


def _ev(vk: int, flags: int = 0, repeat: int = 0):  # noqa: ANN202
    return SimpleNamespace(vk=vk, flags=flags, repeat=repeat)


def test_event_tap_fires_on_right_alt_double_tap(monkeypatch) -> None:  # noqa: ANN001
    _install_fakes(monkeypatch)
    from voxtype.hotkey import DOUBLE_TAP_KEYS, _SuppressingHotKeys

    fired = threading.Event()
    vk, devbit = DOUBLE_TAP_KEYS["right_alt"]
    hk = _SuppressingHotKeys(
        [], double_tap=(vk, devbit, 0.4, fired.set, None)
    )
    q = _FakeQuartz
    alt = q.kCGEventFlagMaskAlternate
    seq = [
        _ev(vk, alt | devbit),  # down
        _ev(vk, 0),  # up
        _ev(vk, alt | devbit),  # down
        _ev(vk, 0),  # up -> fire
    ]
    for e in seq:
        # Modifier events are NEVER swallowed: Option must keep working.
        assert hk._intercept(q.kCGEventFlagsChanged, e) is e
    assert fired.wait(2.0), "double tap did not fire the callback"
    hk.stop()


def test_event_tap_left_alt_does_not_fire_right_alt_binding(
    monkeypatch,  # noqa: ANN001
) -> None:
    _install_fakes(monkeypatch)
    from voxtype.hotkey import DOUBLE_TAP_KEYS, _SuppressingHotKeys

    fired = threading.Event()
    vk, devbit = DOUBLE_TAP_KEYS["right_alt"]
    lvk, ldevbit = DOUBLE_TAP_KEYS["left_alt"]
    hk = _SuppressingHotKeys(
        [], double_tap=(vk, devbit, 0.4, fired.set, None)
    )
    q = _FakeQuartz
    alt = q.kCGEventFlagMaskAlternate
    for e in (
        _ev(lvk, alt | ldevbit),
        _ev(lvk, 0),
        _ev(lvk, alt | ldevbit),
        _ev(lvk, 0),
    ):
        assert hk._intercept(q.kCGEventFlagsChanged, e) is e
    assert not fired.wait(0.2)
    hk.stop()


def test_event_tap_held_shift_throughout_does_not_fire(monkeypatch) -> None:  # noqa: ANN001
    """Shift held from before the first tap never produces an event of
    its own, so the modifier check on each tap must catch the chord."""
    _install_fakes(monkeypatch)
    from voxtype.hotkey import DOUBLE_TAP_KEYS, _SuppressingHotKeys

    fired = threading.Event()
    vk, devbit = DOUBLE_TAP_KEYS["right_alt"]
    hk = _SuppressingHotKeys(
        [], double_tap=(vk, devbit, 0.4, fired.set, None)
    )
    q = _FakeQuartz
    alt, shift = q.kCGEventFlagMaskAlternate, q.kCGEventFlagMaskShift
    for e in (
        _ev(vk, shift | alt | devbit),
        _ev(vk, shift),
        _ev(vk, shift | alt | devbit),
        _ev(vk, shift),
    ):
        assert hk._intercept(q.kCGEventFlagsChanged, e) is e
    assert not fired.wait(0.2)
    # Same with the OPPOSITE-side key of the same family held: left
    # Option down throughout while right Option is tapped is a chord.
    lvk, ldevbit = DOUBLE_TAP_KEYS["left_alt"]
    for e in (
        _ev(vk, alt | ldevbit | devbit),
        _ev(vk, alt | ldevbit),
        _ev(vk, alt | ldevbit | devbit),
        _ev(vk, alt | ldevbit),
    ):
        hk._intercept(q.kCGEventFlagsChanged, e)
    assert not fired.wait(0.2)
    # ...and a clean double tap right after still works.
    for e in (
        _ev(vk, alt | devbit), _ev(vk, 0), _ev(vk, alt | devbit), _ev(vk, 0)
    ):
        hk._intercept(q.kCGEventFlagsChanged, e)
    assert fired.wait(2.0)
    hk.stop()


def test_event_tap_keydown_between_taps_resets(monkeypatch) -> None:  # noqa: ANN001
    _install_fakes(monkeypatch)
    from voxtype.hotkey import DOUBLE_TAP_KEYS, _SuppressingHotKeys

    fired = threading.Event()
    vk, devbit = DOUBLE_TAP_KEYS["right_alt"]
    hk = _SuppressingHotKeys(
        [], double_tap=(vk, devbit, 0.4, fired.set, None)
    )
    q = _FakeQuartz
    alt = q.kCGEventFlagMaskAlternate
    hk._intercept(q.kCGEventFlagsChanged, _ev(vk, alt | devbit))
    hk._intercept(q.kCGEventFlagsChanged, _ev(vk, 0))
    letter = _ev(0x00)
    # An unrelated key press passes through and breaks the sequence.
    assert hk._intercept(q.kCGEventKeyDown, letter) is letter
    assert hk._intercept(q.kCGEventKeyUp, letter) is letter
    hk._intercept(q.kCGEventFlagsChanged, _ev(vk, alt | devbit))
    hk._intercept(q.kCGEventFlagsChanged, _ev(vk, 0))
    assert not fired.wait(0.2)
    hk.stop()


def test_event_tap_double_tap_respects_when_predicate(monkeypatch) -> None:  # noqa: ANN001
    _install_fakes(monkeypatch)
    from voxtype.hotkey import DOUBLE_TAP_KEYS, _SuppressingHotKeys

    fired = threading.Event()
    vk, devbit = DOUBLE_TAP_KEYS["right_alt"]
    hk = _SuppressingHotKeys(
        [], double_tap=(vk, devbit, 0.4, fired.set, lambda: False)
    )
    q = _FakeQuartz
    alt = q.kCGEventFlagMaskAlternate
    for e in (
        _ev(vk, alt | devbit),
        _ev(vk, 0),
        _ev(vk, alt | devbit),
        _ev(vk, 0),
    ):
        hk._intercept(q.kCGEventFlagsChanged, e)
    assert not fired.wait(0.2)
    hk.stop()


def test_start_hotkeys_rejects_unknown_double_tap_key(monkeypatch) -> None:  # noqa: ANN001
    _install_fakes(monkeypatch)
    from voxtype.hotkey import start_hotkeys

    with pytest.raises(ValueError, match="double_tap"):
        start_hotkeys(
            [("<ctrl>+;", lambda: None)],
            double_tap=("middle_alt", 400, lambda: None),
        )


# -- app wiring ---------------------------------------------------------------


def test_app_passes_double_tap_binding_to_listener(monkeypatch) -> None:  # noqa: ANN001
    import voxtype.app as app_mod
    import voxtype.hotkey as hotkey_mod

    captured: dict = {}

    def fake_start_hotkeys(bindings, double_tap=None):  # noqa: ANN001, ANN202
        captured["bindings"] = bindings
        captured["double_tap"] = double_tap
        return SimpleNamespace(stop=lambda: None)

    monkeypatch.setattr(hotkey_mod, "start_hotkeys", fake_start_hotkeys)
    monkeypatch.setattr(hotkey_mod, "check_permissions", lambda: [])
    monkeypatch.setattr(app_mod, "Typist", lambda: SimpleNamespace())
    app = app_mod.VoiceTypeApp(
        Config(
            mode="toggle",
            sounds=False,
            overlay=False,
            double_tap_key="right_alt",
            double_tap_ms=300,
        )
    )
    assert app._start_hotkey_listener() is not None
    # The Ctrl+; chord is still registered alongside the double tap.
    assert [b[0] for b in captured["bindings"]][0] == "<ctrl>+;"
    key, ms, cb = captured["double_tap"][:3]
    assert (key, ms) == ("right_alt", 300)
    assert cb == app.toggle


def test_app_omits_double_tap_when_unset(monkeypatch) -> None:  # noqa: ANN001
    import voxtype.app as app_mod
    import voxtype.hotkey as hotkey_mod

    captured: dict = {}

    def fake_start_hotkeys(bindings, double_tap=None):  # noqa: ANN001, ANN202
        captured["double_tap"] = double_tap
        return SimpleNamespace(stop=lambda: None)

    monkeypatch.setattr(hotkey_mod, "start_hotkeys", fake_start_hotkeys)
    monkeypatch.setattr(hotkey_mod, "check_permissions", lambda: [])
    monkeypatch.setattr(app_mod, "Typist", lambda: SimpleNamespace())
    app = app_mod.VoiceTypeApp(
        Config(mode="toggle", sounds=False, overlay=False)
    )
    app._start_hotkey_listener()
    assert captured["double_tap"] is None


@pytest.mark.parametrize("armed", [True, False])
def test_app_reports_double_tap_only_when_armed(monkeypatch, armed) -> None:  # noqa: ANN001
    """The startup line claiming the double tap works must track the
    listener's own verdict, not the config: a fallback listener that
    ignored the binding must not be reported as active."""
    import voxtype.app as app_mod
    import voxtype.hotkey as hotkey_mod

    monkeypatch.setattr(
        hotkey_mod,
        "start_hotkeys",
        lambda bindings, double_tap=None: SimpleNamespace(
            stop=lambda: None, double_tap_active=armed
        ),
    )
    monkeypatch.setattr(hotkey_mod, "check_permissions", lambda: [])
    monkeypatch.setattr(app_mod, "Typist", lambda: SimpleNamespace())
    lines: list[str] = []
    monkeypatch.setattr(app_mod.VoiceTypeApp, "_status", staticmethod(lines.append))
    app = app_mod.VoiceTypeApp(
        Config(mode="toggle", sounds=False, overlay=False, double_tap_key="right_alt")
    )
    app._start_hotkey_listener()
    claimed = any("also toggles recording" in ln for ln in lines)
    assert claimed is armed


def test_suppressing_listener_exposes_double_tap_active(monkeypatch) -> None:  # noqa: ANN001
    _install_fakes(monkeypatch)
    from voxtype.hotkey import DOUBLE_TAP_KEYS, _SuppressingHotKeys

    vk, devbit = DOUBLE_TAP_KEYS["right_alt"]
    armed = _SuppressingHotKeys([], double_tap=(vk, devbit, 0.4, lambda: None, None))
    assert armed.double_tap_active is True
    bare = _SuppressingHotKeys([])
    assert bare.double_tap_active is False
    armed.stop()
    bare.stop()


def test_run_overlay_accepts_opacity() -> None:
    from voxtype.overlay import run_overlay

    params = inspect.signature(run_overlay).parameters
    assert "opacity" in params
    assert params["opacity"].default == 1.0


def test_app_passes_opacity_to_overlay(monkeypatch) -> None:  # noqa: ANN001
    import voxtype.app as app_mod
    import voxtype.engines as engines_mod
    import voxtype.overlay as overlay_mod

    captured: dict = {}

    class _Engine:
        fatal_error = None

        def start(self, *a, **k) -> None:  # noqa: ANN002, ANN003
            pass

        def stop(self) -> None:
            pass

    def fake_run_overlay(sample, tick, stopped, **kw):  # noqa: ANN001, ANN202
        captured.update(kw)
        if kw.get("on_ready"):
            kw["on_ready"]()

    monkeypatch.setattr(app_mod, "Typist", lambda: SimpleNamespace())
    monkeypatch.setattr(
        engines_mod, "create_engine", lambda cfg, status: _Engine()
    )
    monkeypatch.setattr(
        app_mod.VoiceTypeApp,
        "_start_hotkey_listener",
        lambda self: SimpleNamespace(stop=lambda: None),
    )
    monkeypatch.setattr(overlay_mod, "overlay_available", lambda: True)
    monkeypatch.setattr(overlay_mod, "run_overlay", fake_run_overlay)
    app = app_mod.VoiceTypeApp(
        Config(sounds=False, overlay=True, overlay_opacity=0.6)
    )
    app.run()
    assert captured["opacity"] == 0.6
