"""Parakeet-TDT engine: NVIDIA Parakeet v3 via sherpa-onnx.

Pipeline: sounddevice mic capture -> Silero VAD (segments utterances on
silence) -> sherpa-onnx offline transducer decode of each segment. Both
models run fully on-device; files are auto-downloaded on first use from
the k2-fsa/sherpa-onnx release assets into ``~/.cache/voice-type/``.

Requires the ``voice-parakeet`` extra (sherpa-onnx, sounddevice, numpy).
"""

from __future__ import annotations

import contextlib
import shutil
import tarfile
import tempfile
import threading
import urllib.request
import warnings
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import Config
from .engines import StatusFn

CACHE_DIR = Path.home() / ".cache" / "voice-type"
_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
MODEL_NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
MODEL_URL = f"{_RELEASE}/{MODEL_NAME}.tar.bz2"
VAD_URL = f"{_RELEASE}/silero_vad.onnx"
MODEL_FILES = (
    "encoder.int8.onnx",
    "decoder.int8.onnx",
    "joiner.int8.onnx",
    "tokens.txt",
)
SAMPLE_RATE = 16000
# Seconds of trailing silence that closes an utterance.
MIN_SILENCE = 0.5


class AutoGain:
    """Adaptive software gain so quiet mics reach VAD-friendly levels.

    Dynamic microphones (e.g. Shure MV7) can deliver peaks around 0.005
    full scale — far below Silero VAD's detection range, so speech is
    never segmented. This tracks the running peak with slow decay and
    scales chunks toward ``TARGET_PEAK``. Gain never attenuates
    (min 1.0), is capped at ``MAX_GAIN``, and moves gradually to avoid
    pumping. Output is clipped to [-1, 1].
    """

    TARGET_PEAK = 0.3
    MAX_GAIN = 100.0
    # Running-peak attack is instantaneous (max); release decays fast
    # enough to adapt to a quiet mic within a few seconds of audio.
    DECAY = 0.85  # per ~0.1 s chunk
    SMOOTHING = 0.3  # fraction of the way to the target gain per chunk

    def __init__(self) -> None:
        self._running_peak = self.TARGET_PEAK  # start at gain 1.0
        self._gain = 1.0

    def process(self, samples):  # noqa: ANN001, ANN202
        """Return ``samples`` scaled toward the target peak level."""
        import numpy as np

        peak = float(np.abs(samples).max()) if samples.size else 0.0
        self._running_peak = max(peak, self._running_peak * self.DECAY)
        target_gain = min(
            self.MAX_GAIN,
            max(1.0, self.TARGET_PEAK / max(self._running_peak, 1e-6)),
        )
        self._gain += self.SMOOTHING * (target_gain - self._gain)
        if self._gain <= 1.001:
            return samples
        return np.clip(samples * self._gain, -1.0, 1.0).astype(np.float32)


def _download(url: str, dest: Path, status: StatusFn) -> None:
    """Download ``url`` to ``dest`` atomically via a unique temp file.

    The temp file name is unique per process (``mkstemp``), so concurrent
    downloaders never write to the same path; whoever finishes publishes
    with an atomic rename.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    status(f"downloading {url.rsplit('/', 1)[-1]} ...")
    fd, tmp_name = tempfile.mkstemp(
        dir=dest.parent, prefix=dest.name + ".", suffix=".part"
    )
    tmp = Path(tmp_name)
    try:
        with open(fd, "wb") as f, urllib.request.urlopen(url) as resp:
            shutil.copyfileobj(resp, f)
        if tmp.stat().st_size == 0:
            raise RuntimeError(f"downloaded file is empty: {url}")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def _file_ok(path: Path) -> bool:
    """True if ``path`` is an existing, nonempty, readable regular file."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _model_cache_valid(model_dir: Path) -> bool:
    """True if every expected model artifact is present and nonempty."""
    return all(_file_ok(model_dir / f) for f in MODEL_FILES)


def _remove_cache_entry(path: Path) -> None:
    """Remove a cache entry whatever its shape (file, symlink, or dir).

    ``shutil.rmtree`` rejects regular files and directory symlinks, so
    a malformed cache entry of the wrong shape must be unlinked instead
    — otherwise every launch would redownload and then fail to repair.
    Missing paths are a no-op. Callers hold the install lock.
    """
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


