# PR #196 — voxtype: detect a silent hold take and reopen the microphone

## Problem

A hold take with no signal is a dead microphone, not a quiet user — but
voxtype reported it as `decoded to empty text`, indistinguishable from a
genuinely empty decode. Two real incidents in one week:

- Zoom left the Shure MV7+ muted at the CoreAudio level; voxtype recorded
  a 1.6e-07 dither floor and decoded a 43 s take to nothing.
- A PortAudio stream went stale after opening the lid changed the device
  topology; eight days old, it read zeros while a fresh stream on the
  same device read fine.

Silence is valid audio, so nothing tripped the stream-restart logic.

## Fix

- Track each take's raw, pre-AGC peak over the retained slice (the AGC
  amplifies a dead floor up to 60x; audio past the hold cap is not in
  the take and must not vouch for it).
- Below `SILENT_TAKE_PEAK` (1e-04: ~600x above the measured dead floor,
  ~30x below a live mic's room noise) voxtype prints

  ```
  take was silent (Ns, peak X): microphone muted, held by another
  app, or its stream went stale — reopening the microphone
  ```

  skips the pointless decode, and ends the capture session with
  `_ReopenMicrophone`, which `_loop` treats as a deliberate reopen: no
  retry backoff, and the consecutive-failure count resets (audio was
  captured), so a muted mic can never walk it to the fatal limit.
- Hold mode only. In wake/vad mode a dead mic never triggers the VAD,
  and a continuous silence check would false-positive on noise-gated
  mics.

## Verification

- `tests/test_voxtype_silent_take.py` (7 tests): floor placement,
  report/skip/reopen, raw-not-AGC judgment, per-take reset with a live
  take untouched, no fatal accumulation, failure-count reset, cap-slice
  judgment.
- Real hardware through the real capture loop: the dead (lid-closed)
  built-in mic reports silent and reopens; the live Studio Display mic
  decodes normally.
