---
name: voxtype-install
description: Guide the user through installing, configuring, and launching
  voxtype — local on-device voice dictation (speech-to-text that types
  wherever the cursor is). Use when the user asks to install voxtype, set
  up voice dictation / voice typing, or asks how to dictate into their
  editor, terminal, or a coding agent's prompt.
---

# voxtype Install Guide

voxtype is a standalone PyPI package for fully on-device voice
dictation on macOS. It types transcribed speech into whatever app has
focus. No cloud, no accounts, no audio leaving the machine.

## Install

One command; it picks the best engine for the machine automatically
(Apple-GPU `parakeet-mlx` on Apple Silicon, CPU `parakeet` elsewhere):

```bash
uv tool install voxtype
```

If the user doesn't have `uv`, either install it first
(`curl -LsSf https://astral.sh/uv/install.sh | sh`) or use
`pipx install voxtype`.

Note: voxtype only works on macOS (it types via macOS accessibility
APIs). Tell the user this up front if they are on Linux/Windows.

## Configure

Run the interactive wizard — it asks a handful of explained questions
(engine, activation mode, hotkey, ...) and writes
`~/.config/voxtype/config.toml`:

```bash
voxtype setup
```

This must run in the user's own terminal (it is interactive); do not
run it through an agent shell. Alternative for hand-editors:
`voxtype init` writes a fully commented sample config instead.

## Launch

```bash
voxtype
```

Then: press `Ctrl+;` (default hotkey), speak, press `Ctrl+;` again —
the take is transcribed and typed at the cursor. `Esc` cancels a
recording. Saying "go", "over", or "submit" alone presses Enter.

First-run notes for the user:

- macOS will prompt for **Microphone**, **Accessibility**, and
  **Input Monitoring** permissions for the terminal app — all three
  must be granted, then voxtype restarted.
- The first launch downloads the speech model (up to ~2 GB for the
  default Apple-GPU engine, ~490 MB for the CPU engine); later launches
  skip the download and just load it onto the GPU (a few seconds, shown
  by a spinner).

## Reference

- `voxtype --help` — all flags and subcommands
- Full docs: https://pchalasani.github.io/claude-code-tools/tools/voxtype/
