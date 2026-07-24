"""Tests for voxtype: config, logic, app state machine, CLI, moonshine.

Parakeet engine tests (model install + capture loop) live in
test_voxtype_engines.py.
"""

from __future__ import annotations

import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from voxtype.config import (
    Config,
    load_config,
    sample_config,
    write_sample_config,
)
from voxtype.hotkey import parse_hotkey
from voxtype.logic import (
    contains_phrase,
    is_exact_phrase,
    normalize_words,
    strip_fillers,
    text_after_wake_word,
)


# -- logic ----------------------------------------------------------------


def test_normalize_words_strips_punctuation_and_case() -> None:
    assert normalize_words("Hello, World!  It's me.") == [
        "hello",
        "world",
        "it's",
        "me",
    ]


def test_contains_phrase_word_boundaries() -> None:
    assert contains_phrase("Claude, open the file", "claude")
    assert contains_phrase("please stop listening now", "stop listening")
    assert not contains_phrase("clauded something", "claude")
    assert not contains_phrase("stop, then keep listening", "stop listening")
    assert not contains_phrase("anything", "")


def test_text_after_wake_word() -> None:
    assert text_after_wake_word("Claude, write hello", "claude") == (
        "write hello"
    )
    assert text_after_wake_word("Hey Claude", "claude") == ""
    assert text_after_wake_word("no wake here", "claude") is None
    assert text_after_wake_word("hey claude bot, go", "claude bot") == "go"


def test_is_exact_phrase() -> None:
    assert is_exact_phrase("Go!", "go")
    assert is_exact_phrase("  Over. ", "over")
    assert not is_exact_phrase("go to the file", "go")
    assert not is_exact_phrase("", "go")
    assert not is_exact_phrase("go", "")


def test_strip_fillers() -> None:
    assert strip_fillers("Um, so this is, uh, the plan.") == (
        "so this is, the plan."
    )
    assert strip_fillers("Take the umbrella") == "Take the umbrella"
    assert strip_fillers("Um. Uh.") == ""
    assert strip_fillers("no fillers here") == "no fillers here"
    assert strip_fillers("HMM, okay") == "okay"


def test_strip_fillers_general_punctuation_boundaries() -> None:
    assert strip_fillers("Um! continue") == "continue"
    assert strip_fillers("uh?") == ""
    assert strip_fillers("um; next item") == "next item"
    assert strip_fillers("erm: fine") == "fine"
    assert strip_fillers("Uh? Um! done") == "done"
    # Fillers embedded inside words stay untouched, punctuation or not.
    assert strip_fillers("umbrella!") == "umbrella!"
    assert strip_fillers("gum? intact") == "gum? intact"
    assert strip_fillers("Take the umbrella") == "Take the umbrella"


def test_strip_fillers_punctuation_delimited_forms() -> None:
    """Fillers wrapped in punctuation runs are still standalone.

    Ellipses, parentheses, and quotes around a filler must not shield
    it, while the same punctuation around real words changes nothing.
    """
    assert strip_fillers("Um... continue") == "continue"
    assert strip_fillers("Well (um) continue") == "Well continue"
    assert strip_fillers('"um" continue') == "continue"
    assert strip_fillers("'uh' next") == "next"
    assert strip_fillers("so, (um), yes") == "so, yes"
    assert strip_fillers("Hmm... (uh) 'um'!") == ""
    # Embedded words survive even when punctuated or hyphenated.
    assert strip_fillers("(umbrella) stays") == "(umbrella) stays"
    assert strip_fillers('"gum" stays') == '"gum" stays'
    assert strip_fillers("um-like pauses stay") == "um-like pauses stay"


# -- hotkey parsing -------------------------------------------------------


def test_parse_hotkey() -> None:
    assert parse_hotkey("<ctrl>+;") == (frozenset({"ctrl"}), ";")
    assert parse_hotkey("<ctrl>+<alt>+d") == (
        frozenset({"ctrl", "alt"}),
        "d",
    )
    assert parse_hotkey("<cmd>+<shift>+<f5>") == (
        frozenset({"cmd", "shift"}),
        "<f5>",
    )
    assert parse_hotkey("<option>+x") == (frozenset({"alt"}), "x")


@pytest.mark.parametrize(
    "bad", ["<ctrl>", "<ctrl>+a+b", "<ctrl>+ab", "<f5>+a+<f6>", ""]
)
def test_parse_hotkey_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_hotkey(bad)


@pytest.mark.parametrize(
    "bad", ["<f21>", "<bogus>", "<ctrl>+<f24>", "<cmd>+<media_play>"]
)
def test_parse_hotkey_rejects_unsupported_named_keys(bad: str) -> None:
    """Unknown named keys fail at parse time with a clear error, not
    later by silently degrading to non-suppressing (leaky) hotkeys."""
    with pytest.raises(ValueError, match="named key"):
        parse_hotkey(bad)


def test_every_named_key_has_a_mac_virtual_keycode() -> None:
    """Every documented named key must be suppressible on macOS: a
    gap would make an advertised chord leak into the focused app."""
    from voxtype.hotkey import (
        _NAMED_KEYS,
        _NAMED_VKS,
        _resolve_vk,
    )

    assert set(_NAMED_VKS) == set(_NAMED_KEYS)
    # Spot-check a few Carbon kVK_* values (layout-independent).
    assert _resolve_vk(None, "<f5>") == 96
    assert _resolve_vk(None, "<up>") == 126
    assert _resolve_vk(None, "<esc>") == 53


# -- config ---------------------------------------------------------------


def test_default_config_is_valid() -> None:
    Config().validate()


def test_load_config_missing_default_uses_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate from the developer's real ~/.config/voxtype/config.toml:
    # the default path must be a missing file for this test to mean
    # "missing config falls back to defaults".
    import voxtype.config as config_mod

    monkeypatch.setattr(
        config_mod, "DEFAULT_CONFIG_PATH", tmp_path / "absent.toml"
    )
    cfg = load_config(None, overrides={"mode": "wake"})
    assert cfg.mode == "wake"
    assert cfg.model_arch == "medium-streaming"


def test_load_config_explicit_missing_path_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_load_config_roundtrip_and_overrides(tmp_path: Path) -> None:
    path = write_sample_config(tmp_path / "config.toml")
    cfg = load_config(path)
    assert cfg == Config()  # sample documents the defaults

    path.write_text('mode = "vad"\nhotkey = "<ctrl>+<alt>+d"\n')
    cfg = load_config(path, overrides={"hotkey": "<ctrl>+;", "mode": None})
    assert cfg.mode == "vad"  # None override ignored
    assert cfg.hotkey == "<ctrl>+;"  # non-None override wins


