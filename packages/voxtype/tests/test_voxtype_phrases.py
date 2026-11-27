"""Submit and show phrases: audio before speech, standalone mishearings,
and logging which phrase was heard."""

from __future__ import annotations

import shutil
import subprocess
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from test_voxtype_wait_for_submit import make_app  # noqa: F401

from voxtype.app import _preview
from voxtype.engine_parakeet import (
    CACHE_DIR,
    MAX_SEGMENT_SECONDS,
    MIN_SILENCE,
    SAMPLE_RATE,
)
from voxtype.preroll import PREROLL_SECONDS, PrerollVad, with_preroll


class ScriptedVad:
    """Stands in for sherpa-onnx's detector: one segment at a set start."""

    def __init__(self) -> None:
        self.fed = 0
        self.segment: SimpleNamespace | None = None
        self.resets = 0

    def accept_waveform(self, samples: np.ndarray) -> None:
        self.fed += len(samples)

    def reset(self) -> None:
        self.resets += 1
        self.fed = 0

    def empty(self) -> bool:
        return self.segment is None

    @property
    def front(self) -> SimpleNamespace | None:
        return self.segment


def _feed(vad: PrerollVad, n: int, window: int = 4) -> None:
    """Feed samples whose values are their own indices."""
    for at in range(0, n, window):
        vad.accept_waveform(np.arange(at, at + window, dtype=np.float32))


def test_segment_gets_the_audio_before_it() -> None:
    inner = ScriptedVad()
    vad = PrerollVad(inner, preroll=5, keep=100)
    _feed(vad, 40)
    inner.segment = SimpleNamespace(
        start=20, samples=np.arange(20, 30, dtype=np.float32)
    )
    front = vad.front
    assert front.start == 15
    assert front.samples.tolist() == list(range(15, 30))
    assert not vad.empty()  # everything else passes through
    assert inner.fed == 40


def test_preroll_stops_at_the_start_of_the_audio() -> None:
    inner = ScriptedVad()
    vad = PrerollVad(inner, preroll=5, keep=100)
    _feed(vad, 12)
    inner.segment = SimpleNamespace(start=2, samples=[2.0, 3.0])
    assert vad.front.samples.tolist() == [0, 1, 2, 3]


def test_reset_restarts_the_count() -> None:
    inner = ScriptedVad()
    vad = PrerollVad(inner, preroll=3, keep=100)
    _feed(vad, 40)
    vad.reset()
    assert inner.resets == 1
    _feed(vad, 8)  # values 0..7 again, at indices 0..7
    inner.segment = SimpleNamespace(start=6, samples=[6.0, 7.0])
    assert vad.front.samples.tolist() == [3, 4, 5, 6, 7]


def test_only_recent_audio_is_kept() -> None:
    inner = ScriptedVad()
    vad = PrerollVad(inner, preroll=5, keep=10)
    _feed(vad, 100)
    assert vad.before(50).size == 0  # long gone
    assert vad.before(99).tolist() == [94, 95, 96, 97, 98]


def test_odd_segments_are_left_alone() -> None:
    inner = ScriptedVad()
    vad = PrerollVad(inner, preroll=5, keep=100)
    _feed(vad, 20)
    odd = SimpleNamespace(start=None, samples=[1.0])
    inner.segment = odd
    assert vad.front is odd
    inner.segment = None
    assert vad.front is None


def test_preview_keeps_the_end() -> None:
    text = "word " * 30 + "and it"
    shown = _preview(text)
    assert len(shown) == 60 and shown.endswith("and it") and "…" in shown
    assert _preview("short") == "short"


@pytest.mark.parametrize(
    "said", ["Send.", "End it.", "Sunday.", "Said it.", "So neat."]
)
def test_send_said_alone_submits(make_app, said: str) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Fix the build.")
    app.handle_utterance(said)
    assert app.typist.events == ["Fix the build. ", "<ENTER>"]


