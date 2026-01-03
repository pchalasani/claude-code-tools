# Changelog

## 1.4.2 - 2026-01-03
- Show first user message in TUI preview pane (fallback to first message).
  - Fixes issue #34 - avoids showing AGENTS.md preload in preview.
  - Thanks to @shanelindsay for the contribution!

## 1.3.9 - 2025-12-30
- Add `first_user_msg` and `total_tokens` to `aichat search --json` output.
- Index the first user message content and total token count from session JSONL.
- Include `first_user_msg` and `total_tokens` in YAML exports for indexing.
