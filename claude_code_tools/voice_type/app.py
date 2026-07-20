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
from typing import Callable

from .config import Config
from .inject import Typist, play_sound
from .logic import (
    collapse_repeats,
    contains_phrase,
    is_exact_phrase,
    strip_fillers,
    text_after_wake_word,
)


class State(Enum):
    """Activation state of the dictation loop."""

    PAUSED = "paused"
    PASSIVE = "waiting for wake word"
    ACTIVE = "dictating"


def _preview(text: str, limit: int = 60) -> str:
    """Shorten ``text`` for a one-line status message."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
        # Bumped on every state transition; keystroke injection is
        # skipped if the version moved between the state snapshot and
        # the injection (see _inject).
        self._state_version = 0
        # Serializes transition side effects (sound + status) so they
        # emit in commit order and stale ones are suppressed.
        self._effects_lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._grace_until = 0.0
        self._last_toggle = 0.0
        self._engine = None
        self._stop = threading.Event()

    # -- state transitions ------------------------------------------------

    def _transition(
        self, compute: Callable[[State], State]
    ) -> int:
        """Atomically commit ``compute(current_state)`` as the new state.

        The current state is read, the new state computed, and the
        transition committed under a single lock acquisition, so
        concurrent transitions (two hotkey toggles, a toggle vs. the
        idle timeout) serialize: none can act on a stale snapshot.
        Effects are emitted after the lock is released.

        Returns:
            The resulting state version (unchanged if ``compute``
            returned the current state).
        """
        with self._lock:
            new = compute(self._state)
            if new == self._state:
                return self._state_version
            old, self._state = self._state, new
            self._state_version += 1
            version = self._state_version
            self._last_activity = time.monotonic()
        self._emit_transition_effects(version, old, new)
        return version

    def _set_state(self, new: State) -> int:
        """Transition to ``new``; returns the resulting state version."""
        return self._transition(lambda _current: new)

    def _emit_transition_effects(
        self,
        version: int,
        old: State,
        new: State,
        pre_msg: str | None = None,
    ) -> None:
        """Emit the sound/status for a committed transition, unless stale.

        The state commit and its user-visible effects are two steps; a
        concurrent transition (hotkey toggle vs. idle timeout) can land
        between them. Effects therefore run under a dedicated lock and
        re-check the state version first: a superseded transition emits
        nothing, so the last sound/status always reflects the actual
        state.
        """
        with self._effects_lock:
            with self._lock:
                if version != self._state_version:
                    return
            if pre_msg is not None:
                self._status(pre_msg)
            if self.cfg.sounds and State.ACTIVE in (old, new):
                play_sound(activate=new == State.ACTIVE)
            self._status(new.value)

    #: Seconds after a toggle-off during which an in-flight utterance
    #: (speech finished just before the toggle) is still committed.
    TOGGLE_GRACE = 2.0

    #: Grace after a hold-mode toggle-off: the whole take is decoded
    #: before delivery, which can take a few seconds for long takes.
    HOLD_GRACE = 10.0

    #: Ignore a second toggle within this window: macro keys (e.g. UHK)
    #: can re-fire the chord on hold, flipping the state twice for one
    #: intended press. OS autorepeat is already filtered at the event
    #: tap; this catches firmware-generated repeats too.
    TOGGLE_DEBOUNCE = 0.3

    def toggle(self) -> None:
        """Hotkey handler: flip between active and the off state.

        The read-invert-commit runs atomically inside ``_transition``:
        two concurrent toggles can never both observe the same state
        and collapse into one transition (a lost toggle).

        Toggle-off means "commit what I said": the engine is asked to
        flush its in-flight VAD segment and a grace window lets that
        trailing utterance still type. Toggle-on resets the engine's
        audio state so stale pre-activation speech never leaks into the
        first utterance.
        """
        now = time.monotonic()
        with self._lock:
            if now - self._last_toggle < self.TOGGLE_DEBOUNCE:
                return
            self._last_toggle = now

        deactivating = {}

        def compute(current: State) -> State:
            deactivating["was_active"] = current == State.ACTIVE
            return (
                self._off_state
                if current == State.ACTIVE
                else State.ACTIVE
            )

        self._transition(compute)
        engine = self._engine
        hold = self.cfg.segmentation == "hold"
        if deactivating.get("was_active"):
            # Hold mode decodes the whole take on stop, so give its
            # delivery a longer grace window than a mere VAD flush.
            grace = self.HOLD_GRACE if hold else self.TOGGLE_GRACE
            with self._lock:
                self._grace_until = time.monotonic() + grace
            action = getattr(
                engine,
                "request_hold_stop" if hold else "request_flush",
                None,
            )
        else:
            action = getattr(
                engine,
                "request_hold_start" if hold else "request_reset",
                None,
            )
        if action is not None:
            action()

    # -- transcript handling ----------------------------------------------

    def note_activity(self) -> None:
        """Record speech evidence (partial text / VAD) to defer idle re-arm.

        Engines call this while an utterance is still in progress so that
        a long dictation never trips the wake-mode idle timeout mid-speech.
        """
        with self._lock:
            self._last_activity = time.monotonic()

    def handle_utterance(self, text: str) -> None:
        """Process one VAD-completed utterance from the transcriber.

        Malformed transcripts (None, non-strings) are ignored rather
        than raised on: engines may surface junk event payloads.
        """
        if not isinstance(text, str):
            return
        text = text.strip()
        if not text:
            return
        with self._lock:
            state = self._state
            version = self._state_version
            self._last_activity = time.monotonic()
        if state in (State.PAUSED, State.PASSIVE):
            with self._lock:
                in_grace = time.monotonic() < self._grace_until
            if in_grace:
                # In-flight speech from just before a toggle-off:
                # commit it (the user said it while dictating).
                if any(
                    is_exact_phrase(text, p)
                    for p in self.cfg.submit_phrases
                ):
                    with self._lock:
                        self.typist.press_enter()
                    self._status("submitted (Enter, in-flight)")
                else:
                    self._status(f'typed in-flight: "{_preview(text)}"')
                    self._type(text, None)
                return
        if state == State.PAUSED:
            self._status(f'heard while paused (dropped): "{_preview(text)}"')
            return
        if state == State.PASSIVE:
            remainder = None
            for word in (self.cfg.wake_word, *self.cfg.wake_word_aliases):
                remainder = text_after_wake_word(text, word)
                if remainder is not None:
                    break
            if remainder is None:
                self._status(
                    f'heard (awaiting wake word): "{_preview(text)}"'
                )
                return
            version = self._set_state(State.ACTIVE)
            if remainder:
                self._type(remainder, version)
            return
        # ACTIVE
        if any(
            is_exact_phrase(text, p) for p in self.cfg.submit_phrases
        ):
            if self._inject(version, self.typist.press_enter):
                self._status("submitted (Enter)")
            return
        if self.cfg.stop_phrase and contains_phrase(
            text, self.cfg.stop_phrase
        ):
            self._set_state(self._off_state)
            return
        self._status(f'typed: "{_preview(text)}"')
        self._type(text, version)

    def _inject(
        self, version: int, action: Callable[[], None]
    ) -> bool:
        """Run a keystroke action unless a state transition intervened.

        The state snapshot in ``handle_utterance`` and the injection
        here are two steps; the hotkey or idle-timeout thread may
        deactivate in between. Holding the lock while re-validating the
        version AND performing the action serializes injection against
        transitions, so text or Enter can never land after a
        deactivation.

        Returns:
            True if the action ran, False if it was skipped as stale.
        """
        with self._lock:
            if version != self._state_version:
                return False
            action()
            return True

    def _type(self, text: str, version: int | None) -> None:
        """Type ``text``; ``version=None`` skips the staleness check
        (used for grace-window commits, which by definition arrive
        after a state transition)."""
        if self.cfg.strip_fillers:
            # Fillers first: removing "um"s can make a stutter run
            # adjacent ("I um I um I" -> "I I I"), which then collapses.
            text = collapse_repeats(strip_fillers(text))
            if not text:
                return
        if self.cfg.trailing_space:
            text += " "
        if version is None:
            with self._lock:
                self.typist.type_text(text)
        else:
            self._inject(version, lambda: self.typist.type_text(text))

    # -- main loop --------------------------------------------------------

    def run(self) -> int:
        """Block running the transcriber until Ctrl+C or engine failure.

        The whole lifecycle (engine + hotkey listener) sits inside one
        try/finally: a failure at any point — engine construction, model
        download, device startup — still stops every resource that did
        start, each in its own guarded cleanup block. Synchronous engine
        failures are reported and produce exit code 1, matching the
        asynchronous ``fatal_error`` path; only ``ImportError`` is
        re-raised so the CLI can print its install hint.

        Returns:
            0 on clean shutdown, 1 if the engine failed fatally.

        Raises:
            ImportError: If the engine's optional dependencies are
                missing (cli.py turns this into an install hint).
        """
        from .engines import create_engine

        engine = None
        hotkeys = None
        exit_code = 0
        try:
            engine = create_engine(self.cfg, self._status)
            self._engine = engine
            hotkeys = self._start_hotkey_listener()
            engine.start(self.handle_utterance, self.note_activity)
            self._status(
                f"ready — engine={self.cfg.engine}, mode={self.cfg.mode}, "
                f"hotkey={self.cfg.hotkey}, Ctrl+C to quit"
            )
            self._status(self._state.value)
            exit_code = self._main_loop(engine)
        except KeyboardInterrupt:
            pass
        except ImportError:
            # Missing optional dependency: propagate so the CLI can
            # print which extra to install (cleanup still runs).
            raise
        except Exception as e:
            self._status(f"engine failed: {e}")
            exit_code = 1
        finally:
            if engine is not None:
                try:
                    engine.stop()
                except Exception as e:
                    self._status(f"error stopping engine: {e}")
            if hotkeys is not None:
                try:
                    hotkeys.stop()
                except Exception as e:
                    self._status(f"error stopping hotkey listener: {e}")
            self._status("stopped")
        return exit_code

    def _main_loop(self, engine) -> int:  # noqa: ANN001
        """Run the housekeeping loop until stop; returns the exit code.

        With the overlay enabled (macOS), AppKit's run loop drives the
        ticks (the waveform pill needs the main thread); otherwise a
        plain sleep loop does. Housekeeping is identical either way.
        """
        result = {"code": 0}

        def tick() -> None:
            self._check_idle_timeout()
            fatal = getattr(engine, "fatal_error", None)
            if fatal:
                self._status(f"engine failed: {fatal}")
                result["code"] = 1
                self._stop.set()

        if self.cfg.overlay:
            from . import overlay

            if overlay.overlay_available():

                def sample() -> tuple[str, bool, float]:
                    with self._lock:
                        state = self._state
                    level = getattr(engine, "level", 0.0)
                    try:
                        level = float(level)
                    except (TypeError, ValueError):
                        level = 0.0
                    return state.value, state is State.ACTIVE, level

                overlay.run_overlay(
                    sample, tick, self._stop.is_set
                )
                return result["code"]
            self._status("overlay unavailable; running without it")
        while not self._stop.is_set():
            time.sleep(0.25)
            tick()
        return result["code"]

    def _check_idle_timeout(self) -> None:
        if self.cfg.mode != "wake":
            return
        with self._lock:
            # Check AND commit under one lock acquisition: note_activity()
            # and handle_utterance() take the same lock, so fresh speech
            # can never slip in between the expiry check and the
            # ACTIVE -> PASSIVE transition.
            expired = (
                self._state == State.ACTIVE
                and time.monotonic() - self._last_activity
                > self.cfg.idle_timeout
            )
            if expired:
                self._state = State.PASSIVE
                self._state_version += 1
                version = self._state_version
                self._last_activity = time.monotonic()
        if expired:
            self._emit_transition_effects(
                version,
                State.ACTIVE,
                State.PASSIVE,
                pre_msg=f"idle for {self.cfg.idle_timeout:g}s",
            )

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
