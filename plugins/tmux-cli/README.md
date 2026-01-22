# tmux-cli

Adds the `tmux-cli` skill so Claude Code can interact with CLI scripts or other
code-agents running in tmux panes.

## What it does

This skill enables Claude Code to communicate with other processes running in tmux
panes, such as:

- Other AI code agents (Claude Code, Codex CLI, etc.)
- Interactive scripts waiting for input
- Long-running processes that need monitoring
- Debuggers (e.g., pdb) for stepping through code

## Usage

Use the `tmux-cli` command to send input to and capture output from other tmux panes.

Run `tmux-cli --help` to see all available options.

Common use cases:

- Launch another Claude instance for parallel work
- Consult another CLI code-agent (e.g. Claude Code, Codex-CLI, or Gemini-CLI) - for help in debugging, reviewing code, or discussing ideas.
- Send commands to a running Python debugger
- Interact with a script that prompts for input
- Monitor output from a background process

## Key Commands

### `execute` - Run command with exit code

The `execute` subcommand runs a shell command and returns both the output and exit
code, making it ideal for build/test automation:

```bash
tmux-cli execute "pytest tests/" --timeout 60
```

Returns JSON with `output` and `exit_code` fields. Use this when you need to know
if a command succeeded or failed (e.g., builds, tests, linters).

For interactive or long-running commands, use `send` + `capture` instead.

## Installation

This skill requires the `claude-code-tools` package:

```bash
uv tool install claude-code-tools
```

If you don't have uv:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
