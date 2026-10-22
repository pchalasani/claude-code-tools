"""Submit suffixes: "fix the build, over and out" types, then presses Enter."""

from __future__ import annotations

import pytest

from voxtype.config import Config
from voxtype.logic import strip_submit_suffix


class OrderedTypist:
    """Typist stand-in that records typing and Enter in one ordered log."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def type_text(self, text: str) -> None:
        self.events.append(text)

    def press_enter(self) -> None:
        self.events.append("<ENTER>")


@pytest.fixture
def make_app(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Build a VoiceTypeApp that records keystrokes instead of sending them."""
    import voxtype.app as app_mod

    def make(**kw):  # noqa: ANN003, ANN202
        kw.setdefault("sounds", False)
        kw.setdefault("overlay", False)
        kw.setdefault("copy_to_clipboard", False)
        cfg = Config(**kw)
        cfg.validate()
        monkeypatch.setattr(app_mod, "Typist", OrderedTypist)
        return app_mod.VoiceTypeApp(cfg)

    return make


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fix the build, over and out.", "Fix the build"),
        ("Fix the build. Over and out!", "Fix the build."),
        ("fix the build over and out", "fix the build"),
        ("Fix it — over, and out", "Fix it"),
        ("Over and out.", ""),
        ("we ran over and out of time", None),
        ("over and outline it", None),
        ("and out", None),
        ("", None),
    ],
)
def test_strip_submit_suffix(text: str, expected: str | None) -> None:
    assert strip_submit_suffix(text, ["over and out"]) == expected


def test_strip_submit_suffix_no_phrases() -> None:
    assert strip_submit_suffix("fix it, over and out", []) is None


def test_suffix_types_text_then_presses_enter(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Fix the build, over and out.")
    assert app.typist.events == ["Fix the build ", "<ENTER>"]
    assert app._state.name == "ACTIVE"


def test_suffix_alone_only_presses_enter(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Over and out.")
    assert app.typist.events == ["<ENTER>"]


def test_suffix_mid_sentence_is_typed(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("we ran over and out of time")
    assert app.typist.events == ["we ran over and out of time "]


def test_suffix_disabled(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad", submit_suffixes=[])
    app.handle_utterance("fix it, over and out")
    assert app.typist.events == ["fix it, over and out "]


def test_suffix_after_fillers_only_presses_enter(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad", strip_fillers=True)
    app.handle_utterance("um, over and out")
    assert app.typist.events == ["<ENTER>"]


def test_suffix_after_wake_word(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="wake")
    app.handle_utterance("claude fix the build over and out")
    assert app.typist.events == ["fix the build ", "<ENTER>"]


def test_suffix_in_toggle_off_grace(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.toggle()  # off; this utterance was already in flight
    app.handle_utterance("send it, over and out")
    assert app.typist.events == ["send it ", "<ENTER>"]


def test_stop_phrase_still_wins(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("stop listening, over and out")
    assert app.typist.events == []
    assert app._state.name == "PAUSED"


@pytest.mark.parametrize("bad", ["over and out", ["over and out", " "], [3]])
def test_validate_rejects_bad_submit_suffixes(bad: object) -> None:
    with pytest.raises(ValueError, match="submit_suffixes"):
        Config(submit_suffixes=bad).validate()  # type: ignore[arg-type]
