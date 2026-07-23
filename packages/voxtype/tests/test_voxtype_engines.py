"""Parakeet engine tests: model install atomicity and the capture loop.

Uses controlled fakes for sounddevice and sherpa-onnx (installed into
sys.modules), a scripted InputStream, and a fake VAD/recognizer, so every
failure path runs without a microphone or the real models.
"""

from __future__ import annotations

import io
import sys
import tarfile
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from voxtype.config import Config


# -- parakeet model install ----------------------------------------------


def _make_archive(dest: Path, files, content: bytes = b"model-bytes") -> None:
    import voxtype.engine_parakeet as ep

    with tarfile.open(dest, "w:bz2") as tf:
        for name in files:
            info = tarfile.TarInfo(f"{ep.MODEL_NAME}/{name}")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))


@pytest.fixture
def parakeet_cache(monkeypatch, tmp_path):  # noqa: ANN001, ANN201
    import voxtype.engine_parakeet as ep

    monkeypatch.setattr(ep, "CACHE_DIR", tmp_path)
    return ep, tmp_path


def _patch_download(monkeypatch, ep, behavior):  # noqa: ANN001, ANN202
    calls: list[str] = []

    def fake_download(url: str, dest: Path, status) -> None:  # noqa: ANN001
        calls.append(url)
        behavior(url, dest)

    monkeypatch.setattr(ep, "_download", fake_download)
    return calls


def _good_download(ep):  # noqa: ANN001, ANN202
    def behavior(url: str, dest: Path) -> None:
        if url == ep.MODEL_URL:
            _make_archive(dest, ep.MODEL_FILES)
        else:
            dest.write_bytes(b"vad-model")

    return behavior


def test_ensure_models_downloads_verifies_and_caches(
    monkeypatch, parakeet_cache
) -> None:
    ep, cache = parakeet_cache
    calls = _patch_download(monkeypatch, ep, _good_download(ep))
    model_dir, vad_path = ep.ensure_models(lambda m: None)
    for f in ep.MODEL_FILES:
        assert (model_dir / f).stat().st_size > 0
    assert vad_path.stat().st_size > 0
    assert calls == [ep.MODEL_URL, ep.VAD_URL]
    # No leftover temp install dirs.
    assert _stray_installs(cache) == []
    # Second call: cache is valid, nothing re-downloaded.
    ep.ensure_models(lambda m: None)
    assert calls == [ep.MODEL_URL, ep.VAD_URL]


def test_ensure_models_rejects_incomplete_archive(monkeypatch, parakeet_cache) -> None:
    ep, cache = parakeet_cache

    def behavior(url: str, dest: Path) -> None:
        _make_archive(dest, ep.MODEL_FILES[:-1])  # tokens.txt missing

    _patch_download(monkeypatch, ep, behavior)
    with pytest.raises(RuntimeError, match="expected files"):
        ep.ensure_models(lambda m: None)
    # Nothing was published: the final model dir must not exist.
    assert not (cache / ep.MODEL_NAME).exists()


def test_ensure_models_rejects_empty_artifacts(monkeypatch, parakeet_cache) -> None:
    ep, cache = parakeet_cache

    def behavior(url: str, dest: Path) -> None:
        _make_archive(dest, ep.MODEL_FILES, content=b"")  # truncated

    _patch_download(monkeypatch, ep, behavior)
    with pytest.raises(RuntimeError, match="expected files"):
        ep.ensure_models(lambda m: None)
    assert not (cache / ep.MODEL_NAME).exists()


def test_ensure_models_download_failure_no_partial_cache(
    monkeypatch, parakeet_cache
) -> None:
    ep, cache = parakeet_cache

    def behavior(url: str, dest: Path) -> None:
        raise OSError("network down")

    _patch_download(monkeypatch, ep, behavior)
    with pytest.raises(OSError):
        ep.ensure_models(lambda m: None)
    assert not (cache / ep.MODEL_NAME).exists()
    assert _stray_installs(cache) == []


def test_ensure_models_repairs_corrupt_cache(monkeypatch, parakeet_cache) -> None:
    ep, cache = parakeet_cache
    # A stale cache where one artifact is empty must NOT be accepted.
    stale = cache / ep.MODEL_NAME
    stale.mkdir(parents=True)
    for f in ep.MODEL_FILES:
        (stale / f).write_bytes(b"old")
    (stale / ep.MODEL_FILES[0]).write_bytes(b"")  # truncated encoder
    calls = _patch_download(monkeypatch, ep, _good_download(ep))
    model_dir, _ = ep.ensure_models(lambda m: None)
    assert ep.MODEL_URL in calls  # reinstall happened
    assert (model_dir / ep.MODEL_FILES[0]).stat().st_size > 0


def test_ensure_models_repairs_file_shaped_cache(
    monkeypatch, parakeet_cache
) -> None:
    """A cache entry that is a regular file is removed and replaced."""
    ep, cache = parakeet_cache
    (cache / ep.MODEL_NAME).write_bytes(b"not a directory")
    _patch_download(monkeypatch, ep, _good_download(ep))
    model_dir, _ = ep.ensure_models(lambda m: None)
    assert model_dir.is_dir() and not model_dir.is_symlink()
    for f in ep.MODEL_FILES:
        assert (model_dir / f).stat().st_size > 0


