"""The voice-type runtime: mic transcription -> state machine -> keystrokes.

Wires Moonshine's ``MicTranscriber`` (streaming transcription with built-in
voice activity detection) to a small activation state machine and the
keystroke injector. Utterances are typed atomically when the VAD completes
a line, so each dictated sentence is a single undo unit in the target app.
"""

from __future__ import annotations

import sys
import threading
import time
from enum import Enum

from .config import Config
from .inject import Typist, play_sound
from .logic import contains_phrase, is_exact_phrase, text_after_wake_word


class State(Enum):
    """Activation state of the dictation loop."""

    PAUSED = "paused"
    PASSIVE = "waiting for wake word"
    ACTIVE = "dictating"


class VoiceTypeApp:
    """Runs the mic transcriber and types utterances per the config."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.typist = Typist()
        self._lock = threading.Lock()
        self._state = (
            State.ACTIVE
            if cfg.mode == "vad"
            else State.PASSIVE
            if cfg.mode == "wake"
            else State.PAUSED
        )
        self._off_state = (
            State.PASSIVE if cfg.mode == "wake" else State.PAUSED
        )
        self._last_activity = time.monotonic()
        self._stop = threading.Event()

    # -- state transitions ------------------------------------------------

    def _set_state(self, new: State) -> None:
        with self._lock:
            if new == self._state:
                return
            old, self._state = self._state, new
            self._last_activity = time.monotonic()
        if self.cfg.sounds and State.ACTIVE in (old, new):
            play_sound(activate=new == State.ACTIVE)
        self._status(new.value)

    def toggle(self) -> None:
        """Hotkey handler: flip between active and the off state."""
        with self._lock:
            active = self._state == State.ACTIVE
        self._set_state(self._off_state if active else State.ACTIVE)

    # -- transcript handling ----------------------------------------------

    def handle_utterance(self, text: str) -> None:
        """Process one VAD-completed utterance from the transcriber."""
        text = text.strip()
        if not text:
            return
        with self._lock:
            state = self._state
            self._last_activity = time.monotonic()
        if state == State.PAUSED:
            return
        if state == State.PASSIVE:
            remainder = text_after_wake_word(text, self.cfg.wake_word)
            if remainder is None:
                return
            self._set_state(State.ACTIVE)
            if remainder:
                self._type(remainder)
            return
        # ACTIVE
        if any(
            is_exact_phrase(text, p) for p in self.cfg.submit_phrases
        ):
            self.typist.press_enter()
            self._status("submitted (Enter)")
            return
        if self.cfg.stop_phrase and contains_phrase(
            text, self.cfg.stop_phrase
        ):
            self._set_state(self._off_state)
            return
        self._type(text)

    def _type(self, text: str) -> None:
        if self.cfg.trailing_space:
            text += " "
        self.typist.type_text(text)

    # -- main loop --------------------------------------------------------

    def run(self) -> None:
        """Block running the transcriber until Ctrl+C or SIGTERM."""
        from moonshine_voice import get_model_for_language
        from moonshine_voice.mic_transcriber import MicTranscriber
        from moonshine_voice.moonshine_api import string_to_model_arch
        from moonshine_voice.transcriber import TranscriptEventListener

        wanted_arch = string_to_model_arch(self.cfg.model_arch)
        self._status(
            f"loading model ({self.cfg.model_arch}, {self.cfg.language})..."
        )
        model_path, model_arch = get_model_for_language(
            wanted_language=self.cfg.language, wanted_model_arch=wanted_arch
        )

        app = self

        class _Listener(TranscriptEventListener):
            def on_line_completed(self, event) -> None:  # noqa: ANN001
                app.handle_utterance(event.line.text)

            def on_error(self, event) -> None:  # noqa: ANN001
                app._status(f"transcriber error: {event}")

        transcriber = MicTranscriber(
            model_path=model_path, model_arch=model_arch
        )
        transcriber.add_listener(_Listener())

        hotkeys = self._start_hotkey_listener()
        transcriber.start()
        self._status(
            f"ready — mode={self.cfg.mode}, hotkey={self.cfg.hotkey}, "
            f"Ctrl+C to quit"
        )
        self._status(self._state.value)
        try:
            while not self._stop.is_set():
                time.sleep(0.25)
                self._check_idle_timeout()
        except KeyboardInterrupt:
            pass
        finally:
            transcriber.stop()
            transcriber.close()
            if hotkeys is not None:
                hotkeys.stop()
            self._status("stopped")

    def _check_idle_timeout(self) -> None:
        if self.cfg.mode != "wake":
            return
        with self._lock:
            expired = (
                self._state == State.ACTIVE
                and time.monotonic() - self._last_activity
                > self.cfg.idle_timeout
            )
        if expired:
            self._status(f"idle for {self.cfg.idle_timeout:g}s")
            self._set_state(State.PASSIVE)

    def _start_hotkey_listener(self):  # noqa: ANN202
        from .hotkey import start_hotkey

        try:
            return start_hotkey(self.cfg.hotkey, self.toggle)
        except ValueError as e:
            self._status(
                f"invalid hotkey {self.cfg.hotkey!r} ({e}); "
                "hotkey disabled"
            )
            return None

    @staticmethod
    def _status(msg: str) -> None:
        print(f"[voice-type] {msg}", file=sys.stderr, flush=True)
