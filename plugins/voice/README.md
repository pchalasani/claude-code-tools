# Voice Plugin

Audio feedback for Claude Code using [pocket-tts](https://github.com/kyutai-labs/pocket-tts).

When the Claude Code agent completes a task, it provides a spoken summary of what
was accomplished.

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

### Stop Hook

When the agent tries to stop, the `stop_hook.sh`:

1. Checks if voice feedback is enabled (via config file)
2. If enabled, blocks the agent and asks it to provide a voice summary
3. The agent calls the `say` script with a brief summary
4. Once spoken, the agent is allowed to stop

### The `/voice:speak` Command

Control voice feedback with the slash command:

- `/voice:speak` - Enable voice feedback
- `/voice:speak <voice>` - Set voice (e.g., azure, alba) and enable
- `/voice:speak stop` - Disable voice feedback

Config is stored in `~/.claude/voice.local.md`.

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

- `TTS_HOST`: TTS server host (default: `localhost`)
- `TTS_PORT`: TTS server port (default: `8000`)

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