def test_ensure_models_repairs_symlink_shaped_cache(
    monkeypatch, parakeet_cache
) -> None:
    """A cache entry that is a directory symlink is unlinked, not rmtree'd."""
    ep, cache = parakeet_cache
    target = cache / "elsewhere"
    target.mkdir()
    (cache / ep.MODEL_NAME).symlink_to(target)
    _patch_download(monkeypatch, ep, _good_download(ep))
    model_dir, _ = ep.ensure_models(lambda m: None)
    assert model_dir.is_dir() and not model_dir.is_symlink()
    for f in ep.MODEL_FILES:
        assert (model_dir / f).stat().st_size > 0
    assert target.exists()  # the symlink target itself is untouched


def test_download_failure_leaves_no_temp_files(monkeypatch, parakeet_cache) -> None:
    ep, cache = parakeet_cache

    def bad_urlopen(url):  # noqa: ANN001, ANN202
        raise OSError("connection refused")

    monkeypatch.setattr(
        "urllib.request.urlopen", bad_urlopen
    )
    dest = cache / "silero_vad.onnx"
    with pytest.raises(OSError):
        ep._download(ep.VAD_URL, dest, lambda m: None)
    assert not dest.exists()
    assert list(cache.glob("*.part")) == []


def test_download_rejects_empty_response(monkeypatch, parakeet_cache) -> None:
    ep, cache = parakeet_cache
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda url: io.BytesIO(b"")
    )
    dest = cache / "silero_vad.onnx"
    with pytest.raises(RuntimeError, match="empty"):
        ep._download(ep.VAD_URL, dest, lambda m: None)
    assert not dest.exists()
    assert list(cache.glob("*.part")) == []


def test_install_extracts_without_tarfile_filter_support(
    monkeypatch, parakeet_cache
) -> None:
    """Pythons without tarfile.data_filter (3.11.0-3.11.3) still install."""
    ep, cache = parakeet_cache
    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    _patch_download(monkeypatch, ep, _good_download(ep))
    model_dir, vad_path = ep.ensure_models(lambda m: None)
    for f in ep.MODEL_FILES:
        assert (model_dir / f).stat().st_size > 0
    assert vad_path.stat().st_size > 0


def test_extract_archive_fallback_rejects_traversal(
    monkeypatch, tmp_path
) -> None:
    """The no-filter fallback refuses path-traversal archive members."""
    import voxtype.engine_parakeet as ep

    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    archive = tmp_path / "evil.tar.bz2"
    with tarfile.open(archive, "w:bz2") as tf:
        info = tarfile.TarInfo("../evil.txt")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"abc"))
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(RuntimeError, match="unsafe path"):
        ep._extract_archive(archive, dest)
    assert not (tmp_path / "evil.txt").exists()


def test_extract_archive_fallback_rejects_links(
    monkeypatch, tmp_path
) -> None:
    """The no-filter fallback refuses symlink members outright."""
    import voxtype.engine_parakeet as ep

    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    archive = tmp_path / "links.tar.bz2"
    with tarfile.open(archive, "w:bz2") as tf:
        info = tarfile.TarInfo("model/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(RuntimeError, match="unsupported member"):
        ep._extract_archive(archive, dest)


# -- parakeet model loading / cache self-repair ---------------------------


def _fake_sherpa(fail_loads: int = 0, window_size: int = 512):
    """Fake sherpa_onnx module; the first ``fail_loads`` loads raise."""
    calls = {"n": 0}
    sherpa = types.ModuleType("sherpa_onnx")

    class OfflineRecognizer:
        @staticmethod
        def from_transducer(**kwargs):  # noqa: ANN003, ANN205
            calls["n"] += 1
            if calls["n"] <= fail_loads:
                raise RuntimeError("corrupt model file")
            return FakeRecognizer(())

    class VadModelConfig:
        def __init__(self) -> None:
            self.silero_vad = SimpleNamespace(
                model="",
                min_silence_duration=0.0,
                window_size=window_size,
            )
            self.sample_rate = 0

    class VoiceActivityDetector:
        def __init__(self, cfg, buffer_size_in_seconds) -> None:  # noqa: ANN001
            pass

    sherpa.OfflineRecognizer = OfflineRecognizer
    sherpa.VadModelConfig = VadModelConfig
    sherpa.VoiceActivityDetector = VoiceActivityDetector
    return sherpa, calls


def test_parakeet_repairs_unloadable_cache(
    monkeypatch, parakeet_cache
) -> None:
    """Nonempty-but-corrupt cache: cleared and redownloaded once."""
    ep, cache = parakeet_cache
    downloads = _patch_download(monkeypatch, ep, _good_download(ep))
    eng, statuses, _ = _make_parakeet(monkeypatch)
    sherpa, loads = _fake_sherpa(fail_loads=1)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", sherpa)
    eng._load_models_with_repair()
    assert loads["n"] == 2  # failed once, succeeded after repair
    # Model and VAD were redownloaded after the quarantine.
    assert downloads.count(ep.MODEL_URL) == 2
    assert downloads.count(ep.VAD_URL) == 2
    assert eng._window_size == 512
    assert any("clearing cached" in s for s in statuses)


def test_parakeet_persistently_unloadable_cache_raises(
    monkeypatch, parakeet_cache
) -> None:
    ep, cache = parakeet_cache
    _patch_download(monkeypatch, ep, _good_download(ep))
    eng, _, _ = _make_parakeet(monkeypatch)
    sherpa, loads = _fake_sherpa(fail_loads=99)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", sherpa)
    with pytest.raises(RuntimeError, match="corrupt model"):
        eng._load_models_with_repair()
    assert loads["n"] == 2  # exactly one repair attempt, then give up


def test_parakeet_load_rejects_invalid_window_size(
    monkeypatch, parakeet_cache
) -> None:
    ep, cache = parakeet_cache
    _patch_download(monkeypatch, ep, _good_download(ep))
    eng, _, _ = _make_parakeet(monkeypatch)
    sherpa, _ = _fake_sherpa(window_size=0)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", sherpa)
    with pytest.raises(RuntimeError, match="window size"):
        eng._load_models_with_repair()


# -- parakeet capture loop -----------------------------------------------


class ScriptedStream:
    """InputStream fake driven by a scripted list of reads.

    Script items may be arrays (returned as data), Exceptions (raised),
    or callables (invoked; their return value is the data — handy for
    setting the stop flag from within a read).
    """

    def __init__(self, script, samplerate=16000) -> None:  # noqa: ANN001
        self.samplerate = samplerate
        self._script = list(script)
        self.aborted = self.closed = False

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc):  # noqa: ANN002, ANN204
        self.close()
        return False

    def read(self, n):  # noqa: ANN001, ANN201
        if not self._script:
            raise RuntimeError("script exhausted")
        item = self._script.pop(0)
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        return item, False

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        self.closed = True