def test_load_config_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('modee = "toggle"\n')
    with pytest.raises(ValueError, match="unknown config keys"):
        load_config(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "bogus"),
        ("engine", "whisper"),
        ("model_arch", "large"),
        ("idle_timeout", -1.0),
        ("idle_timeout", float("nan")),
        ("idle_timeout", float("inf")),
        ("idle_timeout", float("-inf")),
    ],
)
def test_validate_rejects_bad_values(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        Config(**{field: value}).validate()


def test_validate_wake_mode_requires_wake_word() -> None:
    with pytest.raises(ValueError, match="wake_word"):
        Config(mode="wake", wake_word="  ").validate()


def test_write_sample_config_refuses_overwrite(tmp_path: Path) -> None:
    path = write_sample_config(tmp_path / "config.toml")
    with pytest.raises(FileExistsError):
        write_sample_config(path)
    write_sample_config(path, force=True)  # --force succeeds
    assert "voxtype configuration" in sample_config()


@pytest.mark.parametrize(
    "field,value",
    [
        ("strip_fillers", "false"),
        ("strip_fillers", 1),
        ("strip_fillers", None),
        ("strip_fillers", []),
        ("trailing_space", "yes"),
        ("sounds", 0),
        ("wake_word", None),
        ("stop_phrase", 3),
        ("hotkey", None),
        ("language", None),
        ("idle_timeout", None),
        ("idle_timeout", True),
        ("idle_timeout", "20"),
        ("submit_phrases", "go"),
        ("submit_phrases", ["go", ""]),
        ("submit_phrases", ["go", 3]),
    ],
)
def test_validate_rejects_bad_types(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        Config(**{field: value}).validate()


def test_validate_huge_integer_idle_timeout() -> None:
    """Regression: ints too large for a float must not crash validation.

    ``math.isfinite(10**1000)`` raises ``OverflowError``; a Python int
    is always finite, so a huge positive int validates cleanly and a
    huge negative one fails with the contractual ``ValueError`` (never
    an ``OverflowError`` traceback).
    """
    Config(idle_timeout=10**1000).validate()
    with pytest.raises(ValueError, match="positive"):
        Config(idle_timeout=-(10**1000)).validate()


# -- app state machine ----------------------------------------------------


class RecordingTypist:
    """Typist stand-in that records instead of injecting keystrokes."""

    def __init__(self) -> None:
        self.typed: list[str] = []
        self.enters = 0

    def type_text(self, text: str) -> None:
        self.typed.append(text)

    def press_enter(self) -> None:
        self.enters += 1


class FakeClock:
    """Controllable monotonic clock (callable drop-in for monotonic)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def app_factory(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Build a VoiceTypeApp with a recording typist and a fake clock."""
    import voxtype.app as app_mod

    def make(**kw):  # noqa: ANN003, ANN202
        kw.setdefault("sounds", False)
        kw.setdefault("overlay", False)  # never launch AppKit in tests
        cfg = Config(**kw)
        cfg.validate()
        monkeypatch.setattr(app_mod, "Typist", RecordingTypist)
        clock = FakeClock()
        monkeypatch.setattr(app_mod.time, "monotonic", clock)
        app = app_mod.VoiceTypeApp(cfg)
        return app, clock

    return make


def _state(app) -> str:  # noqa: ANN001
    return app._state.name


def test_app_wake_word_activates_and_types_remainder(app_factory) -> None:
    app, _ = app_factory(mode="wake")
    assert _state(app) == "PASSIVE"
    app.handle_utterance("no wake word here")
    assert _state(app) == "PASSIVE"
    assert app.typist.typed == []
    app.handle_utterance("Hey Claude, write hello")
    assert _state(app) == "ACTIVE"
    assert app.typist.typed == ["write hello "]


def test_app_paused_ignores_utterances(app_factory) -> None:  # noqa: ANN001
    app, _ = app_factory(mode="toggle")
    assert _state(app) == "PAUSED"
    app.handle_utterance("hello there")
    assert app.typist.typed == []
    assert app.typist.enters == 0


def test_app_empty_utterance_is_ignored(app_factory) -> None:  # noqa: ANN001
    app, _ = app_factory(mode="vad")
    app.handle_utterance("   ")
    assert app.typist.typed == []


def test_app_toggle_hotkey_flips_state(app_factory) -> None:  # noqa: ANN001
    app, _ = app_factory(mode="toggle")
    app.toggle()
    assert _state(app) == "ACTIVE"
    app.handle_utterance("hello world")
    assert app.typist.typed == ["hello world "]
    app._last_toggle = 0.0  # bypass debounce: a later, real press
    app.toggle()
    assert _state(app) == "PAUSED"


def test_app_concurrent_toggles_never_lose_a_toggle(app_factory) -> None:
    """Two simultaneous hotkey toggles commit exactly ONE transition.

    Contract (updated for debounce): macro keys can re-fire the chord
    for one intended press, so near-simultaneous toggles must collapse
    into a single committed transition — never zero (the old lost-
    toggle bug: state read and inverse committed under separate lock
    acquisitions) and never two (double-flip back to the start). Each
    round resets the debounce clock to simulate distinct real presses.
    """
    app, _ = app_factory(mode="toggle")
    assert _state(app) == "PAUSED"
    rounds = 25
    expected = "PAUSED"
    for round_no in range(1, rounds + 1):
        app._last_toggle = 0.0  # new intended press for this round
        barrier = threading.Barrier(2)

        def worker() -> None:
            barrier.wait()
            app.toggle()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        assert not any(t.is_alive() for t in threads)
        expected = "ACTIVE" if expected == "PAUSED" else "PAUSED"
        assert _state(app) == expected
        assert app._state_version == round_no


def test_app_submit_phrase_beats_stop_phrase(app_factory) -> None:  # noqa: ANN001
    app, _ = app_factory(
        mode="vad",
        submit_phrases=["stop listening"],
        stop_phrase="stop listening",
    )
    # Exact submit phrase wins: Enter, still ACTIVE, nothing typed.
    app.handle_utterance("Stop listening.")
    assert app.typist.enters == 1
    assert _state(app) == "ACTIVE"
    assert app.typist.typed == []
    # Mid-sentence it is only a stop-phrase containment: deactivate.
    app.handle_utterance("please stop listening now")
    assert app.typist.enters == 1
    assert _state(app) == "PAUSED"
    assert app.typist.typed == []


def test_app_stop_phrase_containment_deactivates(app_factory) -> None:  # noqa: ANN001
    app, _ = app_factory(mode="vad")
    app.handle_utterance("Okay, stop listening now.")
    assert _state(app) == "PAUSED"
    assert app.typist.typed == []


def test_app_strips_fillers_only_from_typed_text(app_factory) -> None:  # noqa: ANN001
    app, _ = app_factory(mode="vad", strip_fillers=True)
    app.handle_utterance("Um, hello there")
    assert app.typist.typed == ["hello there "]
    app.handle_utterance("Um. Uh.")  # all fillers: nothing typed
    assert app.typist.typed == ["hello there "]


def test_app_strip_fillers_disabled_types_verbatim(app_factory) -> None:  # noqa: ANN001
    app, _ = app_factory(mode="vad", strip_fillers=False)
    app.handle_utterance("Um, hi")
    assert app.typist.typed == ["Um, hi "]


def test_app_filler_stripping_cannot_create_submit_command(
    app_factory,
) -> None:  # noqa: ANN001
    """Fillers are removed at typing time only, never before command
    matching: "um go" is not the submit phrase "go" and must be typed
    (with the filler stripped), not submitted."""
    app, _ = app_factory(
        mode="vad", strip_fillers=True, submit_phrases=["go"]
    )
    app.handle_utterance("um go")
    assert app.typist.enters == 0
    assert app.typist.typed == ["go "]
    assert _state(app) == "ACTIVE"


def test_app_filler_does_not_bridge_stop_phrase(app_factory) -> None:  # noqa: ANN001
    """"stop um listening" must not match stop phrase "stop listening":
    command matching sees the raw transcript; only the typed text has
    the filler removed."""
    app, _ = app_factory(mode="vad", strip_fillers=True)
    app.handle_utterance("stop um listening")
    assert _state(app) == "ACTIVE"  # no deactivation
    assert app.typist.typed == ["stop listening "]


def test_app_filler_does_not_bridge_wake_word(app_factory) -> None:  # noqa: ANN001
    app, _ = app_factory(
        mode="wake", strip_fillers=True, wake_word="hey claude"
    )
    app.handle_utterance("hey um claude write this")
    assert _state(app) == "PASSIVE"  # raw text lacks the wake phrase
    assert app.typist.typed == []


def test_app_wake_idle_timeout_deferred_by_note_activity(app_factory) -> None:
    app, clock = app_factory(mode="wake", idle_timeout=20.0)
    app.handle_utterance("claude")  # wake word alone: activate
    assert _state(app) == "ACTIVE"
    clock.advance(19.0)
    app.note_activity()  # in-progress speech defers the timeout
    clock.advance(19.0)
    app._check_idle_timeout()
    assert _state(app) == "ACTIVE"
    clock.advance(2.0)
    app._check_idle_timeout()
    assert _state(app) == "PASSIVE"
    assert app.typist.typed == []


def test_app_idle_timeout_only_in_wake_mode(app_factory) -> None:  # noqa: ANN001
    app, clock = app_factory(mode="vad", idle_timeout=20.0)
    assert _state(app) == "ACTIVE"
    clock.advance(1000.0)
    app._check_idle_timeout()
    assert _state(app) == "ACTIVE"


def test_app_utterance_refreshes_idle_timer(app_factory) -> None:  # noqa: ANN001
    app, clock = app_factory(mode="wake", idle_timeout=20.0)
    app.handle_utterance("claude start")
    clock.advance(15.0)
    app.handle_utterance("more dictation")
    clock.advance(15.0)
    app._check_idle_timeout()
    assert _state(app) == "ACTIVE"


def test_app_ignores_non_string_transcripts(app_factory) -> None:  # noqa: ANN001
    app, _ = app_factory(mode="vad")
    for bad in (None, 123, 4.5, b"bytes", ["list"], {"text": "hi"}):
        app.handle_utterance(bad)  # must not raise
    assert app.typist.typed == []
    assert app.typist.enters == 0
    assert _state(app) == "ACTIVE"


def test_app_concurrent_toggle_off_commits_utterance_exactly_once(
    app_factory, monkeypatch
) -> None:  # noqa: ANN001
    """A toggle-off landing between snapshot and injection still commits.

    A worker thread handles an utterance but is paused (deterministically,
    via a patched strip_fillers) between the state inspection and the
    injection; the main thread then toggles the app off. That toggle-off
    arms the one-shot grace handoff for exactly this in-flight utterance,
    so it must be typed (toggle-off means "commit what I said") — once,
    consuming the handoff so later stray speech cannot ride it.
    """
    import voxtype.app as app_mod

    app, _ = app_factory(mode="vad")
    reached = threading.Event()
    proceed = threading.Event()
    orig = app_mod.strip_fillers

    def pausing_strip(text: str) -> str:
        reached.set()
        assert proceed.wait(timeout=5.0)
        return orig(text)

    monkeypatch.setattr(app_mod, "strip_fillers", pausing_strip)
    worker = threading.Thread(
        target=lambda: app.handle_utterance("hello world")
    )
    worker.start()
    assert reached.wait(timeout=5.0)
    app.toggle()  # hotkey thread deactivates mid-utterance
    proceed.set()
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert _state(app) == "PAUSED"
    assert app.typist.typed == ["hello world "]
    # the handoff was consumed: speech begun after the toggle is dropped
    monkeypatch.setattr(app_mod, "strip_fillers", orig)
    app.handle_utterance("later stray speech")
    assert app.typist.typed == ["hello world "]


def test_app_concurrent_toggle_off_still_submits_in_flight_enter(
    app_factory, monkeypatch
) -> None:  # noqa: ANN001
    """A submit phrase losing the injection race to a toggle-off is the
    in-flight utterance that toggle's grace authorizes: Enter fires."""
    import voxtype.app as app_mod

    app, _ = app_factory(mode="vad")
    orig = app_mod.is_exact_phrase

    def racing(text: str, phrase: str) -> bool:
        result = orig(text, phrase)
        if result:
            # Deactivate between the submit decision and the Enter.
            app.toggle()
        return result

    monkeypatch.setattr(app_mod, "is_exact_phrase", racing)
    app.handle_utterance("go")
    assert app.typist.enters == 1
    assert _state(app) == "PAUSED"
    # one-shot: the handoff is spent
    monkeypatch.setattr(app_mod, "is_exact_phrase", orig)
    app.handle_utterance("anything else")
    assert app.typist.typed == []


def test_app_wake_remainder_rides_handoff_when_toggled_off_meanwhile(
    app_factory, monkeypatch
) -> None:  # noqa: ANN001
    """The wake remainder losing its injection race to a toggle-off is
    committed via that toggle's armed grace handoff, exactly once."""
    import voxtype.app as app_mod

    app, _ = app_factory(mode="wake")

    def racing_strip(text: str) -> str:
        app.toggle()  # deactivate right after wake-word activation
        return text

    monkeypatch.setattr(app_mod, "strip_fillers", racing_strip)
    app.handle_utterance("claude write hello")
    assert app.typist.typed == ["write hello "]
    assert _state(app) == "PASSIVE"


def test_app_idle_timeout_effects_suppressed_by_concurrent_toggle(
    app_factory, monkeypatch
) -> None:  # noqa: ANN001
    """A toggle landing between the idle PASSIVE commit and its
    sound/status must suppress the stale idle effects: the last
    reported status reflects the actual (ACTIVE) state."""
    import voxtype.app as app_mod

    app, clock = app_factory(mode="wake", idle_timeout=20.0)
    app.handle_utterance("claude")  # wake word alone: activate
    assert _state(app) == "ACTIVE"
    statuses: list[str] = []
    app._status = statuses.append  # instance shadow of the staticmethod
    clock.advance(21.0)

    reached = threading.Event()
    proceed = threading.Event()
    orig = app_mod.VoiceTypeApp._emit_transition_effects
    pause_first = {"on": True}

    def pausing(self, version, old, new, pre_msg=None):  # noqa: ANN001, ANN202
        if pause_first["on"]:
            pause_first["on"] = False
            reached.set()
            assert proceed.wait(timeout=5.0)
        orig(self, version, old, new, pre_msg)

    monkeypatch.setattr(
        app_mod.VoiceTypeApp, "_emit_transition_effects", pausing
    )
    worker = threading.Thread(target=app._check_idle_timeout)
    worker.start()
    assert reached.wait(timeout=5.0)  # PASSIVE committed, effects pending
    app.toggle()  # hotkey reactivates before the idle effects run
    proceed.set()
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert _state(app) == "ACTIVE"
    # The superseded idle transition emitted nothing.
    assert not any("idle for" in s for s in statuses)
    assert statuses[-1] == app_mod.State.ACTIVE.value


def test_app_idle_timeout_effects_emitted_when_uncontended(
    app_factory,
) -> None:  # noqa: ANN001
    app, clock = app_factory(mode="wake", idle_timeout=20.0)
    app.handle_utterance("claude")
    statuses: list[str] = []
    app._status = statuses.append
    clock.advance(21.0)
    app._check_idle_timeout()
    assert _state(app) == "PASSIVE"
    assert any("idle for 20" in s for s in statuses)
    assert statuses[-1] == "waiting for wake word"


# -- app lifecycle --------------------------------------------------------


class FakeHotkeys:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeEngine:
    def __init__(
        self,
        fatal: str | None = None,
        start_exc: Exception | None = None,
        stop_exc: Exception | None = None,
    ) -> None:
        self.fatal_error: str | None = None
        self._fatal = fatal
        self._start_exc = start_exc
        self._stop_exc = stop_exc
        self.started = False
        self.stop_called = False

    def start(self, on_utterance, on_activity) -> None:  # noqa: ANN001
        if self._start_exc is not None:
            raise self._start_exc
        self.started = True
        self.fatal_error = self._fatal

    def stop(self) -> None:
        self.stop_called = True
        if self._stop_exc is not None:
            raise self._stop_exc


def _lifecycle_app(monkeypatch, engine, sleep=None):  # noqa: ANN001, ANN202
    import voxtype.app as app_mod
    import voxtype.engines as engines_mod

    monkeypatch.setattr(app_mod, "Typist", RecordingTypist)
    monkeypatch.setattr(
        engines_mod, "create_engine", lambda cfg, status: engine
    )
    hotkeys = FakeHotkeys()
    monkeypatch.setattr(
        app_mod.VoiceTypeApp,
        "_start_hotkey_listener",
        lambda self: hotkeys,
    )
    monkeypatch.setattr(
        app_mod.time, "sleep", sleep or (lambda s: None)
    )
    # overlay=False: tests must never enter the AppKit run loop
    app = app_mod.VoiceTypeApp(Config(sounds=False, overlay=False))
    return app, hotkeys


def test_app_run_reports_fatal_engine_error(monkeypatch) -> None:  # noqa: ANN001
    engine = FakeEngine(fatal="mic exploded")
    app, hotkeys = _lifecycle_app(monkeypatch, engine)
    assert app.run() == 1
    assert engine.stop_called
    assert hotkeys.stopped


def test_app_run_cleans_up_when_engine_start_fails(monkeypatch) -> None:  # noqa: ANN001
    engine = FakeEngine(start_exc=ImportError("sherpa missing"))
    app, hotkeys = _lifecycle_app(monkeypatch, engine)
    with pytest.raises(ImportError):
        app.run()
    assert engine.stop_called
    assert hotkeys.stopped


def test_app_run_returns_1_on_engine_start_runtime_error(monkeypatch) -> None:
    """Non-ImportError start failures become exit code 1, not tracebacks."""
    engine = FakeEngine(start_exc=RuntimeError("mic device init failed"))
    app, hotkeys = _lifecycle_app(monkeypatch, engine)
    assert app.run() == 1
    assert engine.stop_called
    assert hotkeys.stopped


def test_app_run_returns_1_when_engine_construction_fails(
    monkeypatch, capsys
) -> None:  # noqa: ANN001
    import voxtype.app as app_mod
    import voxtype.engines as engines_mod

    monkeypatch.setattr(app_mod, "Typist", RecordingTypist)

    def boom(cfg, status):  # noqa: ANN001, ANN202
        raise RuntimeError("model download failed")

    monkeypatch.setattr(engines_mod, "create_engine", boom)
    app = app_mod.VoiceTypeApp(Config(sounds=False))
    assert app.run() == 1
    assert "model download failed" in capsys.readouterr().err


def test_app_run_stops_hotkeys_even_if_engine_stop_raises(monkeypatch) -> None:
    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    engine = FakeEngine(stop_exc=RuntimeError("stuck"))
    app, hotkeys = _lifecycle_app(monkeypatch, engine, sleep=interrupt)
    assert app.run() == 0
    assert engine.stop_called
    assert hotkeys.stopped


def test_overlay_path_starts_hotkeys_from_inside_run_loop(monkeypatch) -> None:
    """With the overlay, the hotkey tap must be installed from INSIDE the
    AppKit loop (via on_ready) — after engine.start and after the loop is
    up — not before, which raced the loop and killed the tap."""
    import voxtype.app as app_mod
    import voxtype.engines as engines_mod
    import voxtype.overlay as overlay_mod

    engine = FakeEngine()
    hotkeys = FakeHotkeys()
    order: list[str] = []

    monkeypatch.setattr(app_mod, "Typist", RecordingTypist)
    monkeypatch.setattr(
        engines_mod, "create_engine", lambda cfg, status: engine
    )

    def fake_start_listener(self):  # noqa: ANN001, ANN202
        order.append("hotkeys")
        return hotkeys

    monkeypatch.setattr(
        app_mod.VoiceTypeApp, "_start_hotkey_listener", fake_start_listener
    )
    monkeypatch.setattr(overlay_mod, "overlay_available", lambda: True)

    def fake_run_overlay(  # noqa: ANN001, ANN202
        sample, tick, stopped, on_ready=None, flex=1.0, speed=1.0
    ):
        order.append("overlay_loop")
        on_ready()  # the one-shot timer firing inside the live loop
        return None

    monkeypatch.setattr(overlay_mod, "run_overlay", fake_run_overlay)

    app = app_mod.VoiceTypeApp(Config(sounds=False, overlay=True))
    assert app.run() == 0
    assert engine.started
    # The listener started only after the overlay loop was up.
    assert order == ["overlay_loop", "hotkeys"]
    assert hotkeys.stopped  # and it is still cleaned up


# -- CLI ------------------------------------------------------------------


def _run_cli(monkeypatch, tmp_path, app_cls, extra_args=()):  # noqa: ANN001, ANN202
    import voxtype.app as app_mod
    from voxtype.cli import main

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("")  # defaults only, isolated from ~/.config
    monkeypatch.setattr(app_mod, "VoiceTypeApp", app_cls)
    monkeypatch.setattr(
        sys,
        "argv",
        ["voxtype", "--config", str(cfg_path), *extra_args],
    )
    return main()


def test_cli_import_error_at_construction(monkeypatch, tmp_path, capsys) -> None:
    class CtorFails:
        def __init__(self, cfg: Config) -> None:
            raise ImportError("pynput missing")

    assert _run_cli(monkeypatch, tmp_path, CtorFails) == 1
    err = capsys.readouterr().err
    assert "pynput missing" in err
    assert "uv tool install" in err


def test_cli_import_error_at_run(monkeypatch, tmp_path, capsys) -> None:  # noqa: ANN001
    class RunFails:
        def __init__(self, cfg: Config) -> None:
            pass

        def run(self) -> int:
            raise ImportError("moonshine missing")

    assert _run_cli(monkeypatch, tmp_path, RunFails) == 1
    err = capsys.readouterr().err
    assert "moonshine missing" in err
    assert "uv tool install" in err


def test_cli_engine_flag_reaches_config_and_exit_code(monkeypatch, tmp_path) -> None:
    class Recording:
        last_cfg: Config | None = None
        exit_code = 0

        def __init__(self, cfg: Config) -> None:
            type(self).last_cfg = cfg

        def run(self) -> int:
            return type(self).exit_code

    assert (
        _run_cli(
            monkeypatch, tmp_path, Recording, ["--engine", "parakeet"]
        )
        == 0
    )
    assert Recording.last_cfg is not None
    assert Recording.last_cfg.engine == "parakeet"

    Recording.exit_code = 1  # engine failure propagates to the shell
    assert _run_cli(monkeypatch, tmp_path, Recording) == 1


# -- moonshine engine listener --------------------------------------------


class FakeMicTranscriber:
    instances: list["FakeMicTranscriber"] = []

    def __init__(self, model_path, model_arch) -> None:  # noqa: ANN001
        self.listeners: list = []
        self.started = self.stopped = self.closed = False
        self.stop_exc: Exception | None = None
        FakeMicTranscriber.instances.append(self)

    def add_listener(self, listener) -> None:  # noqa: ANN001
        self.listeners.append(listener)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True
        if self.stop_exc is not None:
            raise self.stop_exc

    def close(self) -> None:
        self.closed = True


def _start_moonshine(  # noqa: ANN202
    monkeypatch, on_utterance, on_activity, status=None  # noqa: ANN001
):
    mv = types.ModuleType("moonshine_voice")
    mv.get_model_for_language = (
        lambda wanted_language, wanted_model_arch: ("/fake", "arch")
    )
    mic = types.ModuleType("moonshine_voice.mic_transcriber")
    mic.MicTranscriber = FakeMicTranscriber
    api = types.ModuleType("moonshine_voice.moonshine_api")
    api.string_to_model_arch = lambda s: s

    class TranscriptEventListener:
        pass

    tr = types.ModuleType("moonshine_voice.transcriber")
    tr.TranscriptEventListener = TranscriptEventListener
    monkeypatch.setitem(sys.modules, "moonshine_voice", mv)
    monkeypatch.setitem(
        sys.modules, "moonshine_voice.mic_transcriber", mic
    )
    monkeypatch.setitem(
        sys.modules, "moonshine_voice.moonshine_api", api
    )
    monkeypatch.setitem(sys.modules, "moonshine_voice.transcriber", tr)

    from voxtype.engines import MoonshineEngine

    FakeMicTranscriber.instances.clear()
    statuses: list[str] = []
    eng = MoonshineEngine(Config(), status or statuses.append)
    eng.start(on_utterance, on_activity)
    transcriber = FakeMicTranscriber.instances[-1]
    return eng, transcriber, transcriber.listeners[0], statuses


def test_moonshine_listener_tolerates_malformed_events(monkeypatch) -> None:
    utterances: list[str] = []
    _, _, listener, _ = _start_moonshine(
        monkeypatch, utterances.append, lambda: None
    )
    malformed = [
        None,
        SimpleNamespace(),
        SimpleNamespace(line=None),
        SimpleNamespace(line=SimpleNamespace()),
        SimpleNamespace(line=SimpleNamespace(text=None)),
        SimpleNamespace(line=SimpleNamespace(text=123)),
        SimpleNamespace(line=SimpleNamespace(text="   ")),
    ]
    for event in malformed:
        listener.on_line_completed(event)
    assert utterances == []
    listener.on_line_completed(
        SimpleNamespace(line=SimpleNamespace(text="  hello "))
    )
    assert utterances == ["hello"]


def test_moonshine_listener_survives_raising_callbacks(monkeypatch) -> None:
    def bad_utterance(text: str) -> None:
        raise RuntimeError("typist exploded")

    def bad_activity() -> None:
        raise ValueError("activity exploded")

    _, _, listener, statuses = _start_moonshine(
        monkeypatch, bad_utterance, bad_activity
    )
    listener.on_line_completed(
        SimpleNamespace(line=SimpleNamespace(text="hello"))
    )
    listener.on_line_text_changed(SimpleNamespace())
    assert any("on_utterance callback error" in s for s in statuses)
    assert any("on_activity callback error" in s for s in statuses)


def test_moonshine_listener_survives_raising_status(monkeypatch) -> None:
    """Even the status reporter raising must not escape the listener."""
    armed = {"on": False}

    def bad_status(msg: str) -> None:
        if armed["on"]:
            raise OSError("stderr closed")

    def bad_utterance(text: str) -> None:
        raise RuntimeError("typist exploded")

    def bad_activity() -> None:
        raise ValueError("activity exploded")

    _, _, listener, _ = _start_moonshine(
        monkeypatch, bad_utterance, bad_activity, status=bad_status
    )
    armed["on"] = True  # raise only for listener-thread reporting
    # Every callback (including status) raises; nothing may escape.
    listener.on_line_completed(
        SimpleNamespace(line=SimpleNamespace(text="hello"))
    )
    listener.on_line_text_changed(SimpleNamespace())
    listener.on_error(SimpleNamespace())


def test_moonshine_stop_is_idempotent_and_always_closes(monkeypatch) -> None:
    eng, transcriber, _, _ = _start_moonshine(
        monkeypatch, lambda t: None, lambda: None
    )
    eng.stop()
    assert transcriber.stopped and transcriber.closed
    eng.stop()  # second stop is a no-op

    eng2, transcriber2, _, _ = _start_moonshine(
        monkeypatch, lambda t: None, lambda: None
    )
    transcriber2.stop_exc = RuntimeError("stuck")
    with pytest.raises(RuntimeError):
        eng2.stop()
    assert transcriber2.closed  # close still ran
    assert eng2._transcriber is None


# -- toggle-off grace window (commit in-flight speech) --------------------


class _RecordingEngine:
    def __init__(self) -> None:
        self.flushes = 0
        self.resets = 0

    def request_flush(self) -> None:
        self.flushes += 1

    def request_reset(self) -> None:
        self.resets += 1


class _CollectingTypist:
    def __init__(self) -> None:
        self.typed: list[str] = []
        self.enters = 0

    def type_text(self, text: str) -> None:
        self.typed.append(text)

    def press_enter(self) -> None:
        self.enters += 1


def _grace_app():
    pytest.importorskip("pynput")
    from voxtype.app import VoiceTypeApp

    app = VoiceTypeApp(Config(mode="vad", sounds=False))
    app.typist = _CollectingTypist()
    app._engine = _RecordingEngine()
    return app


def test_toggle_off_flushes_and_grace_commits_in_flight() -> None:
    app = _grace_app()
    app.toggle()  # ACTIVE -> PAUSED
    assert app._engine.flushes == 1
    # utterance arrives moments later (VAD closed it after the toggle)
    app.handle_utterance("this was in flight")
    assert app.typist.typed == ["this was in flight "]


def test_toggle_on_resets_engine_audio() -> None:
    app = _grace_app()
    app.toggle()  # off
    app._last_toggle = 0.0  # bypass debounce for the test
    app.toggle()  # back on -> reset
    assert app._engine.resets == 1


def test_utterance_after_grace_expires_is_dropped() -> None:
    app = _grace_app()
    app.toggle()  # ACTIVE -> PAUSED, grace starts
    app._grace_until = 0.0  # force-expire the grace window
    app.handle_utterance("too late")
    assert app.typist.typed == []


def test_submit_phrase_in_grace_presses_enter() -> None:
    app = _grace_app()
    app.handle_utterance("send this text")
    app.toggle()  # off; "go" was in flight
    app.handle_utterance("go")
    assert app.typist.enters == 1


def test_toggle_debounce_ignores_rapid_second_press() -> None:
    from voxtype.app import State

    app = _grace_app()
    app.toggle()  # ACTIVE -> PAUSED
    app.toggle()  # re-fire within debounce window: ignored
    assert app._state is State.PAUSED


def test_toggle_ignored_until_engine_ready() -> None:
    """A toggle during model load is a no-op: it must not flip the
    state machine into ACTIVE against an engine that cannot record
    yet (the stuck-overlay glitch)."""
    from voxtype.app import State

    app = _grace_app()
    app._set_state(State.PAUSED)  # start from the off state
    app._ready.clear()  # simulate the engine still loading
    app.toggle()
    assert app._state is State.PAUSED  # press swallowed
    assert app._engine.resets == 0  # engine was never asked to arm
    app._ready.set()  # engine finishes loading
    app.toggle()
    assert app._state is State.ACTIVE  # now it works


def test_wake_word_alias_matches() -> None:
    pytest.importorskip("pynput")
    from voxtype.app import State, VoiceTypeApp

    app = VoiceTypeApp(Config(
        mode="wake", sounds=False,
        wake_word_aliases=["claud", "clawed"],
    ))
    app.typist = _CollectingTypist()
    app._engine = _RecordingEngine()
    app.handle_utterance("Clawed, type this out")
    assert app._state is State.ACTIVE
    assert app.typist.typed == ["type this out "]


# -- hold segmentation ----------------------------------------------------


class _HoldEngine(_RecordingEngine):
    def __init__(self) -> None:
        super().__init__()
        self.hold_starts = 0
        self.hold_stops = 0

    def request_hold_start(self) -> None:
        self.hold_starts += 1

    def request_hold_stop(self) -> None:
        self.hold_stops += 1


def test_hold_config_requires_parakeet_toggle() -> None:
    Config(mode="toggle", engine="parakeet", segmentation="hold").validate()
    with pytest.raises(ValueError, match="hold"):
        Config(mode="wake", engine="parakeet", segmentation="hold").validate()
    with pytest.raises(ValueError, match="hold"):
        Config(
            mode="toggle", engine="moonshine", segmentation="hold"
        ).validate()


def test_parakeet_model_validation() -> None:
    Config(parakeet_model="v2-fp16").validate()
    with pytest.raises(ValueError, match="parakeet_model"):
        Config(parakeet_model="v9-int4").validate()


def test_hold_toggle_uses_hold_requests_and_grace() -> None:
    pytest.importorskip("pynput")
    from voxtype.app import VoiceTypeApp

    app = VoiceTypeApp(Config(
        mode="toggle", engine="parakeet", segmentation="hold",
        sounds=False,
    ))
    app.typist = _CollectingTypist()
    app._engine = _HoldEngine()
    app.toggle()  # on -> hold_start
    assert app._engine.hold_starts == 1
    app._last_toggle = 0.0
    app.toggle()  # off -> hold_stop + long grace
    assert app._engine.hold_stops == 1
    # the decoded whole-take utterance arrives during the grace window
    app.handle_utterance("the entire dictated take as one utterance")
    assert app.typist.typed == [
        "the entire dictated take as one utterance "
    ]


def test_parse_hotkey_bracketless() -> None:
    assert parse_hotkey("ctrl+;") == (frozenset({"ctrl"}), ";")
    assert parse_hotkey("cmd+shift+v") == (
        frozenset({"cmd", "shift"}),
        "v",
    )
    assert parse_hotkey("ctrl+f5") == (frozenset({"ctrl"}), "<f5>")


def test_config_overlay_and_threads() -> None:
    Config(overlay=False, parakeet_threads=8).validate()
    with pytest.raises(ValueError, match="parakeet_threads"):
        Config(parakeet_threads=0).validate()
    with pytest.raises(ValueError, match="parakeet_threads"):
        Config(parakeet_threads=True).validate()


def test_mlx_engine_config() -> None:
    Config(
        mode="toggle", engine="parakeet-mlx", segmentation="hold"
    ).validate()
    Config(mode="wake", engine="parakeet-mlx").validate()
    with pytest.raises(ValueError, match="engine"):
        Config(engine="whisper-mlx").validate()


def test_collapse_repeats() -> None:
    from voxtype.logic import collapse_repeats

    assert collapse_repeats("I I I think this is good") == (
        "I think this is good"
    )
    assert collapse_repeats("no, no, no, fine") == "no, fine"
    assert collapse_repeats("blah blah blah done") == "blah done"
    assert collapse_repeats("very very good") == "very very good"
    assert collapse_repeats("that that happened") == "that that happened"
    assert collapse_repeats("I i i think") == "I think"
    assert collapse_repeats("it's it's it's fine") == "it's fine"
    assert collapse_repeats("clean text stays clean") == (
        "clean text stays clean"
    )


def test_fillers_then_repeats_compose() -> None:
    from voxtype.logic import (
        collapse_repeats,
        strip_fillers,
    )

    assert collapse_repeats(strip_fillers("I um I uh I think")) == (
        "I think"
    )


# -- sounds / clipboard / paste-again -------------------------------------


def test_config_new_fields_validate() -> None:
    Config(
        sound_start="Hero",
        sound_stop="/tmp/x.aiff",
        copy_to_clipboard=True,
        paste_hotkey="<cmd>+<ctrl>+v",
    ).validate()
    with pytest.raises(ValueError, match="sound_start"):
        Config(sound_start=None).validate()
    with pytest.raises(ValueError, match="copy_to_clipboard"):
        Config(copy_to_clipboard="yes").validate()


def test_parse_paste_hotkey_chord() -> None:
    assert parse_hotkey("<cmd>+<ctrl>+v") == (
        frozenset({"cmd", "ctrl"}),
        "v",
    )


def test_session_buffer_and_paste_last() -> None:
    pytest.importorskip("pynput")
    from voxtype.app import VoiceTypeApp

    app = VoiceTypeApp(Config(mode="vad", sounds=False, overlay=False))
    app.typist = _CollectingTypist()
    app._engine = _RecordingEngine()
    app.handle_utterance("first part")
    app.handle_utterance("second part")
    assert app.typist.typed == ["first part ", "second part "]
    # rescue: re-type the whole session at the (new) cursor position
    app.paste_last()
    assert app.typist.typed[-1] == "first part second part "
    # a new session resets the buffer
    app.toggle()  # off
    app._last_toggle = 0.0
    app._grace_until = 0.0
    app.toggle()  # on -> new session
    app.handle_utterance("fresh")
    app.paste_last()
    assert app.typist.typed[-1] == "fresh "


def test_paste_last_empty_session() -> None:
    pytest.importorskip("pynput")
    from voxtype.app import VoiceTypeApp

    app = VoiceTypeApp(Config(mode="toggle", sounds=False, overlay=False))
    app.typist = _CollectingTypist()
    app.paste_last()
    assert app.typist.typed == []


# -- cancel (escape) ------------------------------------------------------


def test_cancel_discards_recording() -> None:
    pytest.importorskip("pynput")
    from voxtype.app import State, VoiceTypeApp

    app = VoiceTypeApp(Config(
        mode="toggle", engine="parakeet", segmentation="hold",
        sounds=False, overlay=False,
    ))
    app.typist = _CollectingTypist()
    app._engine = _HoldEngine()
    app.toggle()  # recording
    app.cancel()
    assert app._state is State.PAUSED
    assert app._engine.resets == 1  # pending audio dropped
    # nothing in flight may type: grace is dead
    app.handle_utterance("late straggler")
    assert app.typist.typed == []


def test_cancel_noop_when_not_recording() -> None:
    pytest.importorskip("pynput")
    from voxtype.app import State, VoiceTypeApp

    app = VoiceTypeApp(Config(mode="toggle", sounds=False, overlay=False))
    app.typist = _CollectingTypist()
    app._engine = _RecordingEngine()
    app.cancel()  # paused: nothing happens
    assert app._state is State.PAUSED
    assert app._engine.resets == 0


def test_parse_esc_hotkey() -> None:
    assert parse_hotkey("<esc>") == (frozenset(), "<esc>")


# -- hold delivery is tied to the stop request, not a clock ---------------


def test_hold_take_delivered_no_matter_how_slow_decode() -> None:
    pytest.importorskip("pynput")
    from voxtype.app import VoiceTypeApp

    app = VoiceTypeApp(Config(
        mode="toggle", engine="parakeet", segmentation="hold",
        sounds=False, overlay=False,
    ))
    app.typist = _CollectingTypist()
    app._engine = _HoldEngine()
    app.toggle()  # record
    app._last_toggle = 0.0
    app.toggle()  # stop -> decode begins
    # simulate a decode far longer than any wall-clock grace
    app._grace_until = 0.0
    app.handle_utterance("a very long take finally decoded")
    assert app.typist.typed == ["a very long take finally decoded "]
    # delivered exactly once: later stray utterances are NOT committed
    app.handle_utterance("stray noise")
    assert app.typist.typed == ["a very long take finally decoded "]


def test_two_outstanding_hold_stops_both_deliver() -> None:
    """Off/on/off with two takes still in decode delivers BOTH.

    The hold handoff is a counter, not a bool: the first arriving take
    must not consume the authorization for the second stop request.
    """
    pytest.importorskip("pynput")
    from voxtype.app import VoiceTypeApp

    app = VoiceTypeApp(Config(
        mode="toggle", engine="parakeet", segmentation="hold",
        sounds=False, overlay=False,
    ))
    app.typist = _CollectingTypist()
    app._engine = _HoldEngine()
    app.toggle()               # record take 1
    app._last_toggle = 0.0
    app.toggle()               # stop 1 -> decode begins
    app._last_toggle = 0.0
    app.toggle()               # record take 2
    app._last_toggle = 0.0
    app.toggle()               # stop 2 -> decode begins
    assert app._engine.hold_stops == 2
    app._grace_until = 0.0     # no wall-clock window: counter only
    app.handle_utterance("first take")
    app.handle_utterance("second take")
    assert app.typist.typed == ["first take ", "second take "]
    # both authorizations consumed: stray noise is NOT typed
    app.handle_utterance("stray noise")
    assert app.typist.typed == ["first take ", "second take "]


def test_cancel_clears_pending_hold_delivery() -> None:
    pytest.importorskip("pynput")
    from voxtype.app import VoiceTypeApp

    app = VoiceTypeApp(Config(
        mode="toggle", engine="parakeet", segmentation="hold",
        sounds=False, overlay=False,
    ))
    app.typist = _CollectingTypist()
    app._engine = _HoldEngine()
    app.toggle()   # record
    app.cancel()   # discard
    app.handle_utterance("should never appear")
    assert app.typist.typed == []


# -- unicode-aware phrase matching ----------------------------------------


def test_normalize_words_unicode_and_edge_apostrophes() -> None:
    # Accented words are kept whole, never ASCII-stripped into "caf".
    assert normalize_words("Café, s'il vous plaît!") == [
        "café", "s'il", "vous", "plaît",
    ]
    # Non-Latin scripts survive normalization (documented languages).
    assert normalize_words("クロード、 これを書いて") == [
        "クロード", "これを書いて",
    ]
    # Apostrophes are kept only INSIDE a word, never at its edges.
    assert normalize_words("'claude' said don't") == [
        "claude", "said", "don't",
    ]
    assert normalize_words("’quoted’") == ["quoted"]


def test_unicode_wake_and_phrase_matching() -> None:
    assert not contains_phrase("café au lait", "caf")
    assert contains_phrase("Über uns", "über")
    assert is_exact_phrase("Envía.", "envía")
    assert text_after_wake_word("クロード これを書いて", "クロード") == (
        "これを書いて"
    )


# -- overlay / mlx_model validation ---------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("overlay", "false"),  # truthy string must not enable the pill
        ("overlay", 1),
        ("overlay", None),
        ("mlx_model", None),
        ("mlx_model", ""),
        ("mlx_model", "   "),
        ("mlx_model", 3),
    ],
)
def test_validate_rejects_bad_overlay_and_mlx_model(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError):
        Config(**{field: value}).validate()


