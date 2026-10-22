"""The voxtype runtime: mic transcription -> state machine -> keystrokes.

Wires Moonshine's ``MicTranscriber`` (streaming transcription with built-in
voice activity detection) to a small activation state machine and the
keystroke injector. Each utterance is typed in one go when the VAD
completes a line (most apps undo it as a single edit).
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from enum import Enum
from typing import Callable

from .config import Config
from .inject import SoundPlayer, Typist, copy_to_clipboard
from .logic import (
    collapse_repeats,
    contains_phrase,
    is_exact_phrase,
    strip_fillers,
    strip_submit_suffix,
    text_after_wake_word,
)
from .postprocess import effective_newlines, flatten_lines, strip_controls
from .runtime import RuntimeMixin


class State(Enum):
    """Activation state of the dictation loop."""

    PAUSED = "paused"
    PASSIVE = "waiting for wake word"
    ACTIVE = "dictating"


def _preview(text: str, limit: int = 60) -> str:
    """Shorten ``text`` for a one-line status message."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


# A queued delivery that failed authorization at arrival (see
# VoiceTypeApp._deliver). State versions are never negative, so it can
# never pass _inject, and it must never consume a LATER handoff.
_REJECTED = -1


class VoiceTypeApp(RuntimeMixin):
    """Runs the mic transcriber and types utterances per the config."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.typist = Typist()
        # Preload the chimes so the first one is instant and in sync
        # with the overlay (afplay would lag ~100 ms behind the pill).
        self._sound = SoundPlayer(cfg.sound_start, cfg.sound_stop)
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
        # Hold-mode handoff: count of hold-stop requests whose decoded
        # take has not arrived yet (however long decoding takes) —
        # delivery is tied one-to-one to stop requests, never to a
        # wall-clock window. A counter (not a bool) so a rapid
        # off/on/off sequence with two takes still in decode delivers
        # both instead of silently dropping the second.
        self._expect_hold = 0
        self._last_toggle = 0.0
        # When the last hold take stopped (toggle-off): its speech end,
        # for the post-processing latency line (hold segmentation only).
        self._last_hold_stop = 0.0
        self._engine = None
        # Optional LLM cleanup (post_process); created in run(). Tests
        # may set it directly — anything with a clean(str) -> str.
        self._post = None
        # Deliveries (typing / Enter) queued behind LLM cleanup; wake-mode
        # idle expiry waits for them (a take being cleaned is speech in
        # progress). See _deliver.
        self._cleaning = 0
        self._delivery_q: queue.Queue | None = None
        # Set once _start_hotkey_listener runs — in run() (no overlay) or
        # from inside the overlay loop (see _start_hotkeys_from_loop); the
        # finally block stops it. May be None if no valid hotkeys.
        self._hotkeys = None
        # Text of the current/most recent dictation session, for the
        # clipboard option and the paste-again hotkey.
        self._session_texts: list[str] = []
        # Hands-free wait_for_submit: pieces held back until a submit
        # phrase (see _hold_text); typed without Enter on deactivation.
        self._held: list[str] = []
        # Utterance handling and state transitions take turns, so text
        # released by a toggle-off is always queued before a trailing
        # utterance that toggle lets through. Re-entrant: the stop phrase
        # transitions from inside handle_utterance.
        self._order_lock = threading.RLock()
        self._stop = threading.Event()
        # Cleared while the engine loads (run() clears then sets it),
        # so the toggle hotkey is a no-op — with feedback — until audio
        # capture actually exists. Without this, a toggle pressed during
        # the seconds-long model load flipped the state machine to ACTIVE
        # against an engine that could not yet record, leaving a stuck
        # overlay and an app that looked hung. Set by default so unit
        # tests that drive the state machine directly still toggle.
        self._ready = threading.Event()
        self._ready.set()

    # -- state transitions ------------------------------------------------

    def _transition(
        self, compute: Callable[[State], State], keep_held: bool = True
    ) -> int:
        """Commit a transition in turn with utterances (see _order_lock)."""
        with self._order_lock:
            return self._commit_transition(compute, keep_held)

    def _commit_transition(
        self, compute: Callable[[State], State], keep_held: bool
    ) -> int:
        """Atomically commit ``compute(current_state)`` as the new state.

        The current state is read, the new state computed, and the
        transition committed under a single lock acquisition, so
        concurrent transitions (two hotkey toggles, a toggle vs. the
        idle timeout) serialize: none can act on a stale snapshot.
        Effects are emitted after the lock is released.

        Leaving ACTIVE releases the held hands-free text in the same
        commit (typed without Enter, or dropped if not ``keep_held``),
        so a piece is either held before it or typed normally after.

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
            held, self._held = " ".join(self._held), []
            buf = self._session_texts
            if new == State.ACTIVE:
                # Fresh dictation session: the buffer is cleared as
                # part of the SAME commit that publishes ACTIVE, so a
                # transcript delivered right after activation can never
                # be appended first and then erased by a later effect
                # (which would break clipboard/paste-last recovery).
                self._session_texts = []
        if held and keep_held:
            self._deliver(None, lambda v, at: self._type(held, v, at, buf))
            # Typing it may have used up a toggle-off's grace window; the
            # in-flight words get a full one after it. Still inside
            # _order_lock, so no utterance can see the stale deadline.
            with self._lock:
                if self._grace_until:
                    self._grace_until = time.monotonic() + self.TOGGLE_GRACE
        self._emit_transition_effects(version, old, new)
        return version

    def _set_state(self, new: State, keep_held: bool = True) -> int:
        """Transition to ``new``; returns the resulting state version."""
        return self._transition(lambda _current: new, keep_held)

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
                self._sound.play(
                    self.cfg.sound_start
                    if new == State.ACTIVE
                    else self.cfg.sound_stop
                )
            self._status(new.value)

    #: Seconds after a toggle-off during which an in-flight utterance
    #: (speech finished just before the toggle) is still committed.
    TOGGLE_GRACE = 2.0


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
        if not self._ready.is_set():
            # Engine still loading: swallow the press (with feedback)
            # rather than arm a recording the engine cannot fulfill yet.
            self._status("still starting up — wait for the 'ready' message")
            return
        now = time.monotonic()
        with self._lock:
            if now - self._last_toggle < self.TOGGLE_DEBOUNCE:
                return
            self._last_toggle = now
            predicted_active = self._state == State.ACTIVE

        engine = self._engine
        hold = self.cfg.segmentation == "hold"
        # Arm the handoff BEFORE the new state becomes visible, so an
        # utterance delivered mid-transition can never fall in a gap:
        # deactivation arms the grace/hold-delivery flags first;
        # activation clears stale engine audio while still off.
        if predicted_active:
            with self._lock:
                if hold:
                    self._expect_hold += 1
                    self._last_hold_stop = now
                else:
                    self._grace_until = now + self.TOGGLE_GRACE
        else:
            action = getattr(
                engine,
                "request_hold_start" if hold else "request_reset",
                None,
            )
            if action is not None:
                action()

        deactivating = {}

        def compute(current: State) -> State:
            deactivating["was_active"] = current == State.ACTIVE
            return (
                self._off_state
                if current == State.ACTIVE
                else State.ACTIVE
            )

        self._transition(compute)
        was_active = deactivating.get("was_active")
        if was_active != predicted_active:
            # A concurrent transition raced us between the prediction
            # and the commit (debounce makes this rare). Disarm the
            # possibly-wrong handoff; the winning transition owns it.
            with self._lock:
                self._grace_until = 0.0
                self._expect_hold = 0
            return
        if was_active:
            action = getattr(
                engine,
                "request_hold_stop" if hold else "request_flush",
                None,
            )
            if action is not None:
                action()

    def cancel(self) -> None:
        """Cancel-hotkey handler: discard the recording in progress.

        Transitions to the off state FIRST — with NO grace window — and
        only then asks the engine to drop its pending audio (hold
        buffer / open VAD segment). The engine reset is asynchronous,
        so the order matters: invalidating the state/version before
        requesting the reset means a transcript completing concurrently
        fails the version check and grace test instead of typing after
        the user pressed cancel. Only bound while recording, so Escape
        works normally otherwise.
        """
        with self._lock:
            if self._state != State.ACTIVE:
                return
            self._grace_until = 0.0
            self._expect_hold = 0
        self._set_state(self._off_state, keep_held=False)
        with self._lock:
            self._grace_until = 0.0  # _set_state must not resurrect it
            self._expect_hold = 0
        reset = getattr(self._engine, "request_reset", None)
        if reset is not None:
            reset()
        self._status("recording cancelled (discarded)")

    def _is_recording(self) -> bool:
        """Whether the cancel hotkey (Esc) is intercepted right now.

        Never in hands-free mode: it is always listening, so Esc would
        be taken from every app; only the toggle hotkey pauses it.
        """
        if self.cfg.mode == "vad":
            return False
        with self._lock:
            return self._state == State.ACTIVE

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
        # Arrival time, before waiting for a turn: a toggle-off busy
        # typing held text must not run out the grace window that
        # authorizes these (in-flight) words.
        arrived = time.monotonic()
        with self._order_lock:
            self._handle_utterance(text, arrived)

    def _handle_utterance(self, text: str, arrived: float) -> None:
        if not isinstance(text, str):
            return
        text = text.strip()
        if not text:
            return
        with self._lock:
            state = self._state
            version = self._state_version
            self._last_activity = time.monotonic()
            # The rescue buffer of the session this utterance arrived in
            # (a take still in cleanup when the next session starts must
            # not land in that session's buffer).
            origin_buf = self._session_texts
            # Speech end of a hold take = its toggle-off; with VAD the
            # engine logs decode time itself, so it is left unknown.
            speech_end = (
                self._last_hold_stop
                if self.cfg.segmentation == "hold" and self._last_hold_stop
                else None
            )
        if state in (State.PAUSED, State.PASSIVE):
            if self._consume_handoff(at=arrived):
                # In-flight speech from just before a toggle-off:
                # commit it (the user said it while dictating).
                if any(
                    is_exact_phrase(text, p)
                    for p in self.cfg.submit_phrases
                ):
                    self._deliver(None, lambda v, at: self._enter_in_flight())
                elif self.cfg.stop_phrase and is_exact_phrase(
                    text, self.cfg.stop_phrase
                ):
                    # The trailing utterance was itself the stop
                    # phrase: the user was deactivating, not dictating
                    # — typing it would spray "stop listening" into
                    # the focused app.
                    self._status("stop phrase in flight (dropped)")
                else:
                    t, enter, _ = self._split_suffix(text)
                    if t:  # a bare "go go" only presses Enter
                        self._status(f'typed in-flight: "{_preview(t)}"')
                    self._deliver(
                        None,
                        lambda v, at, b=self._session_texts: (
                            self._type(t, v, at, b, speech_end, enter)
                        ),
                    )
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
                t, enter, _ = self._split_suffix(remainder)
                self._deliver(
                    version,
                    lambda v, at, b=self._session_texts: (
                        self._type(t, v, at, b, speech_end, enter)
                    ),
                )
            return
        # ACTIVE
        if any(
            is_exact_phrase(text, p) for p in self.cfg.submit_phrases
        ):
            if not self._hold_text(version, "", True):
                self._deliver(version, self._submit)
            return
        if self.cfg.stop_phrase and contains_phrase(
            text, self.cfg.stop_phrase
        ):
            self._set_state(self._off_state)
            return
        t, enter, show = self._split_suffix(text)
        if self._hold_text(version, t, enter, show):
            return
        if t:  # a bare "go go" only presses Enter
            self._status(f'typed: "{_preview(t)}"')
        self._deliver(
            version,
            lambda v, at, b=origin_buf: (
                self._type(t, v, at, b, speech_end, enter)
            ),
        )

    def _hold_text(
        self, version: int, text: str, enter: bool, show: bool = False
    ) -> bool:
        """Hands-free wait_for_submit: hold ``text`` until a submit.

        On a submit (``enter``) or a show phrase (``show``), everything
        held is typed as one piece, so cleanup sees it whole; a submit
        then presses Enter, a show leaves it for review. Returns False
        when not holding, or when a transition since ``version`` already
        released the held text; the caller then types as usual.
        """
        if self.cfg.mode != "vad" or not self.cfg.wait_for_submit:
            return False
        with self._lock:
            if version != self._state_version:
                return False
            if text:
                self._held.append(text)
            joined = " ".join(self._held)
            if enter or show:
                self._held = []
            buf = self._session_texts
        if not (enter or show):
            self._status(f'held: "{_preview(text)}"')
            return True
        if joined:
            self._status(f'typed: "{_preview(joined)}"')
        elif show:
            return True  # "ok show" with nothing held: nothing to show
        self._deliver(
            version,
            lambda v, at: self._type(joined, v, at, buf, None, enter),
        )
        return True

    def _split_suffix(self, text: str) -> tuple[str, bool, bool]:
        """Split off a trailing submit ("go go") or show ("ok show") phrase.

        Returns the text to type, whether to press Enter after it, and
        whether a show phrase ended it (type held text, no Enter).
        """
        rest = strip_submit_suffix(text, self.cfg.submit_suffixes)
        if rest is not None:
            return rest, True, False
        rest = strip_submit_suffix(text, self.cfg.show_suffixes)
        return (text, False, False) if rest is None else (rest, False, True)

    def _submit(self, version: int | None, at: float) -> None:
        """Press Enter for a submit phrase that arrived at ``at``.

        ``version=None``: already authorized (see ``_deliver``).
        """
        if version is None:
            with self._lock:
                self.typist.press_enter()
            self._status("submitted (Enter)")
        elif self._inject(version, self.typist.press_enter):
            self._status("submitted (Enter)")
        elif version != _REJECTED and self._consume_handoff(at=at):
            # Snapshot said ACTIVE but a toggle-off landed before
            # injection: this utterance IS the in-flight one that
            # toggle's grace/hold handoff authorizes.
            self._enter_in_flight()

    def _enter_in_flight(self) -> None:
        with self._lock:
            self.typist.press_enter()
        self._status("submitted (Enter, in-flight)")

    def _deliver(
        self,
        version: int | None,
        action: Callable[[int | None, float], None],
    ) -> None:
        """Run a typing/Enter ``action(version, arrived)`` in order.

        Without LLM cleanup it runs inline, exactly as before. With it,
        actions go through ONE FIFO worker thread: cleanup can take up
        to post_process_timeout, and the engine calls handle_utterance
        on its capture thread (VAD segmentation), so cleaning inline
        would stall microphone reads. A single queue keeps every action
        in order — a spoken "go" can never press Enter before the text
        dictated just before it is typed. ``arrived`` is when the
        utterance arrived; cleanup's deadline counts from then, so time
        spent queued is part of post_process_timeout.

        A queued action is AUTHORIZED NOW, as inline delivery would be:
        if the state is still ``version`` (or the toggle-off handoff
        covers it) it is passed ``version=None`` and is typed even if
        dictation stops while it waits — exactly what inline typing
        would have done at this moment. Otherwise it keeps ``version``
        and its staleness check fails later, as it would have now.

        Esc (cancel) therefore never discards text already queued here:
        like origin/main, where a delivered utterance is typed at once,
        Esc discards only audio the engine has not yet transcribed.
        """
        arrived = time.monotonic()
        if self._post is None:
            action(version, arrived)
            return
        if version is not None:
            with self._lock:
                current = version == self._state_version
            # The handoff check happens HERE, once, as it would inline;
            # a rejected take is still recorded for rescue by _type but
            # can never be typed or claim a later session's handoff.
            if current or self._consume_handoff(at=arrived):
                version = None
            else:
                version = _REJECTED
        with self._lock:
            self._cleaning += 1
            if self._delivery_q is None:
                self._delivery_q = queue.Queue()
                threading.Thread(
                    target=self._delivery_loop,
                    args=(self._delivery_q,),
                    name="voxtype-delivery",
                    daemon=True,
                ).start()
            q = self._delivery_q
        q.put((action, version, arrived))

    def _delivery_loop(self, q: queue.Queue) -> None:
        while True:
            item = q.get()
            try:
                if item is None:
                    return
                action, version, arrived = item
                try:
                    action(version, arrived)
                except Exception as e:
                    try:  # best-effort: never let reporting end the worker
                        self._status(f"error delivering text: {e}")
                    except Exception:
                        pass
                finally:
                    with self._lock:
                        self._cleaning -= 1
                        # Idle time counts from when the text lands.
                        self._last_activity = time.monotonic()
            finally:
                q.task_done()

    def flush_deliveries(self) -> None:
        """Block until every queued delivery has been typed."""
        q = self._delivery_q
        if q is not None:
            q.join()

    def _consume_handoff(self, at: float | None = None) -> bool:
        """Consume the armed toggle-off handoff; True if one was armed.

        The handoff (a pending hold delivery or the wall-clock grace
        window) authorizes exactly ONE delivery per toggle-off: the
        segment in flight at that toggle. Consuming it atomically —
        wherever the delivery happens to land — means later utterances
        (speech begun after the toggle) can never ride the same
        window, and each hold take is delivered exactly once per stop
        request, however long the decode took.
        """
        with self._lock:
            # ``at``: when the delivery "arrived". A transcript held up
            # by LLM cleanup arrived when cleanup began, so a grace
            # window opened by a toggle during cleanup still covers it.
            armed = (
                self._expect_hold > 0
                or (time.monotonic() if at is None else at)
                < self._grace_until
            )
            if self._expect_hold:
                self._expect_hold -= 1
            self._grace_until = 0.0
            return armed

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

    def _type(
        self,
        text: str,
        version: int | None,
        arrived: float | None = None,
        session: list[str] | None = None,
        speech_end: float | None = None,
        enter: bool = False,
    ) -> None:
        """Type ``text``; ``version=None`` skips the staleness check
        (used for grace-window commits, which by definition arrive
        after a state transition). ``enter`` presses Enter after the
        text is typed (a submit suffix ended the utterance)."""
        if self.cfg.strip_fillers:
            # Fillers first: removing "um"s can make a stutter run
            # adjacent ("I um I um I" -> "I I I"), which then collapses.
            text = collapse_repeats(strip_fillers(text))
        if not text:
            if enter:
                at = time.monotonic() if arrived is None else arrived
                self._submit(version, at)
            return
        if self._post is not None:
            # On the delivery thread (see _deliver), outside every lock:
            # cleanup takes up to post_process_timeout and must never
            # hold up capture, hotkeys, or the overlay. The submit/stop/
            # wake phrases were already matched on the raw text.
            text = self._post.clean(text, since=arrived)
            # Only "\n" may survive as a control character (see
            # strip_controls).
            text = strip_controls(text)
            if "\n" in text and effective_newlines(
                self.cfg.post_process_newlines,
                self.cfg.post_process_provider,
            ) == "flatten":
                text = flatten_lines(text)
        # Record for the paste-again hotkey / clipboard BEFORE typing:
        # even text that lands in the wrong window (or is skipped as
        # stale) stays rescuable.
        # ``session`` is the buffer of the session the utterance ARRIVED
        # in (captured at arrival): a take still in cleanup when a new
        # session starts must not leak into the new session's rescue
        # text, nor overwrite its clipboard copy.
        with self._lock:
            buf = self._session_texts if session is None else session
            buf.append(text)
            current = buf is self._session_texts
            joined = " ".join(buf)
        if self.cfg.copy_to_clipboard and current:
            copy_to_clipboard(joined)
        if self.cfg.trailing_space:
            text += " "
        inserted = True

        def keys() -> None:
            # Text and its Enter go out under one lock acquisition, so a
            # toggle-off or cancel cannot land between them.
            self.typist.type_text(text)
            if enter:
                self.typist.press_enter()

        if version is None:
            with self._lock:
                keys()
        elif self._inject(version, keys):
            pass
        elif version == _REJECTED or not self._consume_handoff(at=arrived):
            inserted = False  # stale: kept for rescue only, never typed
        else:
            # The utterance snapshotted ACTIVE but lost the injection
            # race to a toggle-off that armed the grace/hold handoff.
            # It IS the one in-flight delivery that handoff authorizes
            # — dropping it here would lose the promised toggle-off
            # commit while leaving the grace window armed for stray
            # later speech. Consume the handoff and type.
            with self._lock:
                keys()
        if enter and inserted:
            self._status("submitted (Enter)")
        if self._post is not None and inserted:
            self._log_latency(arrived, speech_end)

    def _log_latency(
        self, arrived: float | None, speech_end: float | None
    ) -> None:
        """One timing line per post-processed take — no transcript text.

        Transcription (hold: toggle-off to transcript), post-processing
        (including network for cerebras) and total time to insertion.
        """
        try:
            now = time.monotonic()
            pp_ms, problem = self._post.last_outcome
            parts = []
            if speech_end and arrived and arrived >= speech_end:
                parts.append(
                    f"transcribe {1000 * (arrived - speech_end):.0f} ms"
                )
            outcome = f"fallback: {problem}" if problem else "ok"
            parts.append(
                f"post-process {pp_ms:.0f} ms "
                f"({self._post.name}, {outcome})"
            )
            start = speech_end if speech_end else arrived
            if start:
                origin = "speech end" if speech_end else "transcript"
                parts.append(
                    f"total {1000 * (now - start):.0f} ms "
                    f"({origin} to inserted)"
                )
            self._status("latency: " + ", ".join(parts))
        except Exception:
            pass  # instrumentation must never affect delivery

    def paste_last(self) -> None:
        """Paste-hotkey handler: type the last session's transcript.

        Rescues dictation that went to the wrong window — place the
        cursor where it should have gone and hit the paste hotkey.
        """
        with self._lock:
            session = " ".join(self._session_texts)
        if not session:
            self._status("nothing to paste")
            return
        if self.cfg.trailing_space:
            session += " "
        with self._lock:
            self.typist.type_text(session)
        self._status(f'pasted last transcript: "{_preview(session)}"')

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
                and not self._cleaning
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

    @staticmethod
    def _status(msg: str) -> None:
        print(f"[voxtype] {msg}", file=sys.stderr, flush=True)
