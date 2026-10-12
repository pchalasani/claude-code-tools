# Design: local LLM post-processing (S1-mini)

Status: implemented on branch `feat/voxtype-post`. Off by default.

## Goal

Optionally run each transcript through a small local LLM that adds
punctuation and casing and removes self-corrections and verbal clutter, before
voxtype types it. Everything stays on the machine. Any failure types the raw
transcript, exactly as voxtype does today.

## The model

"S1-mini" by "Superwhisper" is a fine-tune of Qwen3-0.6B (596M parameters,
English only).

- Weights: `superwhisper/s1-mini-GGUF`, file `s1-mini-q4_k_m.gguf`, 484,219,808
  bytes, pinned at revision `34add00a48a2e5d24e5a4ee5405a99620a3a240c` with
  sha256 `3b41ebe2…3c634`. The pin and hash match Freehand STT's.
- License: Apache-2.0 plus one added term, that any use must keep calling it
  "S1-mini" by "Superwhisper" with that exact capitalization. voxtype does not
  redistribute the weights; it downloads them from Hugging Face on first use.
  The docs name the model as required.
- Prompt contract, verbatim from the model card: a fixed system prompt, then a
  user message made of one control line and a newline, then the transcript:

  ```
  [Styling: semi-formal] [Structure: prose] [Context: general]
  <transcript>
  ```

  Styling is casual, semi-casual, semi-formal or formal. Structure is prose or
  lists. Context is general or email. The card warns that any other value, or a
  reworded system prompt, can make it hallucinate.
- Decoding must be greedy (temperature 0) with Qwen3 thinking turned off
  (`--chat-template-kwargs '{"enable_thinking":false}'`). The card recommends
  roughly 1,000 input tokens at most, and an output cap of about
  1.3 × input tokens + 32.

## Prior art

- **Muesli** (Swift) runs it in-process through an LLM.swift fork of llama.cpp,
  hardcodes semi-formal, prose and general, and sets no timeout. It falls back
  to the raw text when the output is empty, contains assistant-style chatter,
  or grows too much relative to the input.
- **Freehand STT** (Go) manages a `llama-server` child process on 127.0.0.1
  and calls `/v1/chat/completions`. It falls back to the raw text on a timeout,
  an HTTP error, a truncated result (`finish_reason == "length"`) or an empty
  result.

## Decisions

1. **Serving: a managed `llama-server` child process.** voxtype starts
   llama.cpp's `llama-server` on 127.0.0.1 at a free port and talks to it over
   HTTP with Python's standard library. There is no new Python dependency.

   We considered and rejected two alternatives:

   - `llama-cpp-python` would build llama.cpp from source during
     `uv tool install`, because PyPI has no Metal wheels for it, and it would
     load a second native runtime into voxtype's process next to MLX.
   - Ollama is excluded by policy.

   The user installs the binary with `brew install llama.cpp`, and the model
   downloads on first use. This replaces the "optional extra" idea: the base
   install stays exactly as light as it is now, and neither the binary nor the
   model is fetched unless `post_process = true`.

2. **Where it runs in the pipeline.** Cleanup runs in `VoiceTypeApp._type`,
   after the regex filler and stutter cleanup and before the text is recorded
   for paste-again or the clipboard and typed. Submit phrases, the stop phrase
   and the wake word are all matched earlier, in `handle_utterance`, on the raw
   transcript. Cleanup therefore cannot break them; for example, S1-mini
   turning "go" into "Go." no longer matters.

3. **Never block, never lose.**

   - The server starts in a background thread at launch, which also downloads
     the model if it is missing. Takes that arrive before it is ready are typed
     raw, and voxtype logs one status line saying so.
   - Each request has a deadline, `post_process_timeout` (default 4 s).
   - Cleanup runs on the thread that delivered the transcript, outside the app
     lock, so the hotkey and overlay threads are never held up.
   - The raw text is typed whenever any of these happen: a connection error, an
     HTTP error, a timeout, `finish_reason == "length"`, empty output, output
     with assistant-style chatter, output longer than Muesli's expansion
     limits, or output shorter than 35% of the input.
4. **Long takes are split into chunks** of about 250 words or fewer at
   sentence boundaries, since Parakeet already punctuates. Each chunk is
   cleaned on its own within the shared deadline. A chunk that fails is typed
   raw.
5. **English only.** A transcript whose letters are mostly non-ASCII skips
   cleanup.
6. **Memory.** The server runs with `-c 2048 -np 1` and memory-mapped weights,
   and never with `--mlock`. Measured on an M1 Max, the server's resident set
   is about 820 MB, of which only 292 MB is non-file-backed (its physical
   footprint); the rest is the mapped model file. If `keepalive_minutes` is
   above 0, a tiny request is sent after that much idle time so the model stays
   warm, matching what the Parakeet keepalive does for the speech model.
7. **Lifecycle.** The server process is terminated when voxtype exits. A pid
   file at `~/.cache/voxtype/llama-server.pid` lets the next launch kill a
   server orphaned by a crash, but only after checking that the process's
   command line is our own.

## Config keys

| Key | Default | Meaning |
|---|---|---|
| `post_process` | `false` | Turn LLM cleanup on |
| `post_process_style` | `"semi-formal"` | casual, semi-casual, semi-formal or formal |
| `post_process_structure` | `"prose"` | prose or lists |
| `post_process_context` | `"general"` | general or email |
| `post_process_timeout` | `4.0` | Seconds per take before typing it raw |
| `post_process_model` | `""` | A local `.gguf` path; empty means the pinned S1-mini download |
| `post_process_server` | `"llama-server"` | Name or path of the llama.cpp server binary |

`voxtype post-process "text"` (or the transcript on stdin) runs the same
pipeline once and prints the result with its timing. It is useful for trying
out styles, and it also downloads the model ahead of time.

## Spike measurements (M1 Max, Metal, Q4_K_M, warm)

Median added latency was about 60 ms for 12 words, about 170 ms for 20–24
words, and about 570 ms for 64 words. Across 30 requests, the 50th percentile
was 176 ms and the 95th percentile was 546 ms. The final end-to-end numbers
are in the pull request.
