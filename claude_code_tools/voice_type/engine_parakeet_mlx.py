"""Parakeet on Apple's GPU via MLX: fp16 accuracy at 40x realtime.

Same capture/VAD/hold pipeline as the sherpa-onnx engine (it subclasses
``ParakeetEngine``); only the decoder differs — parakeet-mlx runs the
fp16/bf16 Parakeet-TDT v3 weights on the Metal GPU, giving Hex-class
accuracy AND speed. Model weights come from HuggingFace on first use
(cached in ``~/.cache/huggingface``).

Requires the ``voice-mlx`` extra (Apple Silicon only).
"""

from __future__ import annotations

import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable

from .config import Config
from .engine_parakeet import (
    MIN_SILENCE,
    SAMPLE_RATE,
    ParakeetEngine,
    _install_lock,
    _remove_cache_entry,
    ensure_vad,
)
from .engines import StatusFn


class ParakeetMlxEngine(ParakeetEngine):
    """Mic -> Silero VAD -> Parakeet-TDT v3 on the MLX GPU."""

    def __init__(self, cfg: Config, status: StatusFn) -> None:
        try:
            import mlx.core  # noqa: F401
            import parakeet_mlx  # noqa: F401
        except ImportError as e:
            raise ImportError(
                f"{e}. The parakeet-mlx engine needs the voice-mlx "
                'extra: uv tool install "claude-code-tools[voice-mlx]" '
                "(Apple Silicon only)"
            ) from e
        super().__init__(cfg, status)

    def start(
        self,
        on_utterance: Callable[[str], None],
        on_activity: Callable[[], None],
    ) -> None:
        """Spawn the worker and BLOCK until its models are loaded.

        MLX streams are thread-local: a model loaded on the main thread
        cannot decode on the capture thread ("There is no Stream(cpu, 1)
        in current thread"), so loading happens ON the worker thread.
        ``start`` still waits for the worker to finish loading (or
        fail) before returning: the app reports "ready" right after
        ``start``, and a first-run model download can take minutes —
        without the wait, recordings during that window would be
        silently lost.

        Raises:
            RuntimeError: If model loading failed (``fatal_error`` is
                set to the same message).
        """
        self._reset_runtime_state()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._load_and_loop,
            args=(on_utterance, on_activity),
            name="ParakeetMlxEngine",
            daemon=True,
        )
        self._thread.start()
        # Short-interval polling keeps the main thread responsive to
        # Ctrl+C while the (possibly minutes-long) first download runs.
        while not self._ready.wait(0.2):
            pass
        if self.fatal_error:
            raise RuntimeError(self.fatal_error)

    def _load_and_loop(
        self,
        on_utterance: Callable[[str], None],
        on_activity: Callable[[], None],
    ) -> None:
        try:
            self._load_models_with_repair()
        except Exception as e:
            self.fatal_error = f"model load failed: {e}"
            self._report(self.fatal_error)
            return
        finally:
            self._ready.set()
        self._loop(on_utterance, on_activity)

    def _load_models_with_repair(self) -> None:
        """Load the MLX model (HF-cached) and the Silero VAD.

        Self-repairing, as the name promises: a cached-but-corrupt
        Silero VAD (nonempty file that fails to construct) is removed
        under the install lock and redownloaded once; a second failure
        propagates. Without this, every subsequent launch would reuse
        the same bad file and fail forever.
        """
        from parakeet_mlx import from_pretrained

        model_id = getattr(
            self.cfg, "mlx_model", "mlx-community/parakeet-tdt-0.6b-v3"
        )
        self._status(
            f"loading {model_id} on the GPU (first run downloads "
            "from HuggingFace)..."
        )
        self._model = from_pretrained(model_id)

        for attempt in (1, 2):
            vad_path = ensure_vad(self._status)
            try:
                self._build_vad(vad_path)
                return
            except Exception as e:
                if attempt == 2:
                    raise
                self._report(
                    f"VAD load failed ({e}); clearing cached VAD "
                    "and redownloading"
                )
                with _install_lock():
                    _remove_cache_entry(vad_path)

    def _build_vad(self, vad_path: Path) -> None:
        """Construct the Silero VAD from ``vad_path``."""
        import sherpa_onnx

        vad_config = sherpa_onnx.VadModelConfig()
        vad_config.silero_vad.model = str(vad_path)
        vad_config.silero_vad.min_silence_duration = MIN_SILENCE
        vad_config.sample_rate = SAMPLE_RATE
        window_size = getattr(vad_config.silero_vad, "window_size", None)
        if (
            not isinstance(window_size, int)
            or isinstance(window_size, bool)
            or window_size <= 0
        ):
            raise RuntimeError(
                f"invalid VAD window size: {window_size!r}"
            )
        self._window_size = window_size
        self._vad = sherpa_onnx.VoiceActivityDetector(
            vad_config, buffer_size_in_seconds=120
        )

    def _enqueue_take(
        self, take, on_utterance: Callable[[str], None]  # noqa: ANN001
    ) -> None:
        """Decode the take inline on the capture (model-owning) thread.

        MLX streams are thread-local (see ``start``): the model loaded
        on this worker thread cannot decode on the base class's shared
        decoder thread. Inline decode blocks capture for the take's
        decode time — at MLX's ~40x realtime that window is small, and
        audio arriving meanwhile is retained by the input stream's
        buffer.
        """
        self._deliver_take(take, on_utterance)

    def transcribe(self, samples, sample_rate: int) -> str:  # noqa: ANN001
        """Decode one float32 mono segment via a temp wav file.

        parakeet-mlx's ``transcribe`` only accepts file paths, so the
        segment round-trips through a temporary 16-bit wav — negligible
        next to decode time (a 40 s take is ~1.2 MB).
        """
        import numpy as np

        with tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False
        ) as f:
            tmp = Path(f.name)
        try:
            pcm = (
                np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
                * 32767.0
            ).astype(np.int16)
            with wave.open(str(tmp), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sample_rate)
                w.writeframes(pcm.tobytes())
            result = self._model.transcribe(str(tmp))
            text = getattr(result, "text", None)
            return text.strip() if isinstance(text, str) else ""
        finally:
            tmp.unlink(missing_ok=True)
            self._release_gpu_cache()

    @staticmethod
    def _release_gpu_cache() -> None:
        """Release MLX's buffer cache after a decode.

        MLX caches every GPU buffer size it has ever allocated and
        never frees them on its own. In a long-running dictation
        process decoding takes of many different lengths, the cache
        grows without bound — observed at 49 GB of dirty IOAccelerator
        memory after two days of use, driving system-wide memory
        pressure and multi-second paging stalls. Decodes happen at
        human cadence, so re-allocating per take costs nothing
        noticeable (~0.15 s decodes measured either way).
        """
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:
            pass
