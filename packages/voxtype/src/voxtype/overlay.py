"""Floating waveform pill: visible proof that voxtype is listening.

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

_WIDTH = 210.0
_HEIGHT = 240.0  # generous headroom so the halo never clips when moving
_MARGIN_BOTTOM = 110.0
_TICK_SECONDS = 0.033  # ~30 fps
# Master animation speed: small = slow, gentle, gradual motion.
_PHASE_SPEED = 0.05
# Idle blink: every ~_BLINK_EVERY frames the eyes close for a few.
_BLINK_EVERY = 130
_BLINK_FRAMES = 6


def overlay_available() -> bool:
    """True if the AppKit bridge is importable (macOS with pyobjc)."""
    try:
        import AppKit  # noqa: F401

        return True
    except Exception:
        return False


def run_overlay(  # noqa: PLR0913
    sample: SampleFn,
    tick: TickFn,
    stopped: Callable[[], bool],
    flex: float = 1.0,
    speed: float = 1.0,
) -> None:
    """Show the ghost and block until ``stopped()`` returns True.

    Args:
        sample: Returns (state_label, is_recording, level) each frame.
        tick: App housekeeping to run each frame (idle timeout, fatal
            checks); exceptions are swallowed so the UI never dies.
        stopped: Polled each frame; True ends the loop and closes the
            panel.
        flex: Face shape-flex multiplier (config overlay_flex).
        speed: Animation speed multiplier (config overlay_speed).

    SIGINT is redirected to a flag-friendly handler while the loop
    runs (AppKit's run loop would otherwise swallow Ctrl+C), and
    restored afterwards.
    """
    import AppKit
    import objc
    from AppKit import (
        NSAffineTransform,
        NSApplication,
        NSBackingStoreBuffered,
        NSBezierPath,
        NSColor,
        NSGradient,
        NSGraphicsContext,
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

    def _mk_ghost(cx, cy, gw, jelly, ph):  # noqa: ANN001, ANN202
        """Ghost silhouette (domed head, wavy hem) with a soft jelly flex.

        Built from points so a gentle, slow, spatially-coherent
        displacement can wobble the whole outline — the head is
        non-rigid (flexes shape), but the amplitude is small and smooth
        so it never turns into a lumpy blob. ``jelly`` is the wobble
        amplitude in points; ``ph`` the slow master phase.
        """
        import math

        dome_y = cy + gw * 0.55
        hem_y = cy - gw * 0.95
        pts = [(cx + gw, hem_y), (cx + gw, dome_y)]
        nd = 26
        for i in range(1, nd):
            a = math.pi * i / nd
            pts.append((cx + gw * math.cos(a), dome_y + gw * math.sin(a)))
        pts.append((cx - gw, dome_y))
        pts.append((cx - gw, hem_y))
        segs, bumps, ha = 30, 3, gw * 0.16
        for i in range(1, segs + 1):
            u = i / segs
            x = cx - gw + u * 2 * gw
            pts.append((x, hem_y - ha * math.sin(u * math.pi * bumps)))

        p = NSBezierPath.bezierPath()
        for i, (x, y) in enumerate(pts):
            dx = jelly * math.sin(0.028 * y + ph * 0.8)
            dy = jelly * math.sin(0.028 * x - ph * 0.6)
            pt = (x + dx, y + dy)
            if i == 0:
                p.moveToPoint_(pt)
            else:
                p.lineToPoint_(pt)
        p.closePath()
        return p

    def _mk_mouth(mx, lip_y, gw, open_, ph):  # noqa: ANN001, ANN202
        """An organic mouth-hole that opens downward (jaw) and morphs.

        Not a scaling oval: the rim is perturbed by slow low harmonics
        so it wobbles/changes shape, it opens mostly DOWNWARD from a
        fixed upper-lip line (like a dropping jaw), and it stays a thin
        slit at rest. ``open_`` is 0..1; ``ph`` is the slow master phase.
        """
        import math

        rx = gw * (0.36 + 0.08 * open_)
        ry = gw * (0.05 + 0.34 * open_)
        cy = lip_y - ry * 0.6  # grows downward from the lip line
        # Constrained flex: two gentle low harmonics at small amplitude
        # so the mouth has organic life without becoming a lumpy blob.
        # (It is also clipped to the face by the caller, so it can never
        # spill past the ghost's silhouette.)
        deform = 0.04 + 0.06 * open_
        n = 44
        p = NSBezierPath.bezierPath()
        for i in range(n + 1):
            a = 2.0 * math.pi * i / n
            wob = 1.0 + deform * (
                math.sin(2 * a + ph * 0.5)
                + 0.4 * math.sin(3 * a - ph * 0.4)
            )
            x = mx + rx * wob * math.cos(a)
            y = cy + ry * wob * math.sin(a)
            if i == 0:
                p.moveToPoint_((x, y))
            else:
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
            # Gentle attack, slow release so the mouth and motion change
            # gradually rather than twitching frame-to-frame.
            k = 0.4 if level > self.amp else 0.10
            self.amp += k * (level - self.amp)
            self.phase += _PHASE_SPEED * speed
            self.frame_no += 1
            self.recording = bool(recording)
            self.setNeedsDisplay_(True)

        def drawRect_(self, rect):  # noqa: ANN001, ANN201, N802
            import math

            b = self.bounds()
            cx, cy = b.size.width / 2, b.size.height / 2
            gw = min(b.size.width, b.size.height) * 0.22
            amp = self.amp
            ph = self.phase

            # Slow, gentle motion — a gradual drift, not a vibration.
            # Bob and sway run at DIFFERENT slow frequencies so the
            # ghost describes a lazy figure-eight rather than a rigid
            # vertical bounce; both grow only mildly with volume.
            bob = (3.0 + 3.0 * amp) * math.sin(ph * 0.9)
            sway = (3.0 + 4.5 * amp) * math.sin(ph * 0.6 + 0.8)
            dcx, dcy = cx + sway, cy + bob
            # The whole face gently breathes and EXPANDS with volume
            # (uniform scale — grows/shrinks, no shape distortion), plus
            # a soft left/right lean. That is the head's audio reaction.
            scale = (1.0 + 0.03 * math.sin(ph * 0.7)) * (1.0 + 0.11 * amp)
            sx = sy = scale
            lean = (2.5 + 3.0 * amp) * math.sin(ph * 0.45)  # degrees

            NSGraphicsContext.currentContext().saveGraphicsState()
            t = NSAffineTransform.transform()
            t.translateXBy_yBy_(dcx, dcy)
            t.rotateByDegrees_(lean)
            t.scaleXBy_yBy_(sx, sy)
            t.translateXBy_yBy_(-dcx, -dcy)
            t.concat()
            try:
                # Slow, spatially-coherent jelly flex so the head is
                # visibly non-rigid; more when talking, and scaled by the
                # user's overlay_flex.
                jelly = (2.6 + 5.0 * amp) * flex

                # soft halo for presence (flexes WITH the body)
                halo = _mk_ghost(dcx, dcy, gw * 1.10, jelly, ph)
                NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.30, 0.62, 1.0, 0.20
                ).setFill()
                halo.fill()

                # body: bluish radial gradient + bright rim
                body = _mk_ghost(dcx, dcy, gw, jelly, ph)
                core = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.62, 0.86, 1.0, 0.88
                )
                edge = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.16, 0.44, 0.98, 0.74
                )
                grad = (
                    NSGradient.alloc().initWithStartingColor_endingColor_(
                        core, edge
                    )
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

                # --- face features, CLIPPED to the body so nothing (the
                # mouth in particular) can ever spill past the silhouette
                gc = NSGraphicsContext.currentContext()
                gc.saveGraphicsState()
                body.addClip()
                dark = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.05, 0.10, 0.26, 0.92
                )
                dark.setFill()
                eye_y = dcy + gw * 0.45
                eye_dx = gw * 0.42
                blinking = (
                    self.frame_no % _BLINK_EVERY
                ) < _BLINK_FRAMES
                eye_w = gw * 0.26
                eye_h = gw * 0.06 if blinking else gw * 0.36
                for s in (-1, 1):
                    _mk_oval(
                        dcx + s * eye_dx, eye_y, eye_w, eye_h
                    ).fill()
                _mk_oval(dcx, dcy + gw * 0.05, gw * 0.10, gw * 0.12).fill()
                lip_y = dcy - gw * 0.18
                _mk_mouth(dcx, lip_y, gw, amp, ph).fill()
                gc.restoreGraphicsState()
            finally:
                NSGraphicsContext.currentContext().restoreGraphicsState()

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
