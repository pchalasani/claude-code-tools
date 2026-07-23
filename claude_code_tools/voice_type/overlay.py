"""Floating waveform pill: visible proof that voice-type is listening.

A small always-on-top, click-through, non-activating macOS panel at the
bottom-center of the screen, shown ONLY while recording. It scrolls a
live waveform of the mic level — red waves as you speak — and hides
when paused or waiting for the wake word. Because the panel is a
non-activating borderless NSPanel that ignores mouse events, it can
never steal keyboard focus from the app being dictated into.

Uses pyobjc (already a transitive dependency of pynput on macOS).
The overlay must run on the process's MAIN thread (AppKit rule); the
app hands its per-tick housekeeping in as a callback.
"""

from __future__ import annotations

import signal
from typing import Callable

# (state_label, is_recording, level 0..1) sampled ~20x per second.
SampleFn = Callable[[], tuple[str, bool, float]]
TickFn = Callable[[], None]

_WIDTH = 200.0
_HEIGHT = 150.0
_MARGIN_BOTTOM = 120.0
# Points around the blob's rim (smoothness) and frame rate.
_BLOB_POINTS = 108
_TICK_SECONDS = 0.033  # ~30 fps, smooth wobble


def overlay_available() -> bool:
    """True if the AppKit bridge is importable (macOS with pyobjc)."""
    try:
        import AppKit  # noqa: F401

        return True
    except Exception:
        return False


