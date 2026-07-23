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

_WIDTH = 460.0
_HEIGHT = 92.0
_MARGIN_BOTTOM = 110.0
# Recent audio-level history (drives the local vibration envelope) and
# the number of points the string is rendered with (smoothness).
_HISTORY = 96
_RENDER = 190
_TICK_SECONDS = 0.033  # ~30 fps, smooth string motion


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
        NSMakeRect,
        NSPanel,
        NSScreen,
        NSTimer,
    )

    class WaveView(AppKit.NSView):  # noqa: D401
        """A translucent string that lies flat and vibrates with the voice.

        The line is pinned at both ends (like a plucked string) and its
        oscillation amplitude tracks the live, gain-adjusted mic level:
        silence reads as a near-flat line, speech makes it ripple. The
        recent-level history modulates the amplitude ALONG the string
        (and scrolls), so the shape reflects the actual audio rather
        than a fixed animation.
        """

        def initWithFrame_(self, frame):  # noqa: ANN001, ANN201, N802
            self = objc.super(WaveView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.levels = [0.0] * _HISTORY
            self.phase = 0.0
            self.recording = False
            return self

        def push_(self, sample):  # noqa: ANN001, ANN201, N802
            _label, recording, level = sample
            level = max(0.0, min(1.0, float(level)))
            # Mild smoothing so the string settles rather than jitters.
            prev = self.levels[-1]
            smoothed = prev + 0.5 * (level - prev)
            self.levels = self.levels[1:] + [smoothed]
            self.phase += 0.42  # advance the travelling wave each frame
            self.recording = bool(recording)
            self.setNeedsDisplay_(True)

        def _string_path(self, width, mid, max_amp):  # noqa: ANN001, ANN202
            import math

            path = NSBezierPath.bezierPath()
            path.setLineJoinStyle_(AppKit.NSLineJoinStyleRound)
            path.setLineCapStyle_(AppKit.NSLineCapStyleRound)
            n = len(self.levels)
            for i in range(_RENDER + 1):
                u = i / _RENDER  # 0..1 along the string
                # local amplitude from the level history at this point
                fpos = u * (n - 1)
                lo = int(fpos)
                frac = fpos - lo
                lvl = self.levels[lo]
                if lo + 1 < n:
                    lvl += frac * (self.levels[lo + 1] - lvl)
                window = math.sin(math.pi * u)  # pin both ends to flat
                amp = lvl * max_amp * window
                disp = amp * (
                    math.sin(u * 7.0 * math.pi + self.phase)
                    + 0.4 * math.sin(u * 13.0 * math.pi - 1.7 * self.phase)
                ) / 1.4
                x = u * width
                y = mid + disp
                if i == 0:
                    path.moveToPoint_((x, y))
                else:
                    path.lineToPoint_((x, y))
            return path

        def drawRect_(self, rect):  # noqa: ANN001, ANN201, N802
            b = self.bounds()
            # Ultra-faint rounded backdrop: just enough to read the
            # string over any wallpaper, without looking like a panel.
            pad = 6.0
            back = NSMakeRect(
                pad, pad, b.size.width - 2 * pad, b.size.height - 2 * pad
            )
            r = back.size.height / 2
            NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.16).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                back, r, r
            ).fill()

            mid = b.size.height / 2
            max_amp = b.size.height / 2 - 16.0
            path = self._string_path(b.size.width, mid, max_amp)
            # soft glow behind, then the crisp translucent string
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                1.0, 0.30, 0.26, 0.16
            ).setStroke()
            path.setLineWidth_(7.0)
            path.stroke()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                1.0, 0.42, 0.38, 0.55
            ).setStroke()
            path.setLineWidth_(2.0)
            path.stroke()

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
