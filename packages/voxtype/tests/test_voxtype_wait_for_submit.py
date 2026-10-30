"""Hands-free wait_for_submit: text is held until a submit phrase."""

from __future__ import annotations

import pytest

from voxtype.config import Config


class OrderedTypist:
    """Typist stand-in that records typing and Enter in one ordered log."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def type_text(self, text: str) -> None:
        self.events.append(text)

    def press_enter(self) -> None:
        self.events.append("<ENTER>")


class CleanupSpy:
    """Post-processor stand-in that records what cleanup receives."""

    name = "spy"
    last_outcome = (1.0, "")

    def __init__(self) -> None:
        self.seen: list[str] = []

    def clean(self, text: str, since: float | None = None) -> str:
        self.seen.append(text)
        return text


@pytest.fixture
def make_app(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Build a VoiceTypeApp that records keystrokes instead of sending them."""
    import voxtype.app as app_mod

    def make(**kw):  # noqa: ANN003, ANN202
        kw.setdefault("sounds", False)
        kw.setdefault("overlay", False)
        kw.setdefault("copy_to_clipboard", False)
        kw.setdefault("strip_fillers", False)
        cfg = Config(**kw)
        cfg.validate()
        monkeypatch.setattr(app_mod, "Typist", OrderedTypist)
        return app_mod.VoiceTypeApp(cfg)

    return make


