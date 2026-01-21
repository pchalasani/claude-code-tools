# Running Claude Code and Codex with Local LLMs

This guide covers running **Claude Code** and **OpenAI Codex CLI** with local
models using [llama.cpp](https://github.com/ggml-org/llama.cpp)'s server:

- **Claude Code** uses the Anthropic-compatible `/v1/messages` endpoint
- **Codex CLI** uses the OpenAI-compatible `/v1/chat/completions` endpoint

## Table of Contents

- [When to Use Local Models](#when-to-use-local-models)
- [How It Works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Shell Function for Claude Code](#shell-function-for-claude-code)
- [Model Commands](#model-commands)
- [Quick Reference](#quick-reference)
- [Usage](#usage)
- [Troubleshooting](#troubleshooting)
- [Using Codex CLI with Local LLMs](#using-codex-cli-with-local-llms)

## When to Use Local Models

These local models (20B-80B parameters) aren't suited for complex coding tasks
where frontier models excel, but they're useful for non-coding tasks like
summarization, answering questions about your private notes, working with
sensitive documents that can't be sent to external APIs, or high-volume tasks
where API costs would add up.

## How It Works

1. **Start llama-server** with a model (see [Model Commands](#model-commands)
   below) - this makes the model available at a local endpoint (e.g., port 8123)
2. **Run Claude Code** pointing to that endpoint using the `cclocal` helper
   function

## Prerequisites

- [llama.cpp](https://github.com/ggml-org/llama.cpp) built and `llama-server`
  available in your PATH
- Sufficient RAM (64GB+ recommended for 30B+ models)
- Models will be downloaded automatically from HuggingFace on first run

## Shell Function for Claude Code

At its simplest, connecting Claude Code to a local model is just one line:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8123 claude
```

The helper function below is just a convenience wrapper for this. Add it to your
`~/.zshrc` or `~/.bashrc`:

```bash
cclocal() {
    local port=8123
    if [[ "$1" =~ ^[0-9]+$ ]]; then
        port="$1"
        shift
    fi
    (
        export ANTHROPIC_BASE_URL="http://127.0.0.1:${port}"
        claude "$@"
    )
}
```

Usage:

```bash
cclocal              # Connect to localhost:8123
cclocal 8124         # Connect to localhost:8124
cclocal 8124 --resume abc123  # With additional claude args
```

> [!IMPORTANT]
> Add this to your `~/.claude/settings.json` to disable telemetry:
>
> ```json
> {
>   // ... other settings ...
>   "env": {
>     "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
>   }
>   // ... other settings ...
> }
> ```
>
> Without this, Claude Code sends telemetry requests to your local server,
> which returns 404s and retries aggressively—causing ephemeral port exhaustion
> on macOS and system-wide network failures.

## Model Commands

### GPT-OSS-20B (Fast, Good Baseline)

Uses the built-in preset with optimized settings:

```bash
llama-server --gpt-oss-20b-default --port 8123
```

**Performance:** ~17-38 tok/s generation on M1 Max

### Qwen3-30B-A3B

```bash
llama-server -hf unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF \
  --port 8124 \
  -c 131072 \
  -b 32768 \
  -ub 1024 \
  --parallel 1 \
  --jinja \
  --chat-template-file ~/Git/llama.cpp/models/templates/Qwen3-Coder.jinja
```

**Performance:** ~15-27 tok/s generation on M1 Max

### Qwen3-Coder-30B-A3B (Recommended)

Uses the built-in preset with Q8_0 quantization (higher quality):

```bash
llama-server --fim-qwen-30b-default --port 8127
```

Downloads `ggml-org/Qwen3-Coder-30B-A3B-Instruct-Q8_0-GGUF` automatically on first
run.

### Qwen3-Next-80B-A3B (Better Long Context)

Newer SOTA model. Slower generation but performance doesn't degrade as much
with long contexts:

```bash
llama-server -hf unsloth/Qwen3-Next-80B-A3B-Instruct-GGUF:Q4_K_XL \
  --port 8126 \
  -c 131072 \
  -b 32768 \
  -ub 1024 \
  --parallel 1 \
  --jinja
```

**Performance:** ~5x slower generation than Qwen3-30B-A3B, but better on long
contexts

### Nemotron-3-Nano-30B-A3B (NVIDIA Reasoning Model)

```bash
llama-server -hf unsloth/Nemotron-3-Nano-30B-A3B-GGUF:Q4_K_XL \
  --port 8125 \
  -c 131072 \
  -b 32768 \
  -ub 1024 \
  --parallel 1 \
  --jinja \
  --chat-template-file ~/Git/llama.cpp/models/templates/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16.jinja \
  --temp 0.6 \
  --top-p 0.95 \
  --min-p 0.01
```

**Recommended settings (from NVIDIA):**

- Tool calling: `temp=0.6`, `top_p=0.95`
- Reasoning tasks: `temp=1.0`, `top_p=1.0`

### GLM-4.7-Flash (Zhipu AI 30B-A3B MoE)

A capable 30B MoE model from Zhipu AI. Requires several specific settings:

```bash
llama-server -hf unsloth/GLM-4.7-Flash-GGUF:Q4_K_XL \
  --port 8129 \
  -c 131072 \
  -b 32768 \
  -ub 1024 \
  --parallel 1 \
  --jinja \
  --reasoning-budget 0 \
  --temp 1.0 \
  --top-p 0.95 \
  --min-p 0.01 \
  --override-kv deepseek2.expert_gating_func=int:2
```

For higher quality, use Q8_0 (~32GB, 20-40% slower):

```bash
llama-server -hf unsloth/GLM-4.7-Flash-GGUF:Q8_0 \
  --port 8129 \
  -c 131072 \
  -b 32768 \
  -ub 1024 \
  --parallel 1 \
  --jinja \
  --reasoning-budget 0 \
  --temp 1.0 \
  --top-p 0.95 \
  --min-p 0.01 \
  --override-kv deepseek2.expert_gating_func=int:2
```

**Critical settings explained:**

| Setting | Why |
|---------|-----|
| `--jinja` | Required for correct chat template |
| `--reasoning-budget 0` | Disables thinking mode (conflicts with Claude Code's assistant prefill) |
| `--min-p 0.01` | llama.cpp defaults to 0.1 which causes issues with this model |
| `--override-kv deepseek2.expert_gating_func=int:2` | Forces SIGMOID gating (model uses SIGMOID, not SOFTMAX like DeepSeek) |

**Prerequisites:**

- llama.cpp build from **Jan 21, 2026 or later** (fixes looping bug)
- Update via: `brew upgrade llama.cpp`

**Quantization options:**

| Quant | Size | Notes |
|-------|------|-------|
| Q4_K_XL | 17.5 GB | Good balance (default) |
| Q8_0 | 31.8 GB | Higher quality, 20-40% slower |

> [!NOTE]
> Disabling thinking (`--reasoning-budget 0`) doesn't significantly impact coding
> tasks. The model still reasons internally—you just don't see explicit
> `<think>...</think>` blocks. Thinking mode is more useful for math proofs and
> logic puzzles where you want to verify step-by-step reasoning.

## Quick Reference

| Model | Port | Command |
|-------|------|---------|
| GPT-OSS-20B | 8123 | `llama-server --gpt-oss-20b-default --port 8123` |
| Qwen3-30B-A3B | 8124 | See full command above |
| Nemotron-3-Nano | 8125 | See full command above |
| Qwen3-Next-80B-A3B | 8126 | See full command above |
| Qwen3-Coder-30B | 8127 | `llama-server --fim-qwen-30b-default --port 8127` |
| GLM-4.7-Flash | 8129 | See full command above |

## Usage

1. Start the llama-server with your chosen model (first request will be slow
   while model loads)
2. In another terminal, run `cclocal <port>` to start Claude Code
3. Use Claude Code as normal

## Notes

- First request is slow while the model loads into memory (~10-30 seconds
  depending on model size)
- Subsequent requests are fast
- The `/v1/messages` endpoint in llama-server handles Anthropic API translation
  automatically
- Each model's chat template handles the model-specific prompt formatting

## Troubleshooting

**"failed to find a memory slot" errors:**

Increase context size (`-c`) or reduce parallel slots (`--parallel 1`). Claude
Code sends large system prompts (~20k+ tokens).

**Slow generation:**

- Increase batch size: `-b 32768`
- Reduce parallel slots: `--parallel 1`
- Check if model is fully loaded in RAM/VRAM

**Model not responding correctly:**

Ensure you're using the correct chat template for your model. The template
handles formatting the Anthropic API messages into the model's expected format.

---

# Using Codex CLI with Local LLMs

[OpenAI Codex CLI](https://github.com/openai/codex) can also use local models via
llama-server's OpenAI-compatible `/v1/chat/completions` endpoint.

## Configuration

Add a local provider to `~/.codex/config.toml`:

```toml
[model_providers.llama-local]
name = "Local LLM via llama.cpp"
base_url = "http://localhost:8123/v1"
wire_api = "chat"
```

For multiple ports (different models), define multiple providers:

```toml
[model_providers.llama-8123]
name = "Local LLM port 8123"
base_url = "http://localhost:8123/v1"
wire_api = "chat"

[model_providers.llama-8124]
name = "Local LLM port 8124"
base_url = "http://localhost:8124/v1"
wire_api = "chat"
```

## Switching Models at Command Line

Use the `--model` flag and `-c` (config) flag to switch models without editing
the TOML file:

```bash
# Use GPT-OSS-20B on port 8123 (model name is immaterial)
codex --model gpt-oss-20b -c model_provider=llama-8123

# Use Qwen3-30B on port 8124 (model name is immaterial)
codex --model qwen3-30b -c model_provider=llama-8124

```

You can also override nested config values with dots:

```bash
codex --model gpt-oss-20b \
  -c model_provider=llama-local \
  -c model_providers.llama-local.base_url="http://localhost:8124/v1"
```

## Running llama-server for Codex

Use the same llama-server commands as for Claude Code.


```bash
# GPT-OSS-20B
llama-server --gpt-oss-20b-default --port 8123

# Qwen3-Coder-30B
llama-server --fim-qwen-30b-default --port 8127
```

## Notes

- Codex uses the `/v1/chat/completions` endpoint (OpenAI format), not
  `/v1/messages` (Anthropic format)
- Both endpoints are served by llama-server simultaneously
- The same model can serve both Claude Code and Codex at the same time

---

# Vision Models with Codex CLI

Codex CLI supports image inputs (`-i`/`--image` flags), and llama-server can serve
vision-language models like Qwen3-VL. This enables local multimodal inference.

> **Note:** Vision only works via the OpenAI-compatible `/v1/chat/completions`
> endpoint (Codex), not the Anthropic `/v1/messages` endpoint (Claude Code).

## Qwen3-VL-30B-A3B Setup

Vision models require two GGUF files: the main model + a multimodal projector
(mmproj).

**One-time setup** (download the mmproj file):

```bash
just qwen3-vl-download
# Or manually:
mkdir -p ~/models
hf download Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF \
  mmproj-Qwen3-VL-30B-A3B-Instruct-f16.gguf \
  --local-dir ~/models
```

**Start the server** (port 8128):

```bash
just qwen3-vl
# Or manually:
llama-server -hf Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF:Q4_K_M \
  --mmproj ~/models/mmproj-Qwen3-VL-30B-A3B-Instruct-f16.gguf \
  --port 8128 \
  -c 32768 \
  -b 2048 \
  -ub 2048 \
  --parallel 1 \
  --jinja
```

**Use with Codex:**

First, add the provider to `~/.codex/config.toml`:

```toml
[model_providers.llama-8128]
name = "Qwen3-VL Vision"
base_url = "http://localhost:8128/v1"
wire_api = "chat"
```

Then run Codex with an image:

```bash
codex --model qwen3-vl -c model_provider=llama-8128 -i screenshot.png "describe this"
```

## Quick Reference

| Model | Port | Command |
|-------|------|---------|
| Qwen3-VL-30B-A3B | 8128 | `just qwen3-vl` (after `just qwen3-vl-download`) |
