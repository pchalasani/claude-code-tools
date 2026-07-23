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

_WIDTH = 184.0
_HEIGHT = 184.0
_MARGIN_BOTTOM = 120.0
_TICK_SECONDS = 0.033  # ~30 fps
# Idle blink: every ~_BLINK_EVERY frames the eyes close for a few.
_BLINK_EVERY = 108
_BLINK_FRAMES = 5


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

    # Plain helpers (NOT NSView methods — pyobjc would try to bridge
    # underscored methods as selectors and reject the extra arguments).
    def _mk_oval(cx, cy, w, h):  # noqa: ANN001, ANN202
        return NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - w / 2, cy - h / 2, w, h)
        )

    def _mk_ghost(cx, cy, gw, bob):  # noqa: ANN001, ANN202
        """Classic ghost silhouette: domed head, wavy hem."""
        import math

        p = NSBezierPath.bezierPath()
        dome_y = cy + gw * 0.55 + bob
        hem_y = cy - gw * 0.95 + bob
        p.moveToPoint_((cx + gw, hem_y))
        p.lineToPoint_((cx + gw, dome_y))
        p.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
            (cx, dome_y), gw, 0, 180
        )
        p.lineToPoint_((cx - gw, hem_y))
        segs, bumps, ha = 36, 3, gw * 0.16
        for i in range(1, segs + 1):
            u = i / segs
            x = cx - gw + u * 2 * gw
            y = hem_y - ha * math.sin(u * math.pi * bumps)
            p.lineToPoint_((x, y))
        p.closePath()
        return p

    class WaveView(AppKit.NSView):  # noqa: D401
        """A little glowing-blue ghost whose mouth opens with your voice.

        A friendly translucent ghost floats at the bottom of the screen:
        it bobs and blinks on its own so it feels alive, and its mouth
        opens wider the louder you speak (driven by the live,
        gain-adjusted mic level) -- the Animoji-style talking face that
        makes the tool fun to use.
        """

        def initWithFrame_(self, frame):  # noqa: ANN001, ANN201, N802
            self = objc.super(WaveView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.amp = 0.0  # smoothed level -> mouth opening
            self.phase = 0.0
            self.frame_no = 0
            self.recording = False
            return self

        def push_(self, sample):  # noqa: ANN001, ANN201, N802
            _label, recording, level = sample
            level = max(0.0, min(1.0, float(level)))
            # Fast attack, slow release: the mouth pops open on speech
            # and eases shut.
            k = 0.6 if level > self.amp else 0.18
            self.amp += k * (level - self.amp)
            self.phase += 0.16
            self.frame_no += 1
            self.recording = bool(recording)
            self.setNeedsDisplay_(True)

        def drawRect_(self, rect):  # noqa: ANN001, ANN201, N802
            import math

            b = self.bounds()
            cx, cy = b.size.width / 2, b.size.height / 2
            gw = min(b.size.width, b.size.height) * 0.30
            bob = 3.0 * math.sin(self.phase * 0.5)  # gentle float

            # soft halo for presence
            halo = _mk_ghost(cx, cy, gw * 1.12, bob)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.30, 0.62, 1.0, 0.20
            ).setFill()
            halo.fill()

            # body: bluish radial gradient + bright rim
            body = _mk_ghost(cx, cy, gw, bob)
            core = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.62, 0.86, 1.0, 0.88
            )
            edge = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.16, 0.44, 0.98, 0.74
            )
            grad = NSGradient.alloc().initWithStartingColor_endingColor_(
                core, edge
            )
            if grad is not None:
                grad.drawInBezierPath_relativeCenterPosition_(
                    body, (0.0, 0.35)
                )
            else:  # pragma: no cover
                edge.setFill()
                body.fill()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.80, 0.94, 1.0, 0.9
            ).setStroke()
            body.setLineWidth_(2.0)
            body.stroke()

            # --- face (dark, translucent "holes" like the ghost emoji) ---
            dark = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.05, 0.10, 0.26, 0.9
            )
            dark.setFill()
            eye_y = cy + gw * 0.45 + bob
            eye_dx = gw * 0.42
            blinking = (self.frame_no % _BLINK_EVERY) < _BLINK_FRAMES
            eye_w = gw * 0.26
            eye_h = gw * 0.06 if blinking else gw * 0.36
            for sx in (-1, 1):
                _mk_oval(cx + sx * eye_dx, eye_y, eye_w, eye_h).fill()
            # tiny nose
            _mk_oval(cx, cy + gw * 0.05 + bob, gw * 0.10, gw * 0.12).fill()
            # mouth: opens with volume (a small line at rest -> wide oval)
            mouth_h = gw * (0.08 + 0.80 * self.amp)
            mouth_w = gw * (0.34 + 0.18 * self.amp)
            _mk_oval(
                cx, cy - gw * 0.42 + bob, mouth_w, mouth_h
            ).fill()

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
