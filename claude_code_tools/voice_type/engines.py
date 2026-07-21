"""Transcription engine backends for voice-type.

An engine captures microphone audio, segments it into utterances (VAD),
transcribes each utterance locally, and invokes a callback with the final
text. The activation state machine in ``app.py`` is engine-agnostic.

Engine protocol: ``start(on_utterance, on_activity)`` begins capture and
transcription; ``stop()`` shuts it down. ``on_utterance`` receives one
string per VAD-completed utterance; ``on_activity`` is called whenever
speech is in progress (partial text / VAD), letting the app defer its
wake-mode idle timeout during long dictations.
"""

from __future__ import annotations

from typing import Callable, Protocol

from .config import Config

StatusFn = Callable[[str], None]


class Engine(Protocol):
    """Minimal interface every transcription backend implements."""

    def start(
        self,
        on_utterance: Callable[[str], None],
        on_activity: Callable[[], None],
    ) -> None:
        """Begin capturing and transcribing; call back per utterance."""
        ...

    def stop(self) -> None:
        """Stop capture and release resources."""
        ...


class MoonshineEngine:
    """Moonshine streaming models via the moonshine-voice toolkit."""

    def __init__(self, cfg: Config, status: StatusFn) -> None:
        self.cfg = cfg
        self._status = status
        self._transcriber = None
        # Activation epoch: bumped by request_reset(). A line whose
        # audio began (first partial text) under an older epoch is
        # dropped when it completes — Moonshine's mic stream cannot be
        # rewound, so this is how paused speech is kept out of a new
        # dictation session.
        self._epoch = 0
        # Epoch at which the in-progress line started (None = no line
        # in progress). Touched only on Moonshine's event thread.
        self._line_epoch: int | None = None

    def start(
        self,
        on_utterance: Callable[[str], None],
        on_activity: Callable[[], None],
    ) -> None:
        from moonshine_voice import get_model_for_language
        from moonshine_voice.mic_transcriber import MicTranscriber
        from moonshine_voice.moonshine_api import string_to_model_arch
        from moonshine_voice.transcriber import TranscriptEventListener

        wanted_arch = string_to_model_arch(self.cfg.model_arch)
        self._status(
            f"loading moonshine model "
            f"({self.cfg.model_arch}, {self.cfg.language})..."
        )
        model_path, model_arch = get_model_for_language(
            wanted_language=self.cfg.language,
            wanted_model_arch=wanted_arch,
        )
        status = self._status
        engine = self

        def safe_status(msg: str) -> None:
            # The status callback itself may raise; nothing reported
            # from Moonshine's event thread is allowed to escape and
            # kill it, so reporting failures are swallowed outright.
            try:
                status(msg)
            except Exception:
                pass

        class _Listener(TranscriptEventListener):
            """Shields Moonshine's transcriber thread from our callbacks.

            Event fields may be missing or null, and client callbacks
            (including the status reporter) may raise; none of that is
            allowed to escape into (and kill) the Moonshine audio/event
            thread, so every report goes through ``safe_status``.
            """

            def on_line_text_changed(self, event) -> None:  # noqa: ANN001
                if engine._line_epoch is None:
                    # First partial text of a new line: stamp it with
                    # the current activation epoch.
                    engine._line_epoch = engine._epoch
                try:
                    on_activity()
                except Exception as e:
                    safe_status(
                        f"on_activity callback error (ignored): {e}"
                    )

            def on_line_completed(self, event) -> None:  # noqa: ANN001
                line_epoch = engine._line_epoch
                engine._line_epoch = None
                line = getattr(event, "line", None)
                text = getattr(line, "text", None)
                if not isinstance(text, str):
                    return
                text = text.strip()
                if not text:
                    return
                if (
                    line_epoch is not None
                    and line_epoch != engine._epoch
                ):
                    # The line's audio began before the most recent
                    # reset (toggle-on): stale paused speech must not
                    # leak into the fresh dictation session.
                    safe_status(
                        f'dropped stale pre-activation line: "{text}"'
                    )
                    return
                try:
                    on_utterance(text)
                except Exception as e:
                    safe_status(
                        f"on_utterance callback error (ignored): {e}"
                    )

            def on_error(self, event) -> None:  # noqa: ANN001
                safe_status(f"transcriber error: {event}")

        self._transcriber = MicTranscriber(
            model_path=model_path, model_arch=model_arch
        )
        self._transcriber.add_listener(_Listener())
        self._transcriber.start()

    def request_flush(self) -> None:
        """No-op: Moonshine's streaming VAD finalizes lines itself."""

    def request_reset(self) -> None:
        """Invalidate any utterance already in progress.

        Moonshine captures continuously and its stream cannot be
        rewound, so the reset is an activation-epoch bump: a line whose
        audio began before this call is discarded when it completes,
        instead of being typed as if it were current dictation.
        """
        self._epoch += 1

    def stop(self) -> None:
        transcriber, self._transcriber = self._transcriber, None
        if transcriber is None:
            return
        try:
            transcriber.stop()
        finally:
            transcriber.close()


def create_engine(cfg: Config, status: StatusFn) -> Engine:
    """Instantiate the engine selected by ``cfg.engine``.

    Raises:
        ImportError: If the selected engine's optional dependencies are
            not installed (message says which extra to install).
    """
    if cfg.engine == "parakeet":
        from .engine_parakeet import ParakeetEngine

        return ParakeetEngine(cfg, status)
    if cfg.engine == "parakeet-mlx":
        from .engine_parakeet_mlx import ParakeetMlxEngine

        return ParakeetMlxEngine(cfg, status)
    return MoonshineEngine(cfg, status)
