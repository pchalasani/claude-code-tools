# PR #195 — voxtype: ghost opacity knob, double-tap modifier toggle

## `overlay_opacity`

```toml
overlay_opacity = 0.6   # 0.1–1.0, default 1.0
```

Whole-ghost transparency, for when the recording ghost sits on top of
text you need to read. Applied as the NSPanel's alpha, so every element
(halo, body, face) scales together and the drawing code keeps its own
per-element alphas untouched.

## `double_tap_key` / `double_tap_ms`

```toml
double_tap_key = "right_alt"   # "" disables (default)
double_tap_ms = 400            # press-to-press window, 50–2000
```

A second way to toggle recording, alongside the `hotkey` chord: tap one
modifier key twice, on its own. Meant for the laptop keyboard, where a
chord like Ctrl+; is awkward; the right Option key is a good choice
because nothing else uses it alone. Both triggers stay active.

Keys: `left_alt right_alt left_cmd right_cmd left_ctrl right_ctrl
left_shift right_shift` ("alt" is the Option key).

### How it works

- Rides on voxtype's existing macOS event tap, observing
  `kCGEventFlagsChanged`. The modifier is **never swallowed** — Option,
  Cmd and friends keep working everywhere.
- Left and right are distinguished by keycode plus the
  `NX_DEVICE*KEYMASK` flag bit (the generic Option flag cannot tell the
  sides apart). Constants checked against the SDK headers; verified live
  on the built-in keyboard.
- Clean-tap rules: press, release, press, release within
  `double_tap_ms` (press to press); no other key and no other held
  modifier during the taps (Shift+Option-Option or left-Option-held are
  chords, not taps); fires on the second *release*, so holding Option
  for an Option-shortcut can never trigger it.
- macOS only; elsewhere the binding is reported and ignored. The
  startup line "double-tap … also toggles recording" is printed only
  when the listener actually armed it.

### Note for programmable keyboards

A UHK 60 v2's right-Alt position is typically a layer key in the UHK
config and sends no Option keycode to macOS at all — so the double tap
does nothing from that keyboard. That is the keyboard's mapping, not a
voxtype limitation; the built-in keyboard reports it correctly.

## Tests

`tests/test_voxtype_doubletap.py`: config bounds and types for all three
keys, the detector state machine (timing, reset, chord-during-hold,
wrong key), stubbed event-tap integration (fires on right Option; not on
left; not with Shift or left Option held; keydown between taps resets;
`when` predicate honored; never swallowed), and app wiring (binding
passed through; opacity passed to the overlay; startup line only when
armed).