# -- CLI: --config placement and hotkey dependency guard ------------------


def _fake_agents(monkeypatch, present, exit_codes=None):  # noqa: ANN001, ANN202
    """Stub shutil.which + subprocess.run for skill_install.

    ``present`` is the set of agent names on PATH; ``exit_codes`` maps a
    full argv tuple to a return code (default 0). Returns the list of
    argv lists actually run.
    """
    import voxtype.skill_install as si

    exit_codes = exit_codes or {}
    runs: list[list[str]] = []

    monkeypatch.setattr(
        si.shutil, "which", lambda name: name if name in present else None
    )

    def fake_run(argv):  # noqa: ANN001, ANN202
        runs.append(argv)
        code = exit_codes.get(tuple(argv), 0)
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(si.subprocess, "run", fake_run)
    return runs


def test_skill_install_targets_both_agents(monkeypatch, capsys) -> None:
    """With both agents present, each gets marketplace-add + install."""
    from voxtype.skill_install import install_skill

    runs = _fake_agents(monkeypatch, present={"claude", "codex"})
    assert install_skill() == 0
    assert [
        "claude", "plugin", "install", "voxtype@cctools-plugins",
    ] in runs
    assert ["codex", "plugin", "add", "voxtype@cctools-plugins"] in runs
    # marketplace add ran for both, from the repo
    adds = [r for r in runs if "marketplace" in r]
    assert all("pchalasani/claude-code-tools" in r for r in adds)
    assert len(adds) == 2


