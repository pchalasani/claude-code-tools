"""voxtype's process lifecycle: engine start-up, main loop, hotkeys.

Split out of app.py to keep it readable; ``VoiceTypeApp`` inherits these
methods, and they use its state (``_lock``, ``_state``, ``cfg``, ...).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import VoiceTypeApp


class RuntimeMixin:
    """Lifecycle methods of ``VoiceTypeApp`` (see app.py)."""

    # -- main loop --------------------------------------------------------

    def run(self: VoiceTypeApp) -> int:
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
        from . import overlay as overlay_mod
        from .engines import create_engine

        engine = None
        exit_code = 0
        try:
            self._ready.clear()
            # The overlay runs AppKit's event loop, which races the
            # keyboard event tap: a tap installed BEFORE that loop starts
            # is intermittently killed by it (hotkey silently dead). So
            # with the overlay we defer the listener to on_ready, fired
            # from INSIDE the running loop (see _main_loop); without it
            # there is no such loop, so we start early — which also gives
            # "still starting up" feedback during the model load.
            use_overlay = (
                self.cfg.overlay and overlay_mod.overlay_available()
            )
            if self.cfg.overlay and not use_overlay:
                self._status("overlay unavailable; running without it")
            if self.cfg.post_process:
                from .post_providers import create_post_processor

                self._post = create_post_processor(self.cfg, self._status)
                # Starts in the background (S1-mini: download, launch,
                # warmup; cerebras: TLS warmup); takes before it is
                # ready are typed raw.
                self._post.start()
            engine = create_engine(self.cfg, self._status)
            self._engine = engine
            if not use_overlay:
                self._hotkeys = self._start_hotkey_listener()
            engine.start(self.handle_utterance, self.note_activity)
            self._ready.set()
            self._status(
                f"ready — engine={self.cfg.engine}, mode={self.cfg.mode}, "
                f"hotkey={self.cfg.hotkey}, Ctrl+C to quit"
            )
            self._status(self._state.value)
            exit_code = self._main_loop(engine, use_overlay)
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
            # The llama-server teardown is the OUTERMOST finally: it runs
            # its own process group (Ctrl+C at the terminal never reaches
            # it), so a second Ctrl+C during any earlier shutdown step
            # must still stop it.
            try:
                if engine is not None:
                    try:
                        engine.stop()
                    except Exception as e:
                        self._status(f"error stopping engine: {e}")
                if self._hotkeys is not None:
                    try:
                        self._hotkeys.stop()
                    except Exception as e:
                        self._status(f"error stopping hotkey listener: {e}")
                if self._delivery_q is not None:
                    # Let queued takes finish (each bounded by the
                    # cleanup timeout) before the server goes away, then
                    # end the worker.
                    self.flush_deliveries()
                    self._delivery_q.put(None)
            finally:
                if self._post is not None:
                    try:
                        self._post.stop()
                    except Exception as e:
                        self._status(f"error stopping post-processor: {e}")
            self._status("stopped")
        return exit_code

    def _start_hotkeys_from_loop(self: VoiceTypeApp) -> None:
        """Install the hotkey listener; fired from inside the overlay loop.

        Running here — after AppKit's event loop is already up — means
        the keyboard event tap is installed on top of it, so the loop
        can't race and silently kill it (the intermittent dead-hotkey
        bug when the tap was installed first).
        """
        self._hotkeys = self._start_hotkey_listener()

    def _main_loop(  # noqa: ANN001
        self: VoiceTypeApp, engine, use_overlay: bool
    ) -> int:
        """Run the housekeeping loop until stop; returns the exit code.

        With the overlay enabled (macOS), AppKit's run loop drives the
        ticks (the waveform pill needs the main thread) and the hotkey
        listener is started from inside it (see
        ``_start_hotkeys_from_loop``); otherwise a plain sleep loop does
        and the listener was already started in ``run``.
        """
        result = {"code": 0}

        def tick() -> None:
            self._check_idle_timeout()
            fatal = getattr(engine, "fatal_error", None)
            if fatal:
                self._status(f"engine failed: {fatal}")
                result["code"] = 1
                self._stop.set()

        if use_overlay:
            from . import overlay
            from .app import State

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
                sample,
                tick,
                self._stop.is_set,
                on_ready=self._start_hotkeys_from_loop,
                flex=self.cfg.overlay_flex,
                speed=self.cfg.overlay_speed,
                opacity=self.cfg.overlay_opacity,
            )
            return result["code"]
        while not self._stop.is_set():
            time.sleep(0.25)
            tick()
        return result["code"]

    def _start_hotkey_listener(self: VoiceTypeApp):  # noqa: ANN202
        """Start the global hotkey listener; invalid chords degrade.

        Missing macOS permissions are reported LOUDLY first: a launch
        context without Input Monitoring gets a dead event tap with no
        error from the OS, which previously looked exactly like "the
        hotkey just doesn't work".

        Each configured chord is validated independently, so one
        malformed OPTIONAL binding (paste/cancel) only disables itself
        — a typo in cancel_hotkey must never take the toggle hotkey
        (and with it all of toggle mode) down.
        """
        from .hotkey import check_permissions, parse_hotkey, start_hotkeys

        for warning in check_permissions():
            self._status(f"WARNING: {warning}")

        candidates: list[tuple[str, tuple]] = [
            ("toggle", (self.cfg.hotkey, self.toggle))
        ]
        if self.cfg.paste_hotkey:
            candidates.append(
                ("paste", (self.cfg.paste_hotkey, self.paste_last))
            )
        if self.cfg.cancel_hotkey:
            # Conditional: intercepted only while recording; passes
            # through to the focused app otherwise.
            candidates.append(
                (
                    "cancel",
                    (
                        self.cfg.cancel_hotkey,
                        self.cancel,
                        self._is_recording,
                    ),
                )
            )
        bindings: list[tuple] = []
        for name, binding in candidates:
            try:
                parse_hotkey(binding[0])
            except ValueError as e:
                self._status(
                    f"invalid {name} hotkey {binding[0]!r} ({e}); "
                    f"{name} hotkey disabled"
                )
                continue
            bindings.append(binding)
        double_tap = None
        if self.cfg.double_tap_key:
            # Second toggle trigger: two taps of a lone modifier key
            # (validated by Config, so no per-binding try here).
            double_tap = (
                self.cfg.double_tap_key,
                self.cfg.double_tap_ms,
                self.toggle,
            )
        if not bindings and double_tap is None:
            self._status("no valid hotkeys configured; hotkeys disabled")
            return None
        try:
            listener = start_hotkeys(bindings, double_tap=double_tap)
        except ValueError as e:
            self._status(
                f"invalid hotkey config ({e}); hotkeys disabled"
            )
            return None
        if double_tap is not None and getattr(
            listener, "double_tap_active", False
        ):
            # Only claim it when the listener actually armed it: the
            # non-tap fallback reports the omission itself.
            self._status(
                f"double-tap {self.cfg.double_tap_key} also toggles "
                "recording"
            )
        return listener