class RawReadStream(ScriptedStream):
    """ScriptedStream whose read() returns script items verbatim.

    No ``(data, overflowed)`` wrapping: lets tests script junk-shaped
    return values such as a bare scalar or an empty tuple.
    """

    def read(self, n):  # noqa: ANN001, ANN201
        if not self._script:
            raise RuntimeError("script exhausted")
        item = self._script.pop(0)
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        return item


class BlockingStream:
    """InputStream fake whose read blocks until abort() is called."""

    def __init__(self) -> None:
        self.samplerate = 16000
        self.aborted = False
        self._unblock = threading.Event()

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc):  # noqa: ANN002, ANN204
        return False

    def read(self, n):  # noqa: ANN001, ANN201
        self._unblock.wait(timeout=10.0)
        raise RuntimeError("stream aborted")

    def abort(self) -> None:
        self.aborted = True
        self._unblock.set()

    def close(self) -> None:
        self._unblock.set()


class FakeVad:
    def __init__(self) -> None:
        self.windows: list = []
        self.queue: list = []
        self.speech = False

    def accept_waveform(self, window) -> None:  # noqa: ANN001
        self.windows.append(window)

    def is_speech_detected(self) -> bool:
        return self.speech

    def empty(self) -> bool:
        return not self.queue

    @property
    def front(self):  # noqa: ANN201
        return self.queue[0]

    def pop(self) -> None:
        self.queue.pop(0)


class FakeRecognizer:
    def __init__(self, texts) -> None:  # noqa: ANN001
        self._texts = list(texts)

    def create_stream(self):  # noqa: ANN201
        text = self._texts.pop(0) if self._texts else ""
        stream = SimpleNamespace(result=SimpleNamespace(text=text))
        stream.accept_waveform = lambda rate, samples: None
        return stream

    def decode_stream(self, stream) -> None:  # noqa: ANN001
        pass


def _stray_installs(cache: Path) -> list[Path]:
    return [p for p in cache.iterdir() if p.name.startswith(".install-")]


def _make_parakeet(monkeypatch, texts=("hello",)):  # noqa: ANN001, ANN202
    """Build a ParakeetEngine wired to fake sherpa/sounddevice modules.

    Returns (engine, statuses, holder); tests set holder["factory"] to
    control what sd.InputStream(**kwargs) does per call.
    """
    pytest.importorskip("numpy")
    holder: dict = {"calls": []}

    sd_mod = types.ModuleType("sounddevice")

    class PortAudioError(Exception):
        pass

    sd_mod.PortAudioError = PortAudioError

    def input_stream(**kwargs):  # noqa: ANN003, ANN202
        holder["calls"].append(kwargs)
        return holder["factory"](**kwargs)

    sd_mod.InputStream = input_stream
    monkeypatch.setitem(sys.modules, "sounddevice", sd_mod)
    monkeypatch.setitem(
        sys.modules, "sherpa_onnx", types.ModuleType("sherpa_onnx")
    )

    from voxtype.engine_parakeet import (
        ParakeetEngine,
    )

    statuses: list[str] = []
    eng = ParakeetEngine(
        Config(engine="parakeet"), statuses.append
    )
    eng._recognizer = FakeRecognizer(texts)
    eng._vad = FakeVad()
    eng._window_size = 160
    eng.RETRY_DELAY = 0.01
    return eng, statuses, holder


