# voxtype v0.1.5 — keepalive: stop slow takes after idle

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
per loop iteration. A command can still land mid-decode — inherent to
inline decoding, take decodes included; activation is then delayed by
the decode's duration and buffered audio is processed right after.

## Validation

- `keepalive_minutes` must be a finite number in [0, 1440]; huge TOML
  integers are rejected before they can overflow the engine's math.

## Tests

12 new tests: config bounds/types, restamp on success and failure,
tick gating (disabled / overdue / mid-take / mid-utterance / queued
command / command grace), failure containment, and capture-loop wiring.