def test_skill_install_skips_absent_agent(monkeypatch) -> None:
    """Only the installed agent is driven; the missing one is skipped."""
    from voxtype.skill_install import install_skill

    runs = _fake_agents(monkeypatch, present={"claude"})
    assert install_skill() == 0
    assert not any(r[0] == "codex" for r in runs)


def test_skill_install_no_agents_errors(monkeypatch, capsys) -> None:
    """No claude and no codex → exit 1 with guidance, no subprocesses."""
    from voxtype.skill_install import install_skill

    runs = _fake_agents(monkeypatch, present=set())
    assert install_skill() == 1
    assert runs == []
    assert "neither" in capsys.readouterr().out


def test_skill_install_survives_already_added_marketplace(
    monkeypatch,
) -> None:
    """A non-zero marketplace-add (already added) still installs."""
    from voxtype.skill_install import install_skill

    add = ("claude", "plugin", "marketplace", "add",
           "pchalasani/claude-code-tools")
    runs = _fake_agents(
        monkeypatch, present={"claude"}, exit_codes={add: 1}
    )
    assert install_skill() == 0  # install still ran and succeeded
    assert [
        "claude", "plugin", "install", "voxtype@cctools-plugins",
    ] in runs


def test_warn_if_unsupported_platform(monkeypatch, capsys) -> None:
    """Non-macOS launches warn (hotkey won't suppress); macOS is silent."""
    import voxtype.cli as cli_mod

    monkeypatch.setattr(cli_mod.sys, "platform", "linux")
    cli_mod._warn_if_unsupported_platform()
    err = capsys.readouterr().err
    assert "macOS only" in err

    monkeypatch.setattr(cli_mod.sys, "platform", "darwin")
    cli_mod._warn_if_unsupported_platform()
    assert capsys.readouterr().err == ""


