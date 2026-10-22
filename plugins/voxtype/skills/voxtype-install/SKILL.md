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

### Editing the config directly

All settings live in `~/.config/voxtype/config.toml` — every key is
optional and the file `voxtype init` writes is fully commented. You (the
agent) can open and edit this file to help the user tune specific
options; common ones:

- `engine` — `parakeet-mlx` (Apple GPU), `parakeet` (CPU), or `moonshine`.
- `mode` — `toggle` (hotkey), `wake` (wake word), or `vad` (always on).
- `segmentation` — `hold` (whole take on toggle-off) or `vad` (per pause).
- `hotkey` — toggle chord, e.g. `"<ctrl>+;"` (run `voxtype hotkey` to
  record one).
- `sounds` — set `false` to silence the start/stop chimes.
- `overlay_opacity` — 0.1–1.0; lower it if the ghost indicator hides
  text underneath it.
- `double_tap_key` — a second toggle for the laptop keyboard: tap one
  modifier twice (e.g. `"right_alt"`, the right Option key); the
  `hotkey` chord keeps working too.
- `keepalive_minutes` — on a memory-pressured Mac, keeps the speech
  model from being swapped out while idle (try `5`); otherwise leave 0.
- Transcript cleanup by an LLM is **off by default**: plain `voxtype`
  types Parakeet's transcript as-is (with regex filler removal). Two
  opt-in providers; on any failure or timeout the raw transcript is
  typed.
  - **Local** ("S1-mini" by "Superwhisper", on-device): punctuation,
    capitals, dropped self-corrections, about 0.15 s per take, English
    only. Needs `brew install llama.cpp`; run `voxtype post-process
    "some text"` once to download the 462 MiB model and try it. Config:

    ```toml
    post_process = true
    ```

    Optional: `post_process_style` (`casual`, `semi-casual`,
    `semi-formal` (default), `formal`).
  - **Cerebras** (gpt-oss-120b over Cerebras's API): also splits long
    dictations into paragraphs and lists, about 0.4 s per take. It
    **sends every transcript to an external service** — tell the user
    before suggesting it. The key goes in `~/.config/voxtype/.env` as
    one line, `CEREBRAS_API_KEY=...`, followed by
    `chmod 600 ~/.config/voxtype/.env`. Never put it in the shell profile
    or the config file. Have the user paste the key into that file
    themselves rather than echoing it in a command. Config:

    ```toml
    post_process = true
    post_process_provider = "cerebras"
    ```

    Optional: `post_process_format` (`auto` (default), `paragraphs`,
    `minimal`). Line breaks are typed as Shift+Enter; for dictating into
    a plain shell, set `post_process_newlines = "flatten"`.
  - `post_process_timeout` caps the wait (default 4 s local, 2 s
    Cerebras; raise it to fall back to raw less often).
    `post_process_log_text = false` drops the full before/after text
    from the logs (voxtype's short `typed: "…"` previews remain).
- `wake_word` / `wake_word_aliases`, `submit_phrases`, `overlay`,
  `paste_hotkey`, `cancel_hotkey`, `copy_to_clipboard`, and more.
- `submit_suffixes` (default `["go go", "gogo", "now go", "over and out"]`): ending
  a dictation with one types the words before it, then presses Enter,
  with no pause.

### Trying a setting for one run

Any config key can be overridden for one launch with `--set KEY=VALUE`,
exactly as if that line were in the config file. Suggest this when the
user wants to try something without editing the file. KEY may use
hyphens or underscores, and `--set` can be repeated:

```bash
voxtype --set post-process=true --set post-process-provider=s1-mini
                                              # local cleanup this run
voxtype --set post-process=false              # no cleanup this run
voxtype --set sounds=false --set submit-phrases="go, send it"
voxtype post-process --set post-process-format=minimal "so um hi"
```

Booleans take true/false (also yes/no, on/off, 1/0); lists take
comma-separated items or a TOML array; `KEY=` gives an empty value. A
bad key or value stops voxtype with a message naming it. Once the user
likes a setting, put it in the config file so plain `voxtype` uses it.

For the full list — every key, its default, and the engine comparison —
read the **Configuration & CLI** reference:
<https://pchalasani.github.io/claude-code-tools/tools/voxtype/configuration/>

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

- Config file: `~/.config/voxtype/config.toml` (fully commented; edit to
  tune any setting)
- `voxtype --help` — all flags and subcommands
- `voxtype --set KEY=VALUE` — set any config key for one run (repeatable;
  `-` or `_` in KEY), e.g. `--set post-process-provider=s1-mini`
- Config reference (every key + defaults):
  https://pchalasani.github.io/claude-code-tools/tools/voxtype/configuration/
- Overview & full docs:
  https://pchalasani.github.io/claude-code-tools/tools/voxtype/
