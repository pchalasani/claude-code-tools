"""Recent typed text as cleanup context, and misheard submit phrases."""

from __future__ import annotations

import pytest
from test_voxtype_cerebras import _proc, api, live  # noqa: F401
from test_voxtype_wait_for_submit import CleanupSpy, make_app  # noqa: F401

import voxtype.recent as recent_mod
from voxtype.config import Config
from voxtype.logic import strip_submit_suffix
from voxtype.postprocess_cerebras import build_messages, system_prompt
from voxtype.recent import RecentText, copied_context


class ContextSpy(CleanupSpy):
    """Cleanup stand-in that also records the context it is given."""

    takes_context = True

    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[str] = []

    def clean(  # type: ignore[override]
        self, text: str, since: float | None = None, context: str = ""
    ) -> str:
        self.contexts.append(context)
        return super().clean(text, since)


def test_recent_keeps_the_last_words(monkeypatch) -> None:  # noqa: ANN001
    clock = [100.0]
    monkeypatch.setattr(recent_mod.time, "monotonic", lambda: clock[0])
    recent = RecentText()
    recent.add("one two three")
    recent.add("four five")
    assert recent.recent(4) == "two three four five"
    assert recent.recent(0) == ""
    clock[0] += recent_mod.CONTEXT_MAX_AGE + 1
    recent.add("six")
    assert recent.recent(10) == "six"  # older text is too old


def test_copied_context_is_detected() -> None:
    context = "Please ask Jevon to restart the Kubernetes cluster tonight."
    raw = "and then tell him thanks"
    assert copied_context(
        raw, "Please ask Jevon to restart the Kubernetes cluster. Thanks.",
        context,
    )
    assert not copied_context(raw, "And then tell him thanks.", context)
    assert not copied_context(raw, "anything", "")
    # A repeated phrase with a misheard name corrected is not a copy.
    assert not copied_context(
        "please ask jay von to restart the kubernetes cluster tonight",
        context, context,
    )
    # Words the speaker said again are not a copy.
    again = "please ask Jevon to restart the Kubernetes cluster"
    assert not copied_context(again, "Please ask Jevon to restart the "
                              "Kubernetes cluster.", context)


def test_messages_carry_context_first() -> None:
    msgs = build_messages("tell jay von thanks", "auto", "Ask Jevon.")
    assert msgs[1]["content"] == (
        "<previous>\nAsk Jevon.\n</previous>\n"
        "<transcript>\ntell jay von thanks\n</transcript>"
    )
    assert "<previous>" in msgs[0]["content"]
    # Without context, the prompt is unchanged.
    assert build_messages("hi", "auto")[0]["content"] == system_prompt("auto")
    assert "<previous>" not in system_prompt("auto")


def test_request_sends_context_and_rejects_a_copy(api) -> None:  # noqa: ANN001
    context = "Please ask Jevon to restart the Kubernetes cluster tonight."
    api.reply = "Tell Jevon thanks."
    proc = _proc(api, [])
    assert proc.clean("tell jay von thanks", context=context) == (
        "Tell Jevon thanks."
    )
    assert "<previous>" in api.requests[0]["messages"][1]["content"]
    api.reply = context + " Tell Jevon thanks."
    assert proc.clean("tell jay von thanks", context=context) == (
        "tell jay von thanks"
    )
    assert proc.last_outcome[1] == "copied the previous text"


def test_app_passes_recent_typed_text(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad", post_process_context_words=5)
    spy = ContextSpy()
    app._post = spy
    app.handle_utterance("first message about Jevon, send it")
    app.flush_deliveries()
    app.handle_utterance("second one, send it")
    app.flush_deliveries()
    app.handle_utterance("third, send it")
    app.flush_deliveries()
    assert spy.contexts == [
        "",
        "first message about Jevon",
        "message about Jevon second one",
    ]


def test_context_off(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad", post_process_context_words=0)
    spy = ContextSpy()
    app._post = spy
    app.handle_utterance("first, send it")
    app.handle_utterance("second, send it")
    app.flush_deliveries()
    assert spy.contexts == ["", ""]


@pytest.mark.parametrize("bad", [-1, 300.0, True, "300"])
def test_context_words_must_be_a_whole_number(bad: object) -> None:
    with pytest.raises(ValueError, match="post_process_context_words"):
        Config(post_process_context_words=bad).validate()


@pytest.mark.parametrize(
    ("said", "kept"),
    [
        ("fix the build, Senator.", "fix the build"),
        ("fix the build senators", "fix the build"),
        ("fix the build. Sendit.", "fix the build."),
    ],
)
def test_misheard_send_it_submits(said: str, kept: str) -> None:
    assert strip_submit_suffix(said, Config().submit_suffixes) == kept


def test_misheard_ok_show_shows() -> None:
    assert strip_submit_suffix(
        "check the logs, okay shore.", Config().show_suffixes
    ) == "check the logs"


def test_live_context_spells_names(live) -> None:  # noqa: ANN001
    context = (
        "Can you ask Raghav whether the Kubernetes upgrade for the "
        "Postgrid cluster is done?"
    )
    out = live.clean(
        "also tell ra gov that the post grid dashboard is slow",
        context=context,
    )
    assert live.last_outcome[1] == "", live.last_outcome
    assert "Raghav" in out and "Postgrid" in out
    assert "Kubernetes" not in out  # the context itself is not output
