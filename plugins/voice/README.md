# Voice Plugin

Audio feedback for Claude Code using [pocket-tts](https://github.com/kyutai-labs/pocket-tts).

When the Claude Code agent completes a task, this plugin:

1. Summarizes the agent's output using Haiku
2. Speaks the summary aloud using pocket-tts

## Requirements

- [uv](https://docs.astral.sh/uv/) (for running pocket-tts via `uvx`)
- macOS (with `afplay`) or Linux (with `aplay` or `paplay`)

## Installation

Copy or symlink this plugin to your Claude Code plugins directory:

```bash
# Option 1: Symlink (recommended for development)
ln -s /path/to/claude-code-tools/plugins/voice ~/.claude/plugins/voice

# Option 2: Copy
cp -r /path/to/claude-code-tools/plugins/voice ~/.claude/plugins/
```

## How It Works

### Stop Hook

When the agent stops, the `stop_hook.py`:

1. Receives the agent's output/transcript
2. Sends it to Haiku for summarization into 1-2 short sentences
3. Calls the `say` script to speak the summary

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

To disable voice feedback, simply remove or uninstall the plugin:

```bash
rm -rf ~/.claude/plugins/voice
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

### Summary not working

The plugin uses `claude -p --model haiku` for summarization. Ensure Claude CLI
is installed and configured.