def test_cli_init_config_before_subcommand(monkeypatch, tmp_path) -> None:
    """`voxtype --config X init` must write X, not the default path."""
    from voxtype.cli import main

    dest = tmp_path / "wanted.toml"
    monkeypatch.setattr(
        sys, "argv", ["voxtype", "--config", str(dest), "init"]
    )
    assert main() == 0
    assert dest.exists()


def test_cli_init_config_after_subcommand(monkeypatch, tmp_path) -> None:
    from voxtype.cli import main

    dest = tmp_path / "after.toml"
    monkeypatch.setattr(
        sys, "argv", ["voxtype", "init", "--config", str(dest)]
    )
    assert main() == 0
    assert dest.exists()


def test_cli_hotkey_missing_dependency_hint(
    monkeypatch, capsys
) -> None:
    """The lazy pynput import inside record_hotkey() must be guarded."""
    from voxtype.cli import main

    monkeypatch.setitem(sys.modules, "pynput", None)  # import -> error
    monkeypatch.setattr(sys, "argv", ["voxtype", "hotkey"])
    assert main() == 1
    err = capsys.readouterr().err
    assert "uv tool install" in err


# -- moonshine reset drops in-flight (pre-activation) lines ---------------


def test_moonshine_reset_drops_line_in_progress(monkeypatch) -> None:
    utterances: list[str] = []
    eng, _, listener, statuses = _start_moonshine(
        monkeypatch, utterances.append, lambda: None
    )
    # A line begun (partial text) BEFORE toggle-on must not be typed
    # after activation, even though it completes afterwards.
    listener.on_line_text_changed(SimpleNamespace())
    eng.request_reset()
    listener.on_line_completed(
        SimpleNamespace(line=SimpleNamespace(text="stale paused speech"))
    )
    assert utterances == []
    assert any("stale" in s for s in statuses)
    # A line begun after activation is delivered normally.
    listener.on_line_text_changed(SimpleNamespace())
    listener.on_line_completed(
        SimpleNamespace(line=SimpleNamespace(text="fresh dictation"))
    )
    assert utterances == ["fresh dictation"]


