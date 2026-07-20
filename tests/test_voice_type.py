"""Tests for voice-type: config, logic, app state machine, CLI, moonshine.

Parakeet engine tests (model install + capture loop) live in
test_voice_type_engines.py.
"""

from __future__ import annotations

import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_code_tools.voice_type.config import (
    Config,
    load_config,
    sample_config,
    write_sample_config,
)
from claude_code_tools.voice_type.hotkey import parse_hotkey
from claude_code_tools.voice_type.logic import (
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


# -- config ---------------------------------------------------------------


def test_default_config_is_valid() -> None:
    Config().validate()


def test_load_config_missing_default_uses_defaults(tmp_path: Path) -> None:
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
    assert "voice-type configuration" in sample_config()


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
    import claude_code_tools.voice_type.app as app_mod

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


def test_app_no_typing_after_concurrent_deactivation(
    app_factory, monkeypatch
) -> None:  # noqa: ANN001
    """Hotkey deactivation between state snapshot and typing wins.

    A worker thread handles an utterance but is paused (deterministically,
    via a patched strip_fillers) between the state inspection and the
    injection; the main thread then toggles the app off. The stale
    utterance must not be typed after deactivation.
    """
    import claude_code_tools.voice_type.app as app_mod

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
    assert app.typist.typed == []


def test_app_no_enter_after_concurrent_deactivation(
    app_factory, monkeypatch
) -> None:  # noqa: ANN001
    import claude_code_tools.voice_type.app as app_mod

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
    assert app.typist.enters == 0
    assert _state(app) == "PAUSED"


def test_app_wake_types_remainder_unless_deactivated_meanwhile(
    app_factory, monkeypatch
) -> None:  # noqa: ANN001
    import claude_code_tools.voice_type.app as app_mod

    app, _ = app_factory(mode="wake")

    def racing_strip(text: str) -> str:
        app.toggle()  # deactivate right after wake-word activation
        return text

    monkeypatch.setattr(app_mod, "strip_fillers", racing_strip)
    app.handle_utterance("claude write hello")
    assert app.typist.typed == []
    assert _state(app) == "PASSIVE"


def test_app_idle_timeout_effects_suppressed_by_concurrent_toggle(
    app_factory, monkeypatch
) -> None:  # noqa: ANN001
    """A toggle landing between the idle PASSIVE commit and its
    sound/status must suppress the stale idle effects: the last
    reported status reflects the actual (ACTIVE) state."""
    import claude_code_tools.voice_type.app as app_mod

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
    import claude_code_tools.voice_type.app as app_mod
    import claude_code_tools.voice_type.engines as engines_mod

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
    import claude_code_tools.voice_type.app as app_mod
    import claude_code_tools.voice_type.engines as engines_mod

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


# -- CLI ------------------------------------------------------------------


def _run_cli(monkeypatch, tmp_path, app_cls, extra_args=()):  # noqa: ANN001, ANN202
    import claude_code_tools.voice_type.app as app_mod
    from claude_code_tools.voice_type.cli import main

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("")  # defaults only, isolated from ~/.config
    monkeypatch.setattr(app_mod, "VoiceTypeApp", app_cls)
    monkeypatch.setattr(
        sys,
        "argv",
        ["voice-type", "--config", str(cfg_path), *extra_args],
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

    from claude_code_tools.voice_type.engines import MoonshineEngine

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
    from claude_code_tools.voice_type.app import VoiceTypeApp

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
    from claude_code_tools.voice_type.app import State

    app = _grace_app()
    app.toggle()  # ACTIVE -> PAUSED
    app.toggle()  # re-fire within debounce window: ignored
    assert app._state is State.PAUSED
    app._last_toggle = 0.0
    app.toggle()  # a real later press works
    assert app._state is State.ACTIVE


def test_wake_word_alias_matches() -> None:
    pytest.importorskip("pynput")
    from claude_code_tools.voice_type.app import State, VoiceTypeApp

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
    from claude_code_tools.voice_type.app import VoiceTypeApp

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
    from claude_code_tools.voice_type.logic import collapse_repeats

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
    from claude_code_tools.voice_type.logic import (
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
    from claude_code_tools.voice_type.app import VoiceTypeApp

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
    from claude_code_tools.voice_type.app import VoiceTypeApp

    app = VoiceTypeApp(Config(mode="toggle", sounds=False, overlay=False))
    app.typist = _CollectingTypist()
    app.paste_last()
    assert app.typist.typed == []


# -- cancel (escape) ------------------------------------------------------


def test_cancel_discards_recording() -> None:
    pytest.importorskip("pynput")
    from claude_code_tools.voice_type.app import State, VoiceTypeApp

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
    from claude_code_tools.voice_type.app import State, VoiceTypeApp

    app = VoiceTypeApp(Config(mode="toggle", sounds=False, overlay=False))
    app.typist = _CollectingTypist()
    app._engine = _RecordingEngine()
    app.cancel()  # paused: nothing happens
    assert app._state is State.PAUSED
    assert app._engine.resets == 0


def test_parse_esc_hotkey() -> None:
    assert parse_hotkey("<esc>") == (frozenset(), "<esc>")