def test_parakeet_loop_transcribes_and_survives_bad_values(monkeypatch) -> None:
    np = pytest.importorskip("numpy")
    eng, statuses, holder = _make_parakeet(monkeypatch)
    vad = eng._vad
    vad.speech = True
    vad.queue.extend(
        [
            SimpleNamespace(samples=np.ones(100, dtype=np.float32)),
            None,  # null VAD front tolerated
            SimpleNamespace(samples=None),  # null samples tolerated
            SimpleNamespace(samples=3.14),  # unsized scalar tolerated
            SimpleNamespace(samples="junk"),  # non-numeric tolerated
            SimpleNamespace(  # empty segment tolerated
                samples=np.zeros(0, dtype=np.float32)
            ),
            SimpleNamespace(  # wrong-shaped segment tolerated
                samples=np.ones((4, 2), dtype=np.float32)
            ),
        ]
    )
    utterances: list[str] = []
    activity = {"n": 0}

    def on_activity() -> None:
        activity["n"] += 1
        raise ValueError("activity callback exploded")

    def on_utterance(text: str) -> None:
        utterances.append(text)
        raise RuntimeError("utterance callback exploded")

    chunk = np.ones((1600, 1), dtype=np.float32)

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    stream = ScriptedStream(
        [chunk, None, np.zeros((0, 1), dtype=np.float32), final_read]
    )
    holder["factory"] = lambda **kw: stream
    eng._loop(on_utterance, on_activity)  # must return, not raise

    assert utterances == ["hello"]
    assert activity["n"] >= 1
    assert len(vad.windows) == 10  # 1600 samples in 160-sample windows
    assert all(len(w) == 160 for w in vad.windows)
    assert any("on_utterance callback error" in s for s in statuses)
    assert any("on_activity callback error" in s for s in statuses)
    # Malformed segments never escape the drain and restart capture.
    assert not any("capture error" in s for s in statuses)
    assert eng.fatal_error is None
    assert eng._stream is None  # stream reference released


def test_parakeet_read_junk_shapes_then_valid_audio(monkeypatch) -> None:
    """Empty tuples, scalars, 3-D and non-numeric reads are unusable —
    never crashes — and real audio afterwards is still processed."""
    np = pytest.importorskip("numpy")
    eng, statuses, holder = _make_parakeet(monkeypatch)
    eng._vad.queue.append(
        SimpleNamespace(samples=np.ones(100, dtype=np.float32))
    )
    chunk = np.ones((1600, 1), dtype=np.float32)

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return (np.zeros((0, 1), dtype=np.float32), False)

    stream = RawReadStream(
        [
            (),  # read() returned an empty tuple
            np.float32(0.5),  # bare scalar return
            (np.float32(0.5), False),  # scalar data inside the tuple
            (np.ones((4, 2, 2), dtype=np.float32), False),  # 3-D data
            ("junk", False),  # non-numeric data
            (np.ones((3, 0), dtype=np.float32), False),  # zero channels
            (chunk, False),  # then real audio
            final_read,
        ]
    )
    holder["factory"] = lambda **kw: stream
    utterances: list[str] = []
    eng._loop(utterances.append, lambda: None)
    assert utterances == ["hello"]
    assert eng.fatal_error is None
    # Malformed reads never escaped and restarted the capture session.
    assert not any("capture error" in s for s in statuses)
    assert len(eng._vad.windows) == 10  # the real chunk got processed


def test_parakeet_persistent_malformed_reads_go_fatal(monkeypatch) -> None:
    """A stream that only ever returns malformed data must go fatal.

    Regression: a nonempty-but-junk chunk used to mark the session as
    having made progress, resetting the consecutive-failure counter on
    every retry so persistent malformed capture retried forever.
    """
    np = pytest.importorskip("numpy")
    eng, _, holder = _make_parakeet(monkeypatch)
    eng.MAX_CONSECUTIVE_FAILURES = 3
    eng.MAX_EMPTY_READS = 4
    holder["factory"] = lambda **kw: RawReadStream(
        [np.float32(0.5)] * 10
    )
    eng._loop(lambda t: None, lambda: None)  # returns instead of looping
    assert eng.fatal_error is not None
    assert "no usable audio" in eng.fatal_error


def test_parakeet_persistent_empty_reads_restart_and_go_fatal(
    monkeypatch,
) -> None:
    """None/empty reads must not spin forever while looking healthy:
    the session restarts with backoff and eventually goes fatal."""
    np = pytest.importorskip("numpy")
    eng, statuses, holder = _make_parakeet(monkeypatch)
    eng.MAX_CONSECUTIVE_FAILURES = 2
    eng.MAX_EMPTY_READS = 3
    holder["factory"] = lambda **kw: ScriptedStream(
        [None, np.zeros((0, 1), dtype=np.float32), None]
    )
    eng._loop(lambda t: None, lambda: None)
    assert eng.fatal_error is not None
    assert "no usable audio" in eng.fatal_error
    assert any("retrying" in s for s in statuses)