# -- cancel ordering and one-shot grace -----------------------------------


def test_cancel_deactivates_before_engine_reset() -> None:
    """The state must be off (version bumped) BEFORE the async engine
    reset is requested, so nothing in flight can type after cancel."""
    pytest.importorskip("pynput")
    from voxtype.app import State, VoiceTypeApp

    app = VoiceTypeApp(Config(mode="vad", sounds=False, overlay=False))
    app.typist = _CollectingTypist()
    states_at_reset: list[State] = []

    class _Engine(_RecordingEngine):
        def request_reset(self) -> None:
            super().request_reset()
            states_at_reset.append(app._state)

    app._engine = _Engine()
    app.cancel()
    assert states_at_reset == [State.PAUSED]


def test_grace_window_is_consumed_by_first_delivery() -> None:
    """Grace authorizes exactly one in-flight utterance; speech begun
    after the toggle-off can never ride the same window."""
    app = _grace_app()
    app.toggle()  # ACTIVE -> PAUSED, grace armed
    app.handle_utterance("the in-flight utterance")
    app.handle_utterance("speech begun after toggle-off")
    assert app.typist.typed == ["the in-flight utterance "]


def test_utterance_losing_injection_race_to_toggle_off_still_types() -> None:
    """An utterance that snapshotted ACTIVE but reaches injection just
    after a toggle-off IS the in-flight delivery that toggle's grace
    window authorizes: it must type, consuming the handoff, instead of
    being dropped while the window stays armed for stray speech."""
    app = _grace_app()
    version = app._state_version  # snapshot, as handle_utterance does
    app.toggle()  # toggle-off commits first: version moves, grace armed
    app._type("in flight", version)  # stale-version injection path
    assert app.typist.typed == ["in flight "]
    # the handoff was consumed: later stray speech cannot ride it
    app.handle_utterance("stray noise")
    assert app.typist.typed == ["in flight "]