@contextlib.contextmanager
def _install_lock() -> Iterator[None]:
    """Serialize cache installation across processes (flock on POSIX)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = CACHE_DIR / ".install.lock"
    with open(lock_path, "w") as f:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX fallback
            fcntl = None  # type: ignore[assignment]
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _extract_archive(archive: Path, dest: Path) -> None:
    """Extract a model archive safely on every supported Python.

    Uses the stdlib ``data`` extraction filter where available.
    Python 3.11.0-3.11.3 predate extraction-filter support (added in
    3.11.4), where passing ``filter=`` raises ``TypeError``; detect
    that via ``tarfile.data_filter`` and fall back to validating each
    member ourselves (no traversal, no links/devices) before
    extracting.
    """
    with tarfile.open(archive, "r:bz2") as tf:
        if hasattr(tarfile, "data_filter"):
            tf.extractall(dest, filter="data")
            return
        base = dest.resolve()
        for member in tf.getmembers():
            if not (member.isreg() or member.isdir()):
                raise RuntimeError(
                    "unsupported member type in model archive: "
                    f"{member.name!r}"
                )
            target = (base / member.name).resolve()
            if target != base and not target.is_relative_to(base):
                raise RuntimeError(
                    f"unsafe path in model archive: {member.name!r}"
                )
        with warnings.catch_warnings():
            # Members were validated above; on filter-capable Pythons
            # this branch only runs in tests, where the no-filter call
            # emits a DeprecationWarning.
            warnings.simplefilter("ignore", DeprecationWarning)
            tf.extractall(dest)


def _install_model(model_dir: Path, status: StatusFn) -> None:
    """Download + extract the model into a temp dir, verify, then publish.

    Extraction happens in a unique temporary directory under the cache
    root; only a fully verified model directory is atomically renamed
    into its final location, so an interrupted install can never leave a
    partial cache that a later run mistakes for a valid one.
    """
    status("Parakeet model not cached; downloading ~490 MB (one time)")
    with tempfile.TemporaryDirectory(
        dir=CACHE_DIR, prefix=".install-"
    ) as td:
        tmp_dir = Path(td)
        archive = tmp_dir / f"{MODEL_NAME}.tar.bz2"
        _download(MODEL_URL, archive, status)
        status("extracting model...")
        _extract_archive(archive, tmp_dir)
        extracted = tmp_dir / MODEL_NAME
        if not _model_cache_valid(extracted):
            missing = [
                f for f in MODEL_FILES if not _file_ok(extracted / f)
            ]
            raise RuntimeError(
                "model archive did not contain expected files "
                f"(missing/empty): {missing}"
            )
        # A stale/invalid cache entry may be a directory, a regular
        # file, or a symlink; we hold the install lock, so removing it
        # by shape and replacing it is safe.
        _remove_cache_entry(model_dir)
        extracted.replace(model_dir)


def ensure_models(status: StatusFn) -> tuple[Path, Path]:
    """Ensure the Parakeet model and Silero VAD are cached locally.

    Validates that every artifact is present and nonempty (mere existence
    is not enough), serializes installation across concurrent processes,
    and only ever publishes fully verified files.

    Returns:
        (model_dir, vad_model_path)
    """
    model_dir = CACHE_DIR / MODEL_NAME
    if not _model_cache_valid(model_dir):
        with _install_lock():
            if not _model_cache_valid(model_dir):
                _install_model(model_dir, status)
    vad_path = CACHE_DIR / "silero_vad.onnx"
    if not _file_ok(vad_path):
        with _install_lock():
            if not _file_ok(vad_path):
                _remove_cache_entry(vad_path)
                _download(VAD_URL, vad_path, status)
    return model_dir, vad_path


class ParakeetEngine:
    """Mic -> Silero VAD -> Parakeet-TDT v3, all local via sherpa-onnx.

    The capture worker survives recoverable failures (device errors,
    malformed reads, VAD/decoder hiccups, raising callbacks): each is
    reported and capture retries. Only persistent capture failure sets
    ``fatal_error``, which the app polls to shut down cleanly.
    """

    # Delay between capture retries after a recoverable failure.
    RETRY_DELAY = 1.0
    # Consecutive capture failures before giving up for good.
    MAX_CONSECUTIVE_FAILURES = 10
    # Consecutive unusable reads (None/empty/malformed) before the
    # capture session is declared broken and restarted with backoff.
    # A healthy blocking stream returns real audio (even for silence),
    # so a long run of unusable reads means the device is defunct; the
    # cap also prevents a CPU spin when such reads return immediately.
    MAX_EMPTY_READS = 100

    def __init__(self, cfg: Config, status: StatusFn) -> None:
        try:
            import numpy  # noqa: F401
            import sherpa_onnx  # noqa: F401
            import sounddevice  # noqa: F401
        except ImportError as e:
            raise ImportError(
                f"{e}. The parakeet engine needs the voice-parakeet "
                'extra: uv tool install "claude-code-tools[voice-parakeet]"'
            ) from e
        self.cfg = cfg
        self._status = status
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream_lock = threading.Lock()
        self._stream: Any = None
        # True once the current capture session has produced audio.
        self._capture_progress = False
        self._agc = AutoGain()
        # Cross-thread requests, honored by the capture worker (the VAD
        # is only ever touched from that thread).
        self._flush_req = threading.Event()
        self._reset_req = threading.Event()
        self.fatal_error: str | None = None

    def request_flush(self) -> None:
        """Ask the worker to finalize the in-flight VAD segment now.

        Used on toggle-off so speech finished just before the toggle is
        still delivered instead of waiting for trailing silence.
        """
        self._flush_req.set()

    def request_reset(self) -> None:
        """Ask the worker to drop buffered audio and reset the VAD.

        Used on toggle-on so stale pre-activation audio never leaks
        into the first dictated utterance.
        """
        self._reset_req.set()

    def start(
        self,
        on_utterance: Callable[[str], None],
        on_activity: Callable[[], None],
    ) -> None:
        self._load_models_with_repair()
        self.fatal_error = None
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            args=(on_utterance, on_activity),
            name="ParakeetEngine",
            daemon=True,
        )
        self._thread.start()

    def _load_models_with_repair(self) -> None:
        """Load recognizer + VAD, self-repairing an unloadable cache.

        A nonempty-but-corrupt archive or Silero download passes the
        size checks yet fails to load. On the first load failure the
        cached artifacts are removed (under the install lock) and
        redownloaded once; a second failure propagates to the caller.
        The cache is therefore never left in a state that fails on
        every subsequent run without triggering a repair.
        """
        for attempt in (1, 2):
            model_dir, vad_path = ensure_models(self._status)
            try:
                self._load_models(model_dir, vad_path)
                return
            except Exception as e:
                if attempt == 2:
                    raise
                self._report(
                    f"model load failed ({e}); clearing cached "
                    "models and redownloading"
                )
                with _install_lock():
                    _remove_cache_entry(model_dir)
                    _remove_cache_entry(vad_path)

    def _load_models(self, model_dir: Path, vad_path: Path) -> None:
        """Construct the sherpa-onnx recognizer and VAD from the cache."""
        import sherpa_onnx

        self._status("loading parakeet-tdt-0.6b-v3 (int8)...")
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model_dir / "encoder.int8.onnx"),
            decoder=str(model_dir / "decoder.int8.onnx"),
            joiner=str(model_dir / "joiner.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=2,
            model_type="nemo_transducer",
        )
        vad_config = sherpa_onnx.VadModelConfig()
        vad_config.silero_vad.model = str(vad_path)
        vad_config.silero_vad.min_silence_duration = MIN_SILENCE
        vad_config.sample_rate = SAMPLE_RATE
        window_size = getattr(
            vad_config.silero_vad, "window_size", None
        )
        if (
            not isinstance(window_size, int)
            or isinstance(window_size, bool)
            or window_size <= 0
        ):
            # A zero window would make the capture loop spin forever
            # without ever consuming data or seeing the stop event.
            raise RuntimeError(
                f"invalid VAD window size: {window_size!r}"
            )
        self._window_size = window_size
        self._vad = sherpa_onnx.VoiceActivityDetector(
            vad_config, buffer_size_in_seconds=120
        )

    def transcribe(self, samples, sample_rate: int) -> str:  # noqa: ANN001
        """Decode one float32 mono segment; returns the text ("" if none)."""
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        self._recognizer.decode_stream(stream)
        result = getattr(stream, "result", None)
        text = getattr(result, "text", None) if result is not None else None
        return text.strip() if isinstance(text, str) else ""

    # -- worker internals -------------------------------------------------

    def _report(self, msg: str) -> None:
        """Report a worker-side problem without letting reporting raise."""
        try:
            self._status(msg)
        except Exception:
            pass

    def _safe_call(self, fn: Callable[[], None], name: str) -> None:
        """Invoke a client callback; never let its exception escape.

        Callbacks are suppressed once shutdown has begun so no text is
        typed after the app reports that it stopped.
        """
        if self._stop.is_set():
            return
        try:
            fn()
        except Exception as e:
            self._report(f"{name} callback error (ignored): {e}")

    def _loop(
        self,
        on_utterance: Callable[[str], None],
        on_activity: Callable[[], None],
    ) -> None:
        """Outer recovery boundary: retry capture until stopped or fatal.

        Only *consecutive* failures count toward the fatal limit: a
        session that captured audio before failing (``_capture_progress``)
        resets the counter, so isolated recoverable errors spread over a
        long run never accumulate into a fatal shutdown. Sessions that
        die before producing any audio (mic gone, bad device) keep
        incrementing until the limit.
        """
        failures = 0
        while not self._stop.is_set():
            self._capture_progress = False
            try:
                self._capture_session(on_utterance, on_activity)
                failures = 0
            except Exception as e:
                if self._stop.is_set():
                    break
                failures = 1 if self._capture_progress else failures + 1
                if failures >= self.MAX_CONSECUTIVE_FAILURES:
                    self.fatal_error = (
                        f"microphone capture failed {failures} times "
                        f"in a row; giving up (last error: {e})"
                    )
                    self._report(self.fatal_error)
                    return
                self._report(
                    f"capture error: {e}; retrying in "
                    f"{self.RETRY_DELAY:g}s "
                    f"({failures}/{self.MAX_CONSECUTIVE_FAILURES})"
                )
                self._stop.wait(self.RETRY_DELAY)

    def _open_stream(self, sd):  # noqa: ANN001, ANN202
        """Open the mic at 16 kHz, falling back to the device's own rate."""
        try:
            return sd.InputStream(
                channels=1, dtype="float32", samplerate=SAMPLE_RATE
            )
        except Exception:
            # Device doesn't do 16 kHz natively; capture at its default
            # rate and resample in _read_samples. If this open fails too,
            # the exception reaches the retry boundary in _loop.
            return sd.InputStream(channels=1, dtype="float32")

    def _capture_session(
        self,
        on_utterance: Callable[[str], None],
        on_activity: Callable[[], None],
    ) -> None:
        """One capture session: open the mic and process until stopped."""
        import numpy as np
        import sounddevice as sd

        window_size = self._window_size
        if (
            not isinstance(window_size, int)
            or isinstance(window_size, bool)
            or window_size <= 0
        ):
            # Defense in depth (start() validates too): a zero/bogus
            # window would spin the buffer loop forever without ever
            # consuming data or checking the stop event.
            raise RuntimeError(
                f"invalid VAD window size: {window_size!r}"
            )
        stream = self._open_stream(sd)
        with self._stream_lock:
            self._stream = stream
        try:
            with stream:
                native_rate = int(getattr(stream, "samplerate", 0) or 0)
                if native_rate <= 0:
                    raise RuntimeError(
                        f"invalid device sample rate: "
                        f"{getattr(stream, 'samplerate', None)!r}"
                    )
                read_size = max(1, int(0.1 * native_rate))
                buffer = np.zeros(0, dtype=np.float32)
                empty_reads = 0
                while not self._stop.is_set():
                    if self._reset_req.is_set():
                        self._reset_req.clear()
                        self._flush_req.clear()
                        try:
                            self._vad.reset()
                        except Exception as e:
                            self._report(f"vad reset error: {e}")
                        buffer = np.zeros(0, dtype=np.float32)
                    if self._flush_req.is_set():
                        self._flush_req.clear()
                        try:
                            self._vad.flush()
                        except Exception as e:
                            self._report(f"vad flush error: {e}")
                        self._drain_segments(on_utterance)
                    samples = self._read_samples(
                        stream, read_size, native_rate, np
                    )
                    if samples is None:
                        # Unusable read (None/empty/malformed). A long
                        # unbroken run of these means the stream is
                        # defunct: fail the session so _loop restarts
                        # it with backoff (and eventually goes fatal if
                        # no session ever produces audio) instead of
                        # spinning here forever looking healthy.
                        empty_reads += 1
                        if empty_reads >= self.MAX_EMPTY_READS:
                            raise RuntimeError(
                                "microphone stream produced no usable "
                                f"audio in {empty_reads} consecutive "
                                "reads"
                            )
                        continue
                    empty_reads = 0
                    samples = self._agc.process(samples)
                    buffer = np.concatenate([buffer, samples])
                    # Only now — after a validated chunk was actually
                    # folded into the pipeline — does the session count
                    # as having made progress; malformed data must
                    # never reset the consecutive-failure streak in
                    # _loop.
                    self._capture_progress = True
                    while (
                        len(buffer) >= window_size
                        and not self._stop.is_set()
                    ):
                        self._vad.accept_waveform(
                            buffer[:window_size]
                        )
                        buffer = buffer[window_size:]
                    if self._vad.is_speech_detected():
                        self._safe_call(on_activity, "on_activity")
                    self._drain_segments(on_utterance)
        finally:
            with self._stream_lock:
                self._stream = None

    def _read_samples(  # noqa: ANN202
        self, stream, read_size, native_rate, np  # noqa: ANN001
    ):
        """Read one chunk as mono float32 at 16 kHz; None if unusable.

        Never returns junk: an empty tuple from ``read()``, ``None``
        data, values numpy cannot convert, scalars, and arrays of
        unexpected rank all yield ``None`` (the caller counts these
        toward the empty-read cap) rather than raising or leaking a
        shape that would break ``np.concatenate`` downstream. The
        result is always a nonempty 1-D float32 array.
        """
        result = stream.read(read_size)
        if isinstance(result, tuple):
            if not result:
                return None
            data = result[0]
        else:
            data = result
        if data is None:
            return None
        try:
            samples = np.asarray(data, dtype=np.float32)
        except Exception:
            return None
        if samples.ndim == 2 and samples.shape[1] > 0:
            samples = samples[:, 0]  # (frames, channels) -> channel 0
        if samples.ndim != 1 or samples.size == 0:
            return None
        if native_rate != SAMPLE_RATE:
            n_out = int(samples.size * SAMPLE_RATE / native_rate)
            if n_out <= 0:
                return None
            samples = np.interp(
                np.linspace(0.0, 1.0, n_out, endpoint=False),
                np.linspace(0.0, 1.0, samples.size, endpoint=False),
                samples,
            ).astype(np.float32)
        return samples

    def _drain_segments(
        self, on_utterance: Callable[[str], None]
    ) -> None:
        """Decode every completed VAD segment; tolerate null/bad values.

        All validation of the segment payload happens inside the guarded
        block: an unsized scalar, non-numeric junk, an empty array, or a
        wrong-shaped value is skipped (and reported) instead of raising
        out of the drain and restarting the capture session.
        """
        import numpy as np

        while not self._vad.empty() and not self._stop.is_set():
            # Read (and COPY) the segment before pop(): vad.front is a
            # view into the native queue and popping invalidates it —
            # reading afterwards yields empty/dangling data, silently
            # discarding every utterance.
            front = self._vad.front
            raw = (
                getattr(front, "samples", None)
                if front is not None
                else None
            )
            try:
                segment = (
                    None
                    if raw is None
                    else np.array(raw, dtype=np.float32, copy=True)
                )
            except Exception:
                segment = None
            self._vad.pop()
            if segment is None or segment.ndim != 1 or segment.size == 0:
                continue
            try:
                text = self.transcribe(segment, SAMPLE_RATE)
            except Exception as e:
                self._report(f"parakeet decode error: {e}")
                continue
            if text:
                self._safe_call(
                    lambda t=text: on_utterance(t), "on_utterance"
                )

    def stop(self) -> None:
        """Stop capture, unblocking any in-flight read, and join the worker.

        Aborting/closing the stream interrupts a blocked
        ``InputStream.read()``. If the worker still does not exit within
        the timeout, its reference is retained (and the condition
        reported) so a later ``stop()`` can try again — it is never
        silently forgotten while alive.
        """
        self._stop.set()
        with self._stream_lock:
            stream = self._stream
        if stream is not None:
            for method in ("abort", "close"):
                try:
                    getattr(stream, method)()
                except Exception:
                    pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3.0)
            if thread.is_alive():
                self._report(
                    "audio worker did not exit within 3s; "
                    "keeping reference and continuing shutdown"
                )
            else:
                self._thread = None