def test_parakeet_empty_read_counter_resets_on_valid_audio(
    monkeypatch,
) -> None:
    """Interspersed unusable reads below the cap stay recoverable."""
    np = pytest.importorskip("numpy")
    eng, statuses, holder = _make_parakeet(monkeypatch, texts=())
    eng.MAX_EMPTY_READS = 3
    data = np.ones((160, 1), dtype=np.float32)

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    holder["factory"] = lambda **kw: ScriptedStream(
        [None, None, data, None, None, data, final_read]
    )
    eng._loop(lambda t: None, lambda: None)
    assert eng.fatal_error is None
    assert not any("capture error" in s for s in statuses)
    assert len(eng._vad.windows) == 2  # both real chunks were processed


def test_parakeet_loop_falls_back_to_native_rate_and_resamples(monkeypatch) -> None:
    np = pytest.importorskip("numpy")
    eng, _, holder = _make_parakeet(monkeypatch, texts=())
    eng_stop = eng._stop

    def final_read():  # noqa: ANN202
        # Stop is honored everywhere (including the window-draining
        # loop), so the data chunk arrives in its own earlier read.
        eng_stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    native = ScriptedStream(
        [np.ones((3200, 1), dtype=np.float32), final_read],
        samplerate=32000,
    )

    def factory(**kwargs):  # noqa: ANN003, ANN202
        if "samplerate" in kwargs:
            raise sys.modules["sounddevice"].PortAudioError("no 16k")
        return native

    holder["factory"] = factory
    eng._loop(lambda t: None, lambda: None)
    # 3200 samples at 32 kHz resample to 1600 at 16 kHz -> 10 windows.
    assert len(eng._vad.windows) == 10
    assert holder["calls"][0].get("samplerate") == 16000
    assert "samplerate" not in holder["calls"][1]


def test_parakeet_loop_retries_after_capture_error(monkeypatch) -> None:  # noqa: ANN001
    np = pytest.importorskip("numpy")
    eng, statuses, holder = _make_parakeet(monkeypatch, texts=("ok",))
    attempts = {"n": 0}

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    data = np.ones((160, 1), dtype=np.float32)

    def factory(**kwargs):  # noqa: ANN003, ANN202
        attempts["n"] += 1
        if attempts["n"] <= 2:  # both open attempts of session 1 fail
            raise RuntimeError("device busy")
        return ScriptedStream([data, final_read])

    holder["factory"] = factory
    utterances: list[str] = []
    eng._vad.queue.append(
        SimpleNamespace(samples=np.ones(100, dtype=np.float32))
    )
    eng._loop(utterances.append, lambda: None)
    assert utterances == ["ok"]
    assert eng.fatal_error is None
    assert any("retrying" in s for s in statuses)


def test_parakeet_separated_failures_do_not_accumulate(monkeypatch) -> None:
    """Errors separated by successful capture never reach the fatal limit.

    Regression test: the consecutive-failure counter must reset once a
    session has produced audio, so isolated recoverable mic errors over
    the engine's lifetime (here 5 of them, limit 3) stay recoverable.
    """
    np = pytest.importorskip("numpy")
    eng, statuses, holder = _make_parakeet(monkeypatch, texts=())
    eng.MAX_CONSECUTIVE_FAILURES = 3
    data = np.ones((160, 1), dtype=np.float32)
    sessions = {"n": 0}

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    def factory(**kwargs):  # noqa: ANN003, ANN202
        sessions["n"] += 1
        if sessions["n"] <= 5:  # capture succeeds, then the mic errors
            return ScriptedStream([data, RuntimeError("mic glitch")])
        return ScriptedStream([data, final_read])

    holder["factory"] = factory
    eng._loop(lambda t: None, lambda: None)
    assert eng.fatal_error is None  # 5 isolated errors < 3 consecutive
    assert sessions["n"] == 6
    assert sum("retrying" in s for s in statuses) == 5


def test_parakeet_failures_without_capture_still_go_fatal_after_success(
    monkeypatch,
) -> None:
    """After a successful session, failures with no captured audio still
    accumulate to the fatal limit (the reset needs real progress)."""
    np = pytest.importorskip("numpy")
    eng, _, holder = _make_parakeet(monkeypatch, texts=())
    eng.MAX_CONSECUTIVE_FAILURES = 3
    data = np.ones((160, 1), dtype=np.float32)
    sessions = {"n": 0}

    def factory(**kwargs):  # noqa: ANN003, ANN202
        sessions["n"] += 1
        if sessions["n"] == 1:  # one good capture, then the mic dies
            return ScriptedStream([data, RuntimeError("mic died")])
        raise RuntimeError("no microphone")

    holder["factory"] = factory
    eng._loop(lambda t: None, lambda: None)
    assert eng.fatal_error is not None
    assert "no microphone" in eng.fatal_error