def test_stale_injection_without_armed_handoff_stays_dropped() -> None:
    """Losing the injection race to a transition that armed NO handoff
    (cancel) must still drop the text."""
    app = _grace_app()
    version = app._state_version
    app.cancel()  # off with no grace: everything in flight is discarded
    app._type("late straggler", version)
    assert app.typist.typed == []


def test_stop_phrase_in_flight_is_dropped_not_typed() -> None:
    """A trailing in-flight utterance that is exactly the stop phrase
    was a deactivation attempt, not dictation: never type it."""
    app = _grace_app()  # default stop_phrase = "stop listening"
    app.toggle()  # off, grace armed
    app.handle_utterance("Stop listening.")
    assert app.typist.typed == []
    # the grace was still consumed by that delivery
    app.handle_utterance("later speech")
    assert app.typist.typed == []


# -- hotkey bindings degrade independently --------------------------------


def test_invalid_optional_hotkey_keeps_toggle(monkeypatch) -> None:
    import voxtype.app as app_mod
    import voxtype.hotkey as hotkey_mod

    monkeypatch.setattr(app_mod, "Typist", RecordingTypist)
    started: dict = {}

    def fake_start_hotkeys(bindings):  # noqa: ANN001, ANN202
        started["bindings"] = bindings
        return SimpleNamespace(stop=lambda: None)

    monkeypatch.setattr(hotkey_mod, "start_hotkeys", fake_start_hotkeys)
    # Never let the test hit real Quartz/ApplicationServices probes
    # (CGRequestListenEventAccess can open a system privacy prompt).
    monkeypatch.setattr(hotkey_mod, "check_permissions", lambda: [])
    app = app_mod.VoiceTypeApp(Config(
        mode="toggle", sounds=False, overlay=False,
        cancel_hotkey="escape", paste_hotkey="<cmd>+<ctrl>+v",
    ))
    assert app._start_hotkey_listener() is not None
    chords = [b[0] for b in started["bindings"]]
    # The malformed cancel chord is dropped; toggle and paste survive.
    assert chords == ["<ctrl>+;", "<cmd>+<ctrl>+v"]


def test_check_permissions_non_darwin_is_empty(monkeypatch) -> None:
    """Off macOS the preflight is a no-op (and touches no OS APIs)."""
    from voxtype import hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod.sys, "platform", "linux")
    assert hotkey_mod.check_permissions() == []


def test_check_permissions_reports_missing_grants(monkeypatch) -> None:
    """Missing grants produce warnings — via STUBBED macOS APIs only.

    The real CGRequestListenEventAccess can open a blocking system
    privacy prompt, so the test injects fake Quartz/ApplicationServices
    modules instead of ever invoking the genuine preflight.
    """
    from voxtype import hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod.sys, "platform", "darwin")
    requested = {"n": 0}
    fake_quartz = types.SimpleNamespace(
        CGPreflightListenEventAccess=lambda: False,
        CGRequestListenEventAccess=lambda: requested.__setitem__(
            "n", requested["n"] + 1
        ),
    )
    ax_prompts: list[dict] = []
    fake_appserv = types.SimpleNamespace(
        AXIsProcessTrusted=lambda: False,
        AXIsProcessTrustedWithOptions=lambda opts: bool(
            ax_prompts.append(opts)
        ),
        kAXTrustedCheckOptionPrompt="AXTrustedCheckOptionPrompt",
    )
    monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)
    monkeypatch.setitem(
        sys.modules, "ApplicationServices", fake_appserv
    )
    warnings = hotkey_mod.check_permissions()
    assert len(warnings) == 2
    assert "Input Monitoring" in warnings[0]
    assert "Accessibility" in warnings[1]
    assert requested["n"] == 1  # registration was requested
    # the Accessibility prompt/registration was requested too
    assert ax_prompts == [{"AXTrustedCheckOptionPrompt": True}]

    # granted context: no warnings, and no fresh registration request
    fake_quartz.CGPreflightListenEventAccess = lambda: True
    fake_appserv.AXIsProcessTrusted = lambda: True
    assert hotkey_mod.check_permissions() == []
    assert requested["n"] == 1  # unchanged: not re-requested
    assert len(ax_prompts) == 1  # unchanged: not re-prompted


def test_check_permissions_reports_unverifiable_probes(monkeypatch) -> None:
    """Failed or unavailable probes yield 'could not verify' warnings
    instead of silently reporting that all is well."""
    from voxtype import hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod.sys, "platform", "darwin")

    def boom() -> bool:
        raise RuntimeError("kaput")

    fake_appserv = types.SimpleNamespace(AXIsProcessTrusted=boom)
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        types.SimpleNamespace(CGPreflightListenEventAccess=boom),
    )
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake_appserv)
    warnings = hotkey_mod.check_permissions()
    assert len(warnings) == 2
    assert "could not verify Input Monitoring" in warnings[0]
    assert "could not verify Accessibility" in warnings[1]

    # a Quartz build lacking the preflight symbol is loud too, never
    # treated as "granted"
    monkeypatch.setitem(sys.modules, "Quartz", types.SimpleNamespace())
    warnings = hotkey_mod.check_permissions()
    assert "could not verify Input Monitoring" in warnings[0]

    # a failed registration request is reported alongside the missing
    # grant, not suppressed
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        types.SimpleNamespace(
            CGPreflightListenEventAccess=lambda: False,
            CGRequestListenEventAccess=boom,
        ),
    )
    warnings = hotkey_mod.check_permissions()
    assert "Input Monitoring permission MISSING" in warnings[0]
    assert "registration failed" in warnings[1]

    # same for Accessibility: a failed prompt/registration request is
    # reported alongside the missing grant, not suppressed (the stub
    # lacks AXIsProcessTrustedWithOptions, so the request fails)
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        types.SimpleNamespace(
            CGPreflightListenEventAccess=lambda: True
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "ApplicationServices",
        types.SimpleNamespace(AXIsProcessTrusted=lambda: False),
    )
    warnings = hotkey_mod.check_permissions()
    assert "Accessibility permission MISSING" in warnings[0]
    assert "requesting Accessibility registration failed" in warnings[1]