def test_misheard_send_inside_a_sentence_is_text(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("We should end it.")
    app.handle_utterance("send it")
    assert app.typist.events == ["We should end it. ", "<ENTER>"]


@pytest.mark.parametrize(
    "said", ["Okay, show you.", "Okay for me.", "Show me.", "Type it."]
)
def test_show_mishearing_said_alone_shows(make_app, said: str) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Check the logs.")
    app.handle_utterance(said)
    assert app.typist.events == ["Check the logs. "]


def test_show_mishearing_with_nothing_held_is_a_reply(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Okay for me.")
    app.handle_utterance("send it")
    assert app.typist.events == ["Okay for me. ", "<ENTER>"]


def test_show_mishearing_ending_a_sentence_is_text(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("That works okay for me.")
    assert app.typist.events == []
    app.handle_utterance("send it")
    assert app.typist.events == ["That works okay for me. ", "<ENTER>"]


def test_the_phrase_heard_is_logged(make_app, capsys) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Fix the build, send it.")
    app.handle_utterance("Check it.")
    app.handle_utterance("Okay for me.")
    app.handle_utterance("Go.")
    err = capsys.readouterr().err
    assert 'submit phrase: "send it."' in err
    assert 'show phrase: "Okay for me."' in err
    assert 'submit phrase: "Go."' in err


def _say(text: str, path: Path) -> np.ndarray:
    subprocess.run(
        ["say", "-v", "Karen", "-o", str(path),
         "--data-format=LEI16@16000", text],
        check=True,
    )
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    return a.astype(np.float32) / 32768


@pytest.mark.skipif(
    not shutil.which("say")
    or not (CACHE_DIR / "silero_vad.onnx").exists(),
    reason="needs macOS say and the cached Silero VAD",
)
def test_real_vad_segment_starts_before_the_first_sound(tmp_path) -> None:  # noqa: ANN001
    """With the real Silero VAD, the segment misses the start of "send
    it"; the wrapped detector's segment includes it."""
    import sherpa_onnx

    speech = _say("send it", tmp_path / "s.wav")
    quiet = np.zeros(SAMPLE_RATE, np.float32)
    audio = np.concatenate([quiet, speech, quiet])
    onset = SAMPLE_RATE + int(np.nonzero(np.abs(speech) > 0.01)[0][0])

    def first_segment(wrap: bool) -> tuple[int, int]:
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(CACHE_DIR / "silero_vad.onnx")
        cfg.silero_vad.min_silence_duration = MIN_SILENCE
        cfg.sample_rate = SAMPLE_RATE
        vad = sherpa_onnx.VoiceActivityDetector(
            cfg, buffer_size_in_seconds=60
        )
        if wrap:
            vad = with_preroll(vad, SAMPLE_RATE, MAX_SEGMENT_SECONDS)
        ws = cfg.silero_vad.window_size
        for i in range(0, len(audio) - ws + 1, ws):
            vad.accept_waveform(audio[i:i + ws])
            if not vad.empty():
                front = vad.front
                return front.start, len(front.samples)
        raise AssertionError("no segment")

    start, n = first_segment(False)
    padded_start, padded_n = first_segment(True)
    assert start > onset  # the plain detector clips the "s"
    pre = int(PREROLL_SECONDS * SAMPLE_RATE)
    assert (padded_start, padded_n) == (start - pre, n + pre)
    assert padded_start < onset


def test_audio_log_keeps_the_newest_pieces(tmp_path) -> None:  # noqa: ANN001
    from voxtype.audio_log import AudioLog

    reports: list[str] = []
    log = AudioLog(2, reports.append, folder=tmp_path)
    transcribe = log.wrap(lambda samples, rate: f"piece {samples[0]:.1f}")
    for n in range(3):
        level = 0.1 * (n + 1)
        out = transcribe(np.full(1600, level, np.float32), 16000)
        assert out == f"piece {level:.1f}"
        time.sleep(0.002)  # distinct file names
    transcribe(np.zeros(800, np.float32), 16000)  # warm-up: not saved
    wavs = sorted(p.name for p in tmp_path.glob("*.wav"))
    assert len(wavs) == 2
    index = (tmp_path / "index.txt").read_text().splitlines()
    assert [line.split("\t") for line in index] == [
        [wavs[0], "piece 0.2"], [wavs[1], "piece 0.3"],
    ]
    with wave.open(str(tmp_path / wavs[1])) as w:
        assert (w.getframerate(), w.getnframes()) == (16000, 1600)
    assert reports == []


def test_audio_log_failure_is_reported_once(tmp_path) -> None:  # noqa: ANN001
    from voxtype.audio_log import AudioLog

    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    reports: list[str] = []
    log = AudioLog(5, reports.append, folder=blocker)
    transcribe = log.wrap(lambda samples, rate: "hi")
    assert transcribe(np.ones(10, np.float32), 16000) == "hi"
    assert transcribe(np.ones(10, np.float32), 16000) == "hi"
    assert len(reports) == 1 and "could not save audio" in reports[0]


@pytest.mark.parametrize("bad", [-1, 1001, 2.0, True, "20"])
def test_save_audio_must_be_a_whole_number(bad: object) -> None:
    from voxtype.config import Config

    with pytest.raises(ValueError, match="save_audio"):
        Config(save_audio=bad).validate()


def test_show_phrase_split_in_two_is_one_phrase(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Fix the build.")
    app.handle_utterance("Okay.")
    app.handle_utterance("show me.")
    assert app.typist.events == ["Fix the build. "]


def test_a_whole_phrase_after_okay_keeps_the_okay(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Okay.")
    app.handle_utterance("send it")
    assert app.typist.events == ["Okay. ", "<ENTER>"]


def test_new_words_after_the_earlier_text() -> None:
    from voxtype.rejoin import new_words

    assert new_words("Okay.", "Okay, show me.") == "show me."
    assert new_words("Okay.", "Cash on me.") == ""
    assert new_words("Okay.", "Okay.") == ""
    assert new_words("", "show me") == ""
    assert new_words("Okay.", "Okay—show me.") == "show me"


def test_join_audio_drops_the_shared_samples() -> None:
    from voxtype.rejoin import join_audio

    rng = np.random.default_rng(0)
    audio = rng.uniform(-0.5, 0.5, 3000).astype(np.float32)
    first, second = audio[:2000], audio[1600:]  # 400 samples shared
    assert np.array_equal(join_audio(first, second, 16000), audio)
    apart = join_audio(first, audio[2500:], 16000)
    assert apart.size == 2000 + 3200 + 500  # 0.2 s of silence between


def test_empty_piece_is_decoded_with_the_one_before() -> None:
    from voxtype.rejoin import EmptyRetry

    okay = np.full(1600, 0.1, np.float32)
    show = np.full(1600, 0.2, np.float32)

    def transcribe(samples: np.ndarray, rate: int) -> str:
        if samples.size > 1600:
            return "Okay, show me."
        return "Okay." if samples[0] == np.float32(0.1) else ""

    reports: list[str] = []
    retrying = EmptyRetry(reports.append).wrap(transcribe)
    assert retrying(okay, 16000) == "Okay."
    assert retrying(np.zeros(800, np.float32), 16000) == ""  # keepalive
    assert retrying(show, 16000) == "show me."
    assert retrying(show, 16000) == ""  # nothing before it now
    assert len(reports) == 1 and "show me." in reports[0]


def test_show_me_inside_a_sentence_is_text(make_app) -> None:  # noqa: ANN001
    app = make_app(mode="vad")
    app.handle_utterance("Can you show me.")
    assert app.typist.events == []