def run_overlay(sample: SampleFn, tick: TickFn, stopped: Callable[[], bool]) -> None:
    """Show the pill and block until ``stopped()`` returns True.

    Args:
        sample: Returns (state_label, is_recording, level) each frame.
        tick: App housekeeping to run each frame (idle timeout, fatal
            checks); exceptions are swallowed so the UI never dies.
        stopped: Polled each frame; True ends the loop and closes the
            panel.

    SIGINT is redirected to a flag-friendly handler while the loop
    runs (AppKit's run loop would otherwise swallow Ctrl+C), and
    restored afterwards.
    """
    import AppKit
    import objc
    from AppKit import (
        NSApplication,
        NSBackingStoreBuffered,
        NSBezierPath,
        NSColor,
        NSGradient,
        NSMakeRect,
        NSPanel,
        NSScreen,
        NSTimer,
    )

    class WaveView(AppKit.NSView):  # noqa: D401
        """A glowing blue blob that wobbles and swells with the voice.

        A soft orb sits at the bottom of the screen: it breathes gently
        in silence and, as the live gain-adjusted mic level rises, its
        rim deforms (organic multi-harmonic wobble) and the whole blob
        swells — so its shape reflects the actual audio. Bluish, with a
        radial gradient core, a rim highlight, and an outer halo for
        presence.
        """

        def initWithFrame_(self, frame):  # noqa: ANN001, ANN201, N802
            self = objc.super(WaveView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.amp = 0.0  # smoothed audio level driving the wobble
            self.phase = 0.0
            self.recording = False
            return self

        def push_(self, sample):  # noqa: ANN001, ANN201, N802
            _label, recording, level = sample
            level = max(0.0, min(1.0, float(level)))
            # Fast attack, slow release: pops on speech, settles gently.
            k = 0.55 if level > self.amp else 0.15
            self.amp += k * (level - self.amp)
            self.phase += 0.16
            self.recording = bool(recording)
            self.setNeedsDisplay_(True)

        def _blob_path(self, cx, cy, base_r):  # noqa: ANN001, ANN202
            import math

            path = NSBezierPath.bezierPath()
            wob = 0.09 + 0.42 * self.amp      # rim deformation depth
            breathe = 0.05 * math.sin(self.phase * 0.5)
            swell = 1.0 + 0.24 * self.amp     # overall growth with volume
            for i in range(_BLOB_POINTS + 1):
                a = 2.0 * math.pi * i / _BLOB_POINTS
                shape = (
                    math.sin(3 * a + self.phase)
                    + 0.6 * math.sin(5 * a - 1.3 * self.phase)
                    + 0.3 * math.sin(7 * a + 1.7 * self.phase)
                ) / 1.9
                r = base_r * swell * (1.0 + breathe + wob * shape)
                x = cx + r * math.cos(a)
                y = cy + r * math.sin(a)
                if i == 0:
                    path.moveToPoint_((x, y))
                else:
                    path.lineToPoint_((x, y))
            path.closePath()
            return path

        def drawRect_(self, rect):  # noqa: ANN001, ANN201, N802
            b = self.bounds()
            cx, cy = b.size.width / 2, b.size.height / 2
            base_r = min(b.size.width, b.size.height) * 0.26

            # outer halo for presence
            halo = self._blob_path(cx, cy, base_r * 1.28)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.30, 0.62, 1.0, 0.20
            ).setFill()
            halo.fill()

            # the blob: radial gradient core -> deeper blue rim
            blob = self._blob_path(cx, cy, base_r)
            core = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.60, 0.85, 1.0, 0.85
            )
            edge = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.16, 0.44, 0.98, 0.70
            )
            grad = NSGradient.alloc().initWithStartingColor_endingColor_(
                core, edge
            )
            if grad is not None:
                grad.drawInBezierPath_relativeCenterPosition_(
                    blob, (0.0, 0.28)
                )
            else:  # pragma: no cover - gradient unavailable
                edge.setFill()
                blob.fill()

            # bright rim
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.75, 0.92, 1.0, 0.9
            ).setStroke()
            blob.setLineWidth_(2.0)
            blob.stroke()

    class Driver(AppKit.NSObject):
        """NSTimer target: samples audio, ticks the app, ends the loop.

        The pill is shown ONLY while recording — its mere presence is
        the "you left the mic on" indicator; absence means not
        listening. (A color change alone proved too subtle.)
        """

        def initWithView_panel_(self, view, panel):  # noqa: ANN001, ANN201, N802, E501
            self = objc.super(Driver, self).init()
            if self is None:
                return None
            self.view = view
            self.panel = panel
            self.shown = False
            return self

        def tick_(self, _timer):  # noqa: ANN001, ANN201, N802
            try:
                tick()
            except Exception:
                pass
            try:
                data = sample()
                recording = bool(data[1])
                if recording != self.shown:
                    if recording:
                        self.panel.orderFrontRegardless()
                    else:
                        self.panel.orderOut_(None)
                    self.shown = recording
                if recording:
                    self.view.push_(data)
            except Exception:
                pass
            if stopped():
                NSApplication.sharedApplication().stop_(None)
                # stop_() only takes effect once an event is processed;
                # post a no-op event so the run loop wakes immediately.
                evt = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(  # noqa: E501
                    AppKit.NSEventTypeApplicationDefined,
                    (0, 0), 0, 0.0, 0, None, 0, 0, 0,
                )
                NSApplication.sharedApplication().postEvent_atStart_(
                    evt, True
                )

    app = NSApplication.sharedApplication()
    # Accessory: no Dock icon, no menu bar, but windows still display.
    app.setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyAccessory
    )

    screen = NSScreen.mainScreen()
    sframe = screen.frame() if screen is not None else NSMakeRect(
        0, 0, 1440, 900
    )
    x = sframe.origin.x + (sframe.size.width - _WIDTH) / 2
    y = sframe.origin.y + _MARGIN_BOTTOM
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(x, y, _WIDTH, _HEIGHT),
        AppKit.NSWindowStyleMaskBorderless
        | AppKit.NSWindowStyleMaskNonactivatingPanel,
        NSBackingStoreBuffered,
        False,
    )
    panel.setLevel_(AppKit.NSStatusWindowLevel)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setIgnoresMouseEvents_(True)
    panel.setHasShadow_(True)
    panel.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorStationary
    )
    view = WaveView.alloc().initWithFrame_(
        NSMakeRect(0, 0, _WIDTH, _HEIGHT)
    )
    panel.setContentView_(view)
    # Hidden at startup; the driver shows it only while recording.

    driver = Driver.alloc().initWithView_panel_(view, panel)
    timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(  # noqa: E501
        _TICK_SECONDS, driver, b"tick:", None, True
    )
    AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(
        timer, AppKit.NSRunLoopCommonModes
    )

    # Ctrl+C: AppKit's run loop doesn't deliver KeyboardInterrupt on
    # its own. Record the signal in a flag the timer polls (the timer
    # firing is what lets the Python-level handler run at all), end
    # the loop, and re-raise KeyboardInterrupt for the caller.
    sigint = {"hit": False}
    caller_stopped = stopped

    def stopped() -> bool:  # noqa: ANN202
        return sigint["hit"] or caller_stopped()

    previous_handler = signal.getsignal(signal.SIGINT)
    signal.signal(
        signal.SIGINT,
        lambda _sig, _frame: sigint.__setitem__("hit", True),
    )
    try:
        app.run()
    finally:
        signal.signal(signal.SIGINT, previous_handler)
        timer.invalidate()
        panel.orderOut_(None)
        if sigint["hit"]:
            raise KeyboardInterrupt
