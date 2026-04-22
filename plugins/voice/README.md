# Voice Plugin

Audio feedback for Claude Code using [pocket-tts](https://github.com/kyutai-labs/pocket-tts).

When the Claude Code agent completes a task, it provides a spoken summary of what
was accomplished.

## Recommended: Speech-to-Text Companion

For a complete voice workflow, pair this TTS plugin with
[Handy](https://github.com/cjpais/Handy) (open-source) using the **Parakeet V3**
model for speech-to-text. It's stunningly fast with near-instant transcription.

The slight accuracy drop compared to larger models is immaterial when talking to
an AI. **Pro tip**: Ask the agent to restate what it understood - this confirms
understanding and helps keep the CLI agent on track.

## Requirements

- [uv](https://docs.astral.sh/uv/) (for running pocket-tts via `uvx`)
- macOS (with `afplay`) or Linux (with `aplay` or `paplay`)
- **Recommended**: [FFmpeg](https://ffmpeg.org/) (provides `ffplay` for lower-latency
  streaming audio)

## Installation

Install from the cctools-plugins marketplace:

```bash
claude plugin add voice
```

## How It Works

### Architecture Overview

The plugin uses a multi-hook strategy to get fast, reliable voice summaries:

```
UserPromptSubmit hook     →  Injects full voice instructions each turn
         ↓
PostToolUse hook          →  Short reminder after each tool call
         ↓
Agent generates 📢 marker →  "📢 Done, fixed the auth bug!"
         ↓
Stop hook extracts it     →  Instant playback (no API call!)
         ↓
[Fallback: headless Claude if agent forgets the marker]
```

### The Hooks

**UserPromptSubmit hook** — Silently injects voice instructions at the start of
each turn, telling Claude to end longer responses with a `📢` spoken summary.
Uses `additionalContext` for silent injection (no terminal noise).

**PostToolUse hook** — Injects a brief reminder after each tool call to keep the
voice instructions fresh during long tool chains where Claude might forget.

**Stop hook** — When the agent stops, this hook:

1. Checks if voice is enabled (via `~/.claude/voice.local.md`)
2. Looks for a `📢` marker in the last assistant message (instant extraction)
3. If no marker but response is short (≤25 words), speaks it directly
4. Falls back to headless Claude summarization only if needed
5. Plays the audio via pocket-tts

### Word Limits

- **Short responses** (≤25 words): Spoken directly, no summary needed
- **Explicit summaries** (📢 marker or headless Claude): Flexible 1.5× limit (37 words)
- **Last resort truncation**: Strict limit (25 words)

The limit is configurable via `MAX_SPOKEN_WORDS` in `hooks/voice_common.py`.

### The `/voice:speak` Command

Control voice feedback with the slash command:

- `/voice:speak` - Enable voice feedback
- `/voice:speak <voice>` - Set voice (e.g., azure, alba) and enable
- `/voice:speak stop` - Disable voice feedback
- `/voice:speak prompt <text>` - Set custom instruction for summaries
- `/voice:speak prompt` - Clear custom prompt

Config is stored in `~/.claude/voice.local.md`.

### Custom Prompts

Use custom prompts to personalize how summaries are delivered:

```bash
# Be more enthusiastic
/voice:speak prompt "be upbeat and encouraging"

# Keep it ultra-brief
/voice:speak prompt "use 5 words or less"

# Add a sign-off
/voice:speak prompt "always end with 'back to you, boss'"
```

The custom prompt is appended as an additional instruction to the summarizer.

### The `say` Script

The `scripts/say` script is a standalone TTS utility that:

1. Checks if the pocket-tts server is running
2. Starts the server if needed (first run may take ~30-60 seconds)
3. Sends text to the TTS endpoint
4. Plays the generated audio

## Standalone Usage

You can use the `say` script directly from the command line:

```bash
# Basic usage
./scripts/say "Hello, world!"

# With a specific voice
./scripts/say --voice azure "Hello, world!"

# Show help
./scripts/say --help
```

### Environment Variables

- `CC_VOICE`: Voice to use (overrides `~/.claude/voice.local.md`; `--voice` CLI flag still wins)
- `TTS_HOST`: TTS server host (default: `localhost`)
- `TTS_PORT`: TTS server port (default: `8000`)

Voice resolution order: `--voice <name>` flag → `CC_VOICE` env var → config file `voice:` field.

## Disabling

Disable voice feedback temporarily:

```
/voice:speak stop
```

Or uninstall the plugin entirely:

```bash
claude plugin remove voice
```

## Troubleshooting

### Server won't start

Check the server log:

```bash
cat /tmp/pocket-tts-server.log
```

### No audio playing

- **macOS**: Ensure `afplay` is available (built-in)
- **Linux**: Ensure `aplay` or `paplay` is installed

### Slow audio playback

If there's a noticeable delay before audio starts, install FFmpeg to enable
streaming mode:

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
```

With FFmpeg installed, audio streams directly to `ffplay` as it's generated,
reducing latency. Without it, the script waits for the full audio file before
playing.
