# PR #168 — voxtype keepalive: stop slow takes after idle

## Problem

On a memory-pressured Mac, macOS evicts the idle Parakeet-MLX model
(~1.7 GB) to compressed memory/swap. The first take after a pause then
pays a multi-second page-in stall — observed: 8.4 s of audio decoded in
6.16 s (1x realtime) on a machine deep in swap, vs ~0.2 s warm.

## Fix: `keepalive_minutes` (opt-in)

New config knob (parakeet-mlx engine only):

```toml
keepalive_minutes = 5.0   # 0 disables (default); max 1440
```

After that many minutes without a decode, the engine decodes half a
second of silence, keeping the model's pages recently-used so eviction
is far less likely — and any page-in cost lands in an idle moment
instead of on a take.

## Safety guards

The keepalive runs on the capture thread (MLX is thread-local), so it
is skipped whenever it could collide with real speech:

- while a hold take or VAD segment is open
- while an unprocessed activation command (hotkey press) is queued
- for 30 s after any activation command (a just-activated user is
  about to speak)

A failed keepalive is contained and retries once per interval, never
per loop iteration.

## Known limitation

The keepalive decodes on the capture thread (MLX is thread-local), so
capture pauses for its duration. The guards above narrow that window
but cannot close it: a hotkey press landing just after the checks, or
mid-decode, can clip the first moment of dictation — PortAudio's input
buffer holds well under a second, and its overflow flag is not
surfaced.

Exposure is small in practice: a keepalive on a warm model takes
~0.2 s, and keeping the model warm is the point; a multi-second
keepalive means the model was already evicted, which is the case this
option prevents. Inline decoding is pre-existing — an ordinary take
decode blocks capture identically — so closing the window means
decoupling capture from decoding (callback-driven capture feeding a
queue), tracked in issue #169 rather than bundled into this opt-in
feature.

## Validation

- `keepalive_minutes` must be a finite number in [0, 1440]; huge TOML
  integers are rejected before they can overflow the engine's math.

## Tests

12 new tests: config bounds/types, restamp on success and failure,
tick gating (disabled / overdue / mid-take / mid-utterance / queued
command / command grace), failure containment, and capture-loop wiring.