def test_startup_emits_permission_warnings_before_hotkeys(
    monkeypatch,
) -> None:
    """Startup surfaces every preflight warning via _status with the
    documented "WARNING: " prefix, before starting the hotkeys.

    Hermetic: check_permissions/start_hotkeys are replaced and Typist
    is stubbed, so no real pynput/Quartz backend is ever imported.
    """
    import voxtype.app as app_mod
    import voxtype.hotkey as hotkey_mod

    monkeypatch.setattr(app_mod, "Typist", RecordingTypist)
    events: list[str] = []
    monkeypatch.setattr(
        hotkey_mod,
        "check_permissions",
        lambda: ["no input monitoring", "no accessibility"],
    )

    def fake_start_hotkeys(bindings):  # noqa: ANN001, ANN202
        events.append("start_hotkeys")
        return SimpleNamespace(stop=lambda: None)

    monkeypatch.setattr(hotkey_mod, "start_hotkeys", fake_start_hotkeys)
    app = app_mod.VoiceTypeApp(
        Config(mode="toggle", sounds=False, overlay=False)
    )
    app._status = events.append  # instance attr shadows the staticmethod
    assert app._start_hotkey_listener() is not None
    assert events[:2] == [
        "WARNING: no input monitoring",
        "WARNING: no accessibility",
    ]
    assert events[-1] == "start_hotkeys"


def test_sound_player_constructs_and_plays_safely() -> None:
    """SoundPlayer must never raise, even for bogus/empty sounds."""
    from voxtype.inject import SoundPlayer

    player = SoundPlayer("Glass", "Bottle", "")
    player.play("Glass")          # real system sound (or afplay fallback)
    player.play("")               # empty: no-op
    player.play("/no/such.aiff")  # missing file: no-op, no raise


# -- setup wizard ---------------------------------------------------------


def _fake_questionary(script):
    """Build a fake questionary module that returns scripted answers."""
    it = iter(script)

    class _Ans:
        def __init__(self, v):
            self._v = v

        def ask(self):
            return self._v

    def _select(msg, choices=None, default=None):  # noqa: ANN001, ANN202
        return _Ans(next(it))

    def _confirm(msg, default=None):  # noqa: ANN001, ANN202
        return _Ans(next(it))

    def _text(msg, default="", validate=None):  # noqa: ANN001, ANN202
        return _Ans(next(it))

    class _Choice:
        def __init__(self, title, value=None):  # noqa: ANN001
            self.title, self.value = title, value

    return types.SimpleNamespace(
        select=_select, confirm=_confirm, text=_text, Choice=_Choice
    )


def test_toml_value_serialization() -> None:
    from voxtype.setup_wizard import _toml_value

    assert _toml_value(True) == "true"
    assert _toml_value("<ctrl>+;") == '"<ctrl>+;"'
    assert _toml_value(["a", "b"]) == '["a", "b"]'
    assert _toml_value('say "go"') == '"say \\"go\\""'


def test_setup_wizard_writes_valid_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(
        sys.modules,
        "questionary",
        _fake_questionary(
            [
                "parakeet-mlx",             # engine
                "toggle",                   # mode
                "hold",                     # segmentation
                "Keep default (<ctrl>+;)",  # hotkey
                False,                      # extras?
            ]
        ),
    )
    from voxtype.config import load_config
    from voxtype.setup_wizard import run_setup

    out = tmp_path / "c.toml"
    assert run_setup(config_path=out, force=True) == 0
    cfg = load_config(out)
    assert (cfg.engine, cfg.mode, cfg.segmentation) == (
        "parakeet-mlx",
        "toggle",
        "hold",
    )


def test_setup_wizard_wake_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(
        sys.modules,
        "questionary",
        _fake_questionary(
            [
                "moonshine",                # engine
                "medium-streaming",         # model_arch
                "wake",                     # mode
                "Keep default (<ctrl>+;)",  # hotkey
                "claude",                   # wake word
                "hey cloud, claud",         # aliases
                False,                      # extras?
            ]
        ),
    )
    from voxtype.config import load_config
    from voxtype.setup_wizard import run_setup

    out = tmp_path / "c.toml"
    assert run_setup(config_path=out, force=True) == 0
    cfg = load_config(out)
    assert cfg.mode == "wake"
    assert cfg.wake_word_aliases == ["hey cloud", "claud"]


def test_setup_wizard_cancel_leaves_no_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(
        sys.modules, "questionary", _fake_questionary([None])  # engine=None
    )
    from voxtype.setup_wizard import run_setup

    out = tmp_path / "c.toml"
    assert run_setup(config_path=out, force=True) == 1
    assert not out.exists()


def test_setup_wizard_cancel_at_optional_prompt_keeps_file(
    monkeypatch, tmp_path
) -> None:
    """Aborting an OPTIONAL prompt cancels — never overwrites a config."""
    out = tmp_path / "c.toml"
    out.write_text('engine = "moonshine"\n')  # pre-existing config
    monkeypatch.setitem(
        sys.modules,
        "questionary",
        _fake_questionary(
            [
                "parakeet-mlx",             # engine
                "toggle",                   # mode
                "hold",                     # segmentation
                "Keep default (<ctrl>+;)",  # hotkey
                None,                       # extras? -> CANCEL (Ctrl-C)
            ]
        ),
    )
    from voxtype.setup_wizard import run_setup

    assert run_setup(config_path=out, force=True) == 1
    assert out.read_text() == 'engine = "moonshine"\n'  # untouched


def test_setup_config_flag_before_subcommand_preserved(
    monkeypatch, tmp_path
) -> None:
    """`voxtype --config X setup` must target X, not the default."""
    import voxtype.cli as cli_mod

    target = tmp_path / "chosen.toml"
    seen = {}

    def _fake_run_setup(config_path=None, force=False):  # noqa: ANN001
        seen["path"] = config_path
        return 0

    monkeypatch.setattr(
        "voxtype.setup_wizard.run_setup",
        _fake_run_setup,
    )
    monkeypatch.setattr(
        sys, "argv", ["voxtype", "--config", str(target), "setup"]
    )
    assert cli_mod.main() == 0
    assert seen["path"] == target  # global --config survived the subparser


def test_toml_value_escapes_control_chars() -> None:
    """A string with newlines/tabs/etc. serializes to valid TOML.

    The old serializer escaped only backslashes and quotes, so a value
    with a control character produced a document tomllib rejected.
    """
    import tomllib

    from voxtype.setup_wizard import _toml_value

    value = "line1\nline2\ttab\rreturn\x00nul"
    rendered = _toml_value(value)
    assert tomllib.loads(f"x = {rendered}")["x"] == value


def test_setup_wizard_rejects_unsupported_recorded_hotkey(
    monkeypatch, tmp_path
) -> None:
    """A recorded chord that ``parse_hotkey`` can't accept is refused,
    falling through to manual entry instead of being written blindly."""
    import voxtype.hotkey as hotkey_mod

    monkeypatch.setattr(
        hotkey_mod, "record_hotkey", lambda *a, **k: "<caps_lock>"
    )
    monkeypatch.setitem(
        sys.modules,
        "questionary",
        _fake_questionary(
            [
                "parakeet-mlx",                      # engine
                "toggle",                            # mode
                "hold",                              # segmentation
                "Record one now (press the combo)",  # hotkey: record
                "<ctrl>+<alt>+d",                    # manual fallback
                False,                               # extras?
            ]
        ),
    )
    from voxtype.config import load_config
    from voxtype.setup_wizard import run_setup

    out = tmp_path / "c.toml"
    assert run_setup(config_path=out, force=True) == 0
    cfg = load_config(out)
    # The unsupported recorded <caps_lock> was refused; the manually
    # typed chord was written instead.
    assert cfg.hotkey == "<ctrl>+<alt>+d"


def test_setup_wizard_atomic_write_preserves_existing_on_failure(
    monkeypatch, tmp_path
) -> None:
    """A write interrupted mid-flight never truncates the old config.

    An fsync failure must leave the pre-existing file byte-for-byte
    intact and leave no temporary litter behind.
    """
    import voxtype.setup_wizard as sw

    out = tmp_path / "c.toml"
    out.write_text('engine = "moonshine"\n')
    monkeypatch.setitem(
        sys.modules,
        "questionary",
        _fake_questionary(
            [
                "parakeet-mlx",             # engine
                "toggle",                   # mode
                "hold",                     # segmentation
                "Keep default (<ctrl>+;)",  # hotkey
                False,                      # extras?
            ]
        ),
    )

    def boom(_fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(sw.os, "fsync", boom)
    with pytest.raises(OSError):
        sw.run_setup(config_path=out, force=True)
    assert out.read_text() == 'engine = "moonshine"\n'  # untouched
    assert list(tmp_path.glob(".config-*")) == []  # temp cleaned up