def test_pieces_are_held_until_suffix(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("First I went to the office.")
    app.handle_utterance("No sorry, the school.")
    assert app.typist.events == []
    app.handle_utterance("Then home. Send it.")
    assert app.typist.events == [
        "First I went to the office. No sorry, the school. Then home. ",
        "<ENTER>",
    ]


def test_cleanup_sees_the_whole_dictation_once(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    spy = CleanupSpy()
    app._post = spy
    app.handle_utterance("one")
    app.handle_utterance("two")
    app.handle_utterance("three, over and out")
    app.flush_deliveries()
    assert spy.seen == ["one two three"]
    assert app.typist.events == ["one two three ", "<ENTER>"]


def test_bare_submit_phrase_types_held_text(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("ship it")
    app.handle_utterance("go")
    assert app.typist.events == ["ship it ", "<ENTER>"]
    app.handle_utterance("Send it.")  # nothing held: just Enter
    assert app.typist.events == ["ship it ", "<ENTER>", "<ENTER>"]


def test_toggle_off_types_held_text_without_enter(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("half a thought")
    app.toggle()
    assert app.typist.events == ["half a thought "]
    assert app._state.name == "PAUSED"


def test_stop_phrase_types_held_text_without_enter(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("half a thought")
    app.handle_utterance("stop listening")
    assert app.typist.events == ["half a thought "]
    assert app._state.name == "PAUSED"


def test_cancel_discards_held_text(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("never mind this")
    app.cancel()
    assert app.typist.events == []
    app._last_toggle = 0.0
    app.toggle()  # back on: the discarded text does not come back
    app.handle_utterance("fresh, send it")
    assert app.typist.events == ["fresh ", "<ENTER>"]


def test_esc_is_never_intercepted_hands_free(make_app) -> None:  # noqa: ANN001
    """Hands-free mode always listens, so Esc stays with the focused app."""
    assert make_app(mode="vad")._is_recording() is False
    app = make_app(mode="toggle")
    app.toggle()
    assert app._is_recording() is True


def test_wait_for_submit_off_types_each_piece(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad", wait_for_submit=False)
    app.handle_utterance("one")
    app.handle_utterance("two, send it")
    assert app.typist.events == ["one ", "two ", "<ENTER>"]


def test_toggle_mode_is_unaffected(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="toggle", segmentation="vad")
    app.toggle()
    app.handle_utterance("typed at once")
    assert app.typist.events == ["typed at once "]


def test_validate_rejects_non_bool() -> None:
    with pytest.raises(ValueError, match="wait_for_submit"):
        Config(wait_for_submit="yes").validate()  # type: ignore[arg-type]


def test_toggle_mid_utterance_keeps_held_text_first(
    make_app, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """A toggle-off racing a final "... send it" never submits the final
    words ahead of the held text: the two take turns."""
    import threading

    import voxtype.app as app_mod

    app = make_app(mode="vad")
    app.handle_utterance("first part")
    reached, proceed = threading.Event(), threading.Event()
    orig = app_mod.is_exact_phrase

    def pausing(text: str, phrase: str) -> bool:
        reached.set()
        assert proceed.wait(timeout=5.0)
        return orig(text, phrase)

    monkeypatch.setattr(app_mod, "is_exact_phrase", pausing)
    worker = threading.Thread(
        target=lambda: app.handle_utterance("last part, send it")
    )
    worker.start()
    assert reached.wait(timeout=5.0)
    toggler = threading.Thread(target=app.toggle)
    toggler.start()
    toggler.join(timeout=0.2)
    proceed.set()
    worker.join(timeout=5.0)
    toggler.join(timeout=5.0)
    assert not worker.is_alive() and not toggler.is_alive()
    assert app.typist.events == ["first part last part ", "<ENTER>"]
    assert app._state.name == "PAUSED"


def test_slow_held_typing_keeps_grace_for_last_words(
    make_app, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """Typing a long held text on toggle-off must not use up the grace
    window meant for the words still being transcribed."""
    import voxtype.app as app_mod

    now = [100.0]
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: now[0])
    app = make_app(mode="vad")
    app.handle_utterance("a long held dictation")
    real_type = app.typist.type_text

    def slow_type(text: str) -> None:
        now[0] += 3.0  # longer than TOGGLE_GRACE
        real_type(text)

    monkeypatch.setattr(app.typist, "type_text", slow_type)
    app.toggle()
    app.handle_utterance("last words")  # the flushed in-flight piece
    assert app.typist.events == ["a long held dictation ", "last words "]


def test_waiting_last_words_survive_slow_held_typing(
    make_app, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """The last words, arriving WHILE a toggle-off types slow held text,
    still get typed after it (the grace window restarts in the same
    turn as the typing)."""
    import threading

    import voxtype.app as app_mod

    now = [100.0]
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: now[0])
    app = make_app(mode="vad")
    app.handle_utterance("a long held dictation")
    typing, finish = threading.Event(), threading.Event()
    real_type = app.typist.type_text

    def slow_type(text: str) -> None:
        typing.set()
        assert finish.wait(timeout=5.0)
        now[0] += 3.0  # longer than TOGGLE_GRACE
        real_type(text)

    monkeypatch.setattr(app.typist, "type_text", slow_type)
    toggler = threading.Thread(target=app.toggle)
    toggler.start()
    assert typing.wait(timeout=5.0)
    monkeypatch.setattr(app.typist, "type_text", real_type)
    worker = threading.Thread(
        target=lambda: app.handle_utterance("last words")
    )
    worker.start()  # waits for the toggle's turn to end
    worker.join(timeout=0.2)
    finish.set()
    toggler.join(timeout=5.0)
    worker.join(timeout=5.0)
    assert not worker.is_alive() and not toggler.is_alive()
    assert app.typist.events == ["a long held dictation ", "last words "]


def test_last_words_authorized_by_arrival_time(
    make_app, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """Last words that arrive inside the grace window still type even if
    they wait past it for a busy lock (e.g. cleaned held text typing)."""
    import threading

    import voxtype.app as app_mod

    now = [100.0]
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: now[0])
    app = make_app(mode="vad")
    app.toggle()  # off: grace window until 102
    app._lock.acquire()  # the delivery worker is busy typing
    worker = threading.Thread(
        target=lambda: app.handle_utterance("last words")
    )
    worker.start()  # arrives at 100, then waits for the lock
    worker.join(timeout=0.2)
    now[0] += 3.0  # typing outlasts the grace window
    app._lock.release()
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert app.typist.events == ["last words "]


def test_ok_show_types_held_text_for_review(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("First the changelog.")
    app.handle_utterance("Then the version. Okay show.")
    assert app.typist.events == ["First the changelog. Then the version. "]
    assert app._state.name == "ACTIVE"  # still listening
    app.handle_utterance("Okay go.")  # nothing held: just send it
    assert app.typist.events[-1] == "<ENTER>"
    assert len(app.typist.events) == 2


def test_ok_show_alone_with_nothing_held_does_nothing(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Ok show.")
    assert app.typist.events == []


def test_ok_go_submits_in_one_step(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("ship it")
    app.handle_utterance("Tell me what you think, okay go.")
    assert app.typist.events == [
        "ship it Tell me what you think ",
        "<ENTER>",
    ]


def test_ok_show_without_holding_strips_the_phrase(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad", wait_for_submit=False)
    app.handle_utterance("Fix the build, okay show.")
    assert app.typist.events == ["Fix the build "]


def test_validate_rejects_bad_show_suffixes() -> None:
    with pytest.raises(ValueError, match="show_suffixes"):
        Config(show_suffixes=["ok show", ""]).validate()


class ResetSpy:
    """Engine stand-in that counts reset requests."""

    def __init__(self) -> None:
        self.resets = 0

    def request_reset(self) -> None:
        self.resets += 1


def test_discard_drops_held_text_and_keeps_listening(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app._engine = ResetSpy()
    app.handle_utterance("Something I said by accident.")
    app.handle_utterance("More of it.")
    app.discard()
    assert app.typist.events == []
    assert app._state.name == "ACTIVE"  # still listening
    assert app._engine.resets == 1
    app._discard_until = 0.0  # past the in-flight window
    app.handle_utterance("What I meant. Send it.")
    assert app.typist.events == ["What I meant. ", "<ENTER>"]


def test_discard_drops_speech_still_in_flight(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Held words.")
    app.discard()
    app.handle_utterance("Said before the discard. Send it.")
    assert app.typist.events == []


def test_discard_hotkey_only_intercepted_while_listening_hands_free(
    make_app,  # noqa: ANN001
) -> None:
    assert make_app(mode="toggle")._is_discardable() is False
    app = make_app(mode="vad")
    assert app._is_discardable() is True
    app.toggle()  # pause
    assert app._is_discardable() is False
    app.discard()  # paused: a no-op
    assert app.typist.events == []


def test_discard_hotkey_default_and_validation() -> None:
    assert Config().discard_hotkey == "<shift>+<esc>"
    with pytest.raises(ValueError, match="discard_hotkey"):
        Config(discard_hotkey=3).validate()  # type: ignore[arg-type]


def test_discard_then_pause_still_drops_in_flight_speech(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Held words.")
    app.discard()
    app.toggle()  # pause right after
    app.handle_utterance("Discarded words. Send it.")
    assert app.typist.events == []


class ConfirmingEngine:
    """Engine stand-in that confirms a reset only when told to."""

    def __init__(self) -> None:
        self.pending: list = []

    def request_reset_then(self, done) -> None:  # noqa: ANN001
        self.pending.append(done)

    def apply(self) -> None:
        for done in self.pending:
            done()
        self.pending.clear()


def test_discard_drops_speech_until_the_engine_confirms_reset(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app._engine = ConfirmingEngine()
    app.handle_utterance("Held words.")
    app.discard()
    # A slow decode of earlier speech, however late, is still dropped.
    app.handle_utterance("Said before the discard. Send it.")
    assert app.typist.events == []
    app._engine.apply()
    app.handle_utterance("New words. Send it.")
    assert app.typist.events == ["New words. ", "<ENTER>"]


def test_discard_before_engine_ready_is_ignored(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app._engine = ConfirmingEngine()
    app._ready.clear()  # engine still starting
    app.discard()
    assert app._engine.pending == []
    app._ready.set()
    app.handle_utterance("Words. Send it.")
    assert app.typist.events == ["Words. ", "<ENTER>"]


def test_clipboard_holds_only_the_last_sent_message(
    make_app, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    import voxtype.app as app_mod

    copied: list[str] = []
    monkeypatch.setattr(app_mod, "copy_to_clipboard", copied.append)
    app = make_app(mode="vad", copy_to_clipboard=True)
    app.handle_utterance("first message, send it")
    app.handle_utterance("second message, send it")
    app.flush_deliveries()
    assert copied == ["first message", "second message"]


def test_bare_submit_after_show_keeps_the_shown_text(
    make_app,  # noqa: ANN001
) -> None:
    app = make_app(mode="vad")
    app.handle_utterance("look at this, ok show")
    app.handle_utterance("go")
    app.flush_deliveries()
    assert app._session_texts == ["look at this"]


@pytest.mark.parametrize(
    ("said", "enter"),
    [
        ("fix it, senate", True),
        ("fix it, ok shall we", False),
        ("fix it, ok so me", False),
    ],
)
def test_misheard_suffixes(make_app, said: str, enter: bool) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance(said)
    app.flush_deliveries()
    assert app.typist.events == ["fix it "] + (["<ENTER>"] if enter else [])


def test_shown_then_sent_message_stays_whole(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("first part, ok show")
    app.handle_utterance("second part, send it")
    app.handle_utterance("next message, send it")
    app.flush_deliveries()
    assert app._session_texts == ["next message"]
    app._session_texts.clear()
    app.handle_utterance("a, ok show")
    app.handle_utterance("b, send it")
    app.flush_deliveries()
    assert app._session_texts == ["a", "b"]


class Clock:
    """Settable stand-in for time.monotonic."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def gap_app(make_app, monkeypatch, tmp_path):  # noqa: ANN001, ANN201
    """Hands-free app whose engine reports 2 s segments, on a fake clock."""
    import types

    import voxtype.app as app_mod

    def make(**kw):  # noqa: ANN003, ANN202
        clock = Clock()
        monkeypatch.setattr(app_mod.time, "monotonic", clock)
        monkeypatch.setattr(
            app_mod, "DEFAULT_CONFIG_PATH", tmp_path / "config.toml"
        )
        app = make_app(mode="vad", **kw)
        app._engine = types.SimpleNamespace(last_segment_secs=2.0)
        return app, clock

    return make


def test_long_silence_drops_held_text(gap_app, tmp_path) -> None:  # noqa: ANN001
    app, clock = gap_app()
    app.handle_utterance("meeting chatter about lunch")
    clock.now += 60  # 58 s of silence before the next 2 s of speech
    app.handle_utterance("fix the parser, send it")
    app.flush_deliveries()
    assert app.typist.events == ["fix the parser ", "<ENTER>"]
    assert (tmp_path / "dropped.txt").read_text() == (
        "meeting chatter about lunch\n"
    )


def test_short_pause_keeps_held_text(gap_app) -> None:  # noqa: ANN001
    app, clock = gap_app()
    app.handle_utterance("first part")
    clock.now += 12  # 10 s pause to think
    app.handle_utterance("second part, send it")
    app.flush_deliveries()
    assert app.typist.events == ["first part second part ", "<ENTER>"]


def test_bare_submit_after_long_pause_sends_held(gap_app) -> None:  # noqa: ANN001
    app, clock = gap_app()
    app.handle_utterance("think it over")
    clock.now += 60
    app.handle_utterance("send it")
    app.flush_deliveries()
    assert app.typist.events == ["think it over ", "<ENTER>"]


def test_gap_rule_off(gap_app) -> None:  # noqa: ANN001
    app, clock = gap_app(held_gap=0)
    app.handle_utterance("first part")
    clock.now += 60
    app.handle_utterance("second part, send it")
    app.flush_deliveries()
    assert app.typist.events == ["first part second part ", "<ENTER>"]


def test_no_segment_length_no_gap_rule(gap_app) -> None:  # noqa: ANN001
    app, clock = gap_app()
    app._engine = None
    app.handle_utterance("first part")
    clock.now += 60
    app.handle_utterance("second part, send it")
    app.flush_deliveries()
    assert app.typist.events == ["first part second part ", "<ENTER>"]


def test_word_cap_types_without_enter(gap_app) -> None:  # noqa: ANN001
    app, _ = gap_app(held_max_words=5)
    app.handle_utterance("one two three four")
    app.handle_utterance("five six, send it")
    app.flush_deliveries()
    assert app.typist.events == ["one two three four five six "]
    app.handle_utterance("go")
    app.flush_deliveries()
    assert app.typist.events[-1] == "<ENTER>"


def test_under_word_cap_submits(gap_app) -> None:  # noqa: ANN001
    app, _ = gap_app(held_max_words=5)
    app.handle_utterance("one two three, send it")
    app.flush_deliveries()
    assert app.typist.events == ["one two three ", "<ENTER>"]


@pytest.mark.parametrize("bad", [-1, "10", True])
def test_held_limits_validated(bad: object) -> None:
    for name in ("held_gap", "held_max_words"):
        with pytest.raises(ValueError, match=name):
            Config(**{name: bad}).validate()


class StartSpy(CleanupSpy):
    """Cleanup stand-in that also answers the message-start check."""

    def __init__(self, answer: tuple[int | None, str]) -> None:
        super().__init__()
        self.answer = answer
        self.asked: list[list[str]] = []

    def message_start(self, pieces: list[str]) -> tuple[int | None, str]:
        self.asked.append(pieces)
        return self.answer


def test_unrelated_pieces_are_dropped(gap_app, tmp_path) -> None:  # noqa: ANN001
    app, _ = gap_app(held_check_words=4)
    app._post = StartSpy((2, ""))
    for piece in ("tv news tonight", "weather is clear"):
        app.handle_utterance(piece)
    app.handle_utterance("fix the bug, send it")
    app.flush_deliveries()
    assert app._post.asked == [
        ["tv news tonight", "weather is clear", "fix the bug"]
    ]
    assert app.typist.events == ["fix the bug ", "<ENTER>"]
    assert (tmp_path / "dropped.txt").read_text() == (
        "tv news tonight weather is clear\n"
    )


@pytest.mark.parametrize("answer", [(0, ""), (None, "timed out")])
def test_check_keeping_everything(gap_app, answer) -> None:  # noqa: ANN001
    app, _ = gap_app(held_check_words=4)
    app._post = StartSpy(answer)
    app.handle_utterance("background first")
    app.handle_utterance("then the request, send it")
    app.flush_deliveries()
    assert app.typist.events == [
        "background first then the request ", "<ENTER>"
    ]


@pytest.mark.parametrize(
    ("pieces", "words"),
    [(["only one piece here today, send it"], 4),  # one piece
     (["two", "short, send it"], 4)],  # under held_check_words
)
def test_check_skipped(gap_app, pieces, words) -> None:  # noqa: ANN001
    app, _ = gap_app(held_check_words=words)
    app._post = StartSpy((1, ""))
    for piece in pieces:
        app.handle_utterance(piece)
    app.flush_deliveries()
    assert app._post.asked == []


def test_cap_applies_after_the_check(gap_app) -> None:  # noqa: ANN001
    app, _ = gap_app(held_check_words=4, held_max_words=5)
    app._post = StartSpy((1, ""))
    app.handle_utterance("one two three four five six seven")
    app.handle_utterance("fix it, send it")
    app.flush_deliveries()
    assert app.typist.events == ["fix it ", "<ENTER>"]


def test_capped_text_stays_with_the_next_part(gap_app) -> None:  # noqa: ANN001
    app, _ = gap_app(held_max_words=5)
    app.handle_utterance("one two three four five six, send it")
    app.handle_utterance("and seven, ok show")
    app.flush_deliveries()
    assert app._session_texts == ["one two three four five six", "and seven"]


def test_release_during_check_keeps_capped_text(gap_app) -> None:  # noqa: ANN001
    import threading

    gate = threading.Event()

    class SlowStart(StartSpy):
        def message_start(self, pieces):  # noqa: ANN001, ANN202
            gate.wait(5)
            return (0, "")

    app, _ = gap_app(held_check_words=4, held_max_words=5)
    app._post = SlowStart((0, ""))
    app.handle_utterance("one two three")
    app.handle_utterance("four five six, send it")
    app.handle_utterance("and seven, ok show")  # while the check runs
    gate.set()
    app.flush_deliveries()
    assert "<ENTER>" not in app.typist.events
    assert app._session_texts == ["one two three four five six", "and seven"]