def test_parakeet_loop_goes_fatal_after_persistent_failure(monkeypatch) -> None:
    eng, statuses, holder = _make_parakeet(monkeypatch)
    eng.MAX_CONSECUTIVE_FAILURES = 3

    def factory(**kwargs):  # noqa: ANN003, ANN202
        raise RuntimeError("no microphone")

    holder["factory"] = factory
    eng._loop(lambda t: None, lambda: None)  # returns instead of hanging
    assert eng.fatal_error is not None
    assert "no microphone" in eng.fatal_error


@pytest.mark.parametrize("bad_window", [0, -1, None, 2.5, True])
def test_parakeet_loop_invalid_window_size_goes_fatal(
    monkeypatch, bad_window
) -> None:  # noqa: ANN001
    """A zero/bogus window size must fail fast, never spin the loop."""
    np = pytest.importorskip("numpy")
    eng, _, holder = _make_parakeet(monkeypatch)
    eng.MAX_CONSECUTIVE_FAILURES = 2
    eng._window_size = bad_window
    data = np.ones((160, 1), dtype=np.float32)
    holder["factory"] = lambda **kw: ScriptedStream([data] * 5)
    eng._loop(lambda t: None, lambda: None)  # returns instead of hanging
    assert eng.fatal_error is not None
    assert "window size" in eng.fatal_error


def test_parakeet_loop_rejects_invalid_sample_rate(monkeypatch) -> None:  # noqa: ANN001
    eng, statuses, holder = _make_parakeet(monkeypatch)
    eng.MAX_CONSECUTIVE_FAILURES = 2
    holder["factory"] = lambda **kw: ScriptedStream([], samplerate=0)
    eng._loop(lambda t: None, lambda: None)
    assert eng.fatal_error is not None
    assert "sample rate" in eng.fatal_error


def test_parakeet_stop_aborts_blocked_read_and_joins(monkeypatch) -> None:
    eng, _, holder = _make_parakeet(monkeypatch)
    stream = BlockingStream()
    holder["factory"] = lambda **kw: stream
    worker = threading.Thread(
        target=eng._loop, args=(lambda t: None, lambda: None), daemon=True
    )
    worker.start()
    eng._thread = worker
    for _ in range(500):  # wait for the worker to open the stream
        if eng._stream is not None:
            break
        time.sleep(0.01)
    assert eng._stream is not None
    eng.stop()
    assert stream.aborted
    assert not worker.is_alive()
    assert eng._thread is None  # joined worker is released


def test_parakeet_no_callbacks_after_stop(monkeypatch) -> None:  # noqa: ANN001
    eng, _, _ = _make_parakeet(monkeypatch)
    called = {"n": 0}
    eng._stop.set()
    eng._safe_call(lambda: called.__setitem__("n", 1), "on_utterance")
    assert called["n"] == 0  # suppressed once shutdown began


# -- AutoGain -------------------------------------------------------------


def test_autogain_amplifies_quiet_audio() -> None:
    np = pytest.importorskip("numpy")
    from voxtype.engine_parakeet import AutoGain

    agc = AutoGain()
    quiet = (0.005 * np.sin(np.linspace(0, 200, 1600))).astype(np.float32)
    out = quiet
    for _ in range(60):  # ~6s of chunks; gain converges
        out = agc.process(quiet)
    assert np.abs(out).max() > 0.1  # brought toward TARGET_PEAK
    assert np.abs(out).max() <= 1.0


def test_autogain_leaves_normal_audio_alone() -> None:
    np = pytest.importorskip("numpy")
    from voxtype.engine_parakeet import AutoGain

    agc = AutoGain()
    loud = (0.5 * np.sin(np.linspace(0, 200, 1600))).astype(np.float32)
    for _ in range(20):
        out = agc.process(loud)
    assert np.allclose(out, loud)  # gain stays at 1.0


def test_autogain_handles_silence_and_empty() -> None:
    np = pytest.importorskip("numpy")
    from voxtype.engine_parakeet import AutoGain

    agc = AutoGain()
    for _ in range(10):
        out = agc.process(np.zeros(1600, dtype=np.float32))
    assert np.abs(out).max() <= 1.0  # no blow-up on silence
    agc.process(np.zeros(0, dtype=np.float32))  # empty chunk is safe


# -- segment drain order (front must be read BEFORE pop) ------------------


class _PopInvalidatingVad:
    """Mimics sherpa-onnx: front becomes invalid once pop() is called."""

    class _Front:
        def __init__(self, samples):  # noqa: ANN001
            self.samples = samples

    def __init__(self, segments):  # noqa: ANN001
        self._segments = list(segments)
        self._front = None

    def empty(self) -> bool:
        return not self._segments and self._front is None

    @property
    def front(self):  # noqa: ANN202
        if self._front is None and self._segments:
            self._front = self._Front(self._segments.pop(0))
        return self._front

    def pop(self) -> None:
        if self._front is not None:
            self._front.samples = []  # invalidate, like the native queue
            self._front = None


