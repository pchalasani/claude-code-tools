# voxtype — local voice dictation that types where your cursor is

`voxtype` turns speech into text in whatever app has focus — your editor,
browser, terminal, or a coding agent's prompt box. Everything runs on your
machine: no cloud, no accounts, no audio leaving your Mac.

> **macOS only.** voxtype types via macOS accessibility, suppresses hotkeys
> at the CGEvent tap, and draws its overlay with AppKit. On Apple Silicon
> (M1 or later) it defaults to the `parakeet-mlx` Apple-GPU engine; Intel
> Macs automatically fall back to the `parakeet` CPU engine.

## Install

```bash
uv tool install voxtype
```

That's it. The install includes the best engine for your machine — the
Apple-GPU `parakeet-mlx` engine on Apple Silicon (best accuracy AND
speed), the CPU `parakeet` engine everywhere else — and that engine is
the runtime default too, so no flags are needed.

Or run without installing: `uvx voxtype`. (No `uv`? `pipx install
voxtype` works too.)

## Why it's different

- **One key to dictate.** Hit the toggle hotkey (default `Ctrl+;`), speak as
  long as you like, hit it again — the whole take is transcribed with full
  sentence context and typed in one go (a single edit to undo, in most
  apps). No chopping at pauses, sub-second results.
- **Or go hands-free.** Say "hey claude" (configurable, with alias
  spellings) to start dictating; say "stop listening" or go quiet to
  re-arm. The hotkey still works as an override.
- **Spoken submit.** Say "go", "over", or "submit" as a standalone
  utterance and voxtype presses Enter — dictate a prompt to a coding agent
  and send it without touching the keyboard.
- **Clean transcripts.** Standalone fillers (uh, um, ...) are stripped and
  3+ word stutters collapse ("I I I think" becomes "I think") — on-device
  regex, no LLM involved.
- **Visible when it matters.** A floating waveform pill appears ONLY while
  recording — red waves as you speak. Pill on screen = mic live; no pill =
  not listening.
- **Nothing is ever lost.** Dictated into the wrong window? A paste-again
  hotkey re-types the last session's transcript at the cursor; optional
  clipboard mirroring too. Escape cancels a recording outright.

## Quick start

1. Install (see above), then grant your terminal **Microphone**,
   **Accessibility**, and **Input Monitoring** permissions
   (System Settings → Privacy & Security) when prompted.

2. Start dictating:

   ```bash
   voxtype --segmentation hold
   ```

   Press `Ctrl+;`, speak (watch the waveform pill), press `Ctrl+;` again —
   the whole take types at your cursor. `Esc` cancels a take.

3. Make it yours: run `voxtype setup` for an interactive walkthrough — it
   asks a handful of explained questions (engine, mode, hotkey, ...) and
   writes your `~/.config/voxtype/config.toml`. (Prefer editing by hand?
   `voxtype init` drops a fully commented sample instead.) After that,
   plain `voxtype` starts with your settings.

## Hotkey & voice vocabulary

| Action | Default | Configurable as |
|--------|---------|-----------------|
| Toggle recording | `Ctrl+;` | `hotkey` |
| Cancel recording (discard) | `Esc` (only while recording) | `cancel_hotkey` |
| Re-type last transcript | off | `paste_hotkey` |
| Submit (press Enter) | say "go" / "over" / "submit" | `submit_phrases` |
| Stop dictating | say "stop listening" | `stop_phrase` |

Not sure how to spell a chord? Run `voxtype hotkey`, press the combo you
want, and it prints the exact line to put in your config
(e.g. `hotkey = "<ctrl>+;"`).

## Engines

| Engine | Availability | Notes |
|--------|--------------|-------|
| `parakeet-mlx` | included (default on Apple Silicon) | Parakeet-TDT 0.6b v3 on the Apple GPU via MLX: fp16 accuracy at ~40x realtime — best accuracy AND speed |
| `parakeet` | included (default elsewhere) | Parakeet-TDT 0.6b v3 on CPU via sherpa-onnx (~490 MB download) |
| `moonshine` | opt-in: `voxtype[moonshine]` | Streaming Moonshine models, smallest footprint (no Intel-macOS build) |

## Documentation

Full reference — every config key with its default, the engine comparison,
and all CLI flags:
<https://pchalasani.github.io/claude-code-tools/tools/voxtype/>

## History

voxtype previously shipped as `voice-type` inside
[claude-code-tools](https://github.com/pchalasani/claude-code-tools). It
still reads a legacy `~/.config/voice-type/config.toml` and reuses the old
`~/.cache/voice-type` model cache, so upgrading is seamless.

MIT license.
