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
    app.handle_utterance("Then home. Go go.")
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
    app.handle_utterance("Go go.")  # nothing held: just Enter
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
    app.handle_utterance("fresh, go go")
    assert app.typist.events == ["fresh ", "<ENTER>"]


def test_wait_for_submit_off_types_each_piece(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad", wait_for_submit=False)
    app.handle_utterance("one")
    app.handle_utterance("two, go go")
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
    """A toggle-off racing a final "... go go" never submits the final
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
        target=lambda: app.handle_utterance("last part, go go")
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