def test_drain_reads_segment_before_pop() -> None:
    np = pytest.importorskip("numpy")
    pytest.importorskip("sherpa_onnx")
    from voxtype.config import Config
    from voxtype.engine_parakeet import ParakeetEngine

    eng = ParakeetEngine(Config(engine="parakeet"), lambda m: None)
    eng._vad = _PopInvalidatingVad(
        [np.ones(1600, dtype=np.float32)]
    )
    eng.transcribe = lambda samples, sr: f"len={len(samples)}"  # type: ignore

    got: list[str] = []
    eng._drain_segments(got.append)
    # If the drain popped before copying, the segment would be empty and
    # silently skipped; reading first yields the real 1600 samples.
    assert got == ["len=1600"]


# -- hold-mode robustness -------------------------------------------------


def _hold_engine(monkeypatch):  # noqa: ANN001, ANN202
    eng, statuses, holder = _make_parakeet(monkeypatch, texts=())
    eng.cfg = Config(
        mode="toggle", engine="parakeet", segmentation="hold"
    )
    eng.transcribe = (  # type: ignore[method-assign]
        lambda samples, sr: f"len={len(samples)}"
    )
    return eng, statuses, holder


def test_parakeet_hold_take_survives_capture_retry(monkeypatch) -> None:
    """A recoverable stream failure mid-take must not discard the hold
    buffer: hold-stop still delivers everything captured so far."""
    np = pytest.importorskip("numpy")
    eng, statuses, holder = _hold_engine(monkeypatch)
    data = np.ones((160, 1), dtype=np.float32)
    sessions = {"n": 0}

    def queue_stop_then_finish():  # noqa: ANN202
        eng.request_hold_stop()
        return np.zeros((0, 1), dtype=np.float32)

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    def factory(**kwargs):  # noqa: ANN003, ANN202
        sessions["n"] += 1
        if sessions["n"] == 1:  # take begins, then the stream hiccups
            return ScriptedStream([data, RuntimeError("mic glitch")])
        return ScriptedStream([data, queue_stop_then_finish, final_read])

    holder["factory"] = factory
    eng.request_hold_start()
    utterances: list[str] = []
    eng._loop(utterances.append, lambda: None)
    # Both chunks (before AND after the retry) are in the take.
    assert utterances == ["len=320"]
    assert any("retrying" in s for s in statuses)


def test_parakeet_rapid_hold_stop_then_start_keeps_take(
    monkeypatch,
) -> None:
    """Commands apply in order: a hold-stop immediately followed by a
    new hold-start still delivers the finished take (regression for
    the old Event flags, where start cleared a pending stop)."""
    np = pytest.importorskip("numpy")
    eng, _, holder = _hold_engine(monkeypatch)
    data = np.ones((160, 1), dtype=np.float32)

    def stop_then_start():  # noqa: ANN202
        eng.request_hold_stop()
        eng.request_hold_start()
        return np.zeros((0, 1), dtype=np.float32)

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    holder["factory"] = lambda **kw: ScriptedStream(
        [data, stop_then_start, data, final_read]
    )
    eng.request_hold_start()
    utterances: list[str] = []
    eng._loop(utterances.append, lambda: None)
    assert utterances == ["len=160"]
    assert eng._holding  # the new take is recording
    assert eng._hold_len == 160  # ...and captured the post-start chunk


def test_parakeet_hold_start_then_cancel_stays_cancelled(
    monkeypatch,
) -> None:
    """A reset queued after hold-start must be processed after it:
    the recording ends up discarded, never resurrected."""
    np = pytest.importorskip("numpy")
    eng, _, holder = _hold_engine(monkeypatch)
    data = np.ones((160, 1), dtype=np.float32)

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    holder["factory"] = lambda **kw: ScriptedStream([data, final_read])
    eng.request_hold_start()
    eng.request_reset()  # cancel right after starting
    utterances: list[str] = []
    eng._loop(utterances.append, lambda: None)
    assert not eng._holding
    assert eng._hold_len == 0
    eng.request_hold_stop()
    assert utterances == []


def test_parakeet_empty_hold_decode_is_reported(monkeypatch) -> None:
    """A hold take decoded to empty text must log its outcome (same
    contract as the VAD drain), never vanish silently."""
    np = pytest.importorskip("numpy")
    eng, statuses, _ = _hold_engine(monkeypatch)
    eng.transcribe = lambda samples, sr: ""  # type: ignore[method-assign]
    got: list[str] = []
    eng._deliver_take(np.ones(1600, dtype=np.float32), got.append)
    assert got == []
    assert any("decoded to empty" in s for s in statuses)


def test_parakeet_hold_cap_bounds_oversized_reads(monkeypatch) -> None:
    """The hold memory cap is enforced per-sample: even one chunk
    larger than the remaining budget cannot blow past the cap."""
    np = pytest.importorskip("numpy")
    from voxtype import engine_parakeet as ep

    eng, statuses, holder = _hold_engine(monkeypatch)
    monkeypatch.setattr(ep, "MAX_HOLD_SECONDS", 1)  # cap = 16000 samples
    big = np.ones((12000, 1), dtype=np.float32)

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    holder["factory"] = lambda **kw: ScriptedStream(
        [big, big, big, final_read]
    )
    eng.request_hold_start()
    eng._loop(lambda t: None, lambda: None)
    assert eng._hold_len == 16000  # exactly the cap, not 24000+
    assert any("capped" in s for s in statuses)


