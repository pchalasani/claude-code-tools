"""Floating waveform pill: visible proof that voice-type is listening.

A small always-on-top, click-through, non-activating macOS panel at the
bottom-center of the screen. It scrolls a live waveform of the mic
level: red waves while dictating, a dim flat line while paused or
waiting for the wake word. Because the panel is a non-activating
borderless NSPanel that ignores mouse events, it can never steal
keyboard focus from the app being dictated into.

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

_WIDTH = 280.0
_HEIGHT = 46.0
_MARGIN_BOTTOM = 96.0
_BARS = 64
_TICK_SECONDS = 0.05


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
        """Draws the pill background, state dot, and scrolling bars."""

        def initWithFrame_(self, frame):  # noqa: ANN001, ANN201, N802
            self = objc.super(WaveView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.levels = [0.0] * _BARS
            self.recording = False
            self.label = ""
            return self

        def push_(self, sample):  # noqa: ANN001, ANN201, N802
            label, recording, level = sample
            self.levels = self.levels[1:] + [max(0.0, min(1.0, level))]
            self.recording = bool(recording)
            self.label = str(label)
            self.setNeedsDisplay_(True)

        def drawRect_(self, rect):  # noqa: ANN001, ANN201, N802
            b = self.bounds()
            pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                b, b.size.height / 2, b.size.height / 2
            )
            NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.82).setFill()
            pill.fill()
            if self.recording:
                bar_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    1.0, 0.27, 0.23, 1.0
                )
            else:
                bar_color = NSColor.colorWithCalibratedWhite_alpha_(
                    0.62, 0.9
                )
            bar_color.setFill()
            # state dot on the left
            dot = 8.0
            dot_rect = NSMakeRect(
                14.0, (b.size.height - dot) / 2, dot, dot
            )
            NSBezierPath.bezierPathWithOvalInRect_(dot_rect).fill()
            # scrolling waveform bars, mirrored around the centerline
            left = 30.0
            right = 14.0
            span = b.size.width - left - right
            bw = span / _BARS
            mid = b.size.height / 2
            max_half = b.size.height / 2 - 7.0
            for i, level in enumerate(self.levels):
                half = max(1.0, level * max_half)
                x = left + i * bw
                bar = NSMakeRect(x, mid - half, max(1.0, bw - 2.0), 2 * half)
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    bar, 1.0, 1.0
                ).fill()

    class Driver(AppKit.NSObject):
        """NSTimer target: samples audio, ticks the app, ends the loop."""

        def initWithView_(self, view):  # noqa: ANN001, ANN201, N802
            self = objc.super(Driver, self).init()
            if self is None:
                return None
            self.view = view
            return self

        def tick_(self, _timer):  # noqa: ANN001, ANN201, N802
            try:
                tick()
            except Exception:
                pass
            try:
                self.view.push_(sample())
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
    panel.orderFrontRegardless()

    driver = Driver.alloc().initWithView_(view)
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