def test_parakeet_hold_takes_survive_slow_decode(monkeypatch) -> None:
    """stop/start/stop while the first take is still decoding must
    deliver BOTH takes, in order: decoding runs off the capture
    thread, so audio spoken during a slow decode keeps flowing into
    the second take's buffer instead of being stopped away empty."""
    np = pytest.importorskip("numpy")
    eng, _, holder = _hold_engine(monkeypatch)
    first_decode_started = threading.Event()
    release_decode = threading.Event()

    def slow_transcribe(samples, sr):  # noqa: ANN001, ANN202
        if not first_decode_started.is_set():
            first_decode_started.set()
            assert release_decode.wait(timeout=10.0)
        return f"len={len(samples)}"

    eng.transcribe = slow_transcribe  # type: ignore[method-assign]
    data = np.ones((160, 1), dtype=np.float32)
    empty = np.zeros((0, 1), dtype=np.float32)

    def stop_first_take():  # noqa: ANN202
        eng.request_hold_stop()
        return empty

    def toggle_on_mid_decode():  # noqa: ANN202
        # The first take is being decoded RIGHT NOW; the user toggles
        # a new recording on while it runs.
        assert first_decode_started.wait(timeout=10.0)
        eng.request_hold_start()
        return empty

    def stop_second_take():  # noqa: ANN202
        eng.request_hold_stop()
        release_decode.set()  # first decode finishes only now
        return empty

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return empty

    holder["factory"] = lambda **kw: ScriptedStream(
        [
            data,  # first take: one chunk
            stop_first_take,
            toggle_on_mid_decode,
            data,  # second take: two chunks, read DURING the decode
            data,
            stop_second_take,
            final_read,
        ]
    )
    eng.request_hold_start()
    utterances: list[str] = []
    eng._loop(utterances.append, lambda: None)
    assert utterances == ["len=160", "len=320"]


def test_parakeet_start_preserves_commands_queued_during_load(
    monkeypatch,
) -> None:
    """A toggle pressed during the (possibly minutes-long) first-run
    model download queues engine commands; start() must preserve
    them for the worker, not clear them after loading."""
    np = pytest.importorskip("numpy")
    eng, _, holder = _hold_engine(monkeypatch)
    monkeypatch.setattr(
        eng,
        "_load_models_with_repair",
        lambda: eng.request_hold_start(),  # toggle lands mid-download
    )

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    holder["factory"] = lambda **kw: ScriptedStream([final_read])
    eng.start(lambda t: None, lambda: None)
    eng._thread.join(timeout=5.0)
    assert not eng._thread.is_alive()
    assert eng._holding  # the mid-download toggle was honored


# -- hostile audio data ---------------------------------------------------


def test_parakeet_rejects_nonfinite_and_huge_reads(monkeypatch) -> None:
    np = pytest.importorskip("numpy")
    eng, _, holder = _make_parakeet(monkeypatch, texts=())
    nan_chunk = np.full((160, 1), np.nan, dtype=np.float32)
    inf_chunk = np.full((160, 1), np.inf, dtype=np.float32)
    huge = np.ones((16000 * 31, 1), dtype=np.float32)  # >30 s in one read
    good = np.ones((160, 1), dtype=np.float32)

    def final_read():  # noqa: ANN202
        eng._stop.set()
        return np.zeros((0, 1), dtype=np.float32)

    holder["factory"] = lambda **kw: ScriptedStream(
        [nan_chunk, inf_chunk, huge, good, final_read]
    )
    eng._loop(lambda t: None, lambda: None)
    assert eng.fatal_error is None
    # Only the finite, plausible chunk reached the VAD.
    assert len(eng._vad.windows) == 1
    assert all(np.isfinite(w).all() for w in eng._vad.windows)


def test_autogain_output_is_finite_and_clipped_for_hostile_input() -> None:
    np = pytest.importorskip("numpy")
    from voxtype.engine_parakeet import AutoGain

    agc = AutoGain()
    hostile = np.array(
        [np.nan, np.inf, -np.inf, 5.0, -5.0, 0.1], dtype=np.float32
    )
    for _ in range(5):
        out = agc.process(hostile)
        assert np.isfinite(out).all()
        assert np.abs(out).max() <= 1.0


def test_report_decode_time_formats(monkeypatch) -> None:
    pytest.importorskip("sherpa_onnx")
    from voxtype.config import Config
    from voxtype.engine_parakeet import ParakeetEngine

    msgs: list[str] = []
    eng = ParakeetEngine(Config(engine="parakeet"), msgs.append)
    eng._report_decode_time(4.0, 0.2)
    assert msgs[-1] == "transcribed 4.0s of audio in 0.20s (20x realtime)"
    eng._report_decode_time(1.0, 0.0)  # never divides by zero
    assert msgs[-1] == "transcribed 1.0s of audio in 0.00s"
