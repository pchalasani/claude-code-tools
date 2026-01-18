# Proposal: Add Exit Code Extraction to tmux-cli

## Background

Hi! I've been driving tmux with LLMs for quite a while in some very complicated dev-ops environments. Over time, I developed my own tmux toolkit (`sane-*` tools) based on iterative friction-analysis loops. After discovering claude-code-tools, it's clear that this project is better packaged and has wider reach than my personal scripts. Rather than compete or fragment the potential userbase, I'd like to contribute my battle-tested features here.

This is my first proposed feature addition. I'd like feedback on design decisions before submitting PRs out of the blue, and guidance on how I can best contribute to this project.

## The Problem

Currently, `tmux-cli` can send commands and capture output, but has no reliable way to determine if commands succeeded or failed. This is a critical gap for LLM-driven automation:

```python
# Current workflow
controller.send_keys("make test")
controller.wait_for_idle()
output = controller.capture_pane()
# ❌ No way to know if tests passed or failed!
```

Without exit codes, LLMs must:
- Parse output text heuristically (fragile, language-dependent)
- Assume success unless obvious error text appears (unreliable)
- Cannot implement proper error handling or retry logic

This significantly reduces the reliability and repeatability of LLM-driven tasks in tmux.

## Proposed Solution

Add an `execute()` method that wraps commands with unique markers to capture exit codes:

```python
# Proposed workflow
result = controller.execute("make test")
# ✅ Returns: {"output": "...", "exit_code": 0}

if result["exit_code"] != 0:
    # Handle failure reliably
```

**Implementation approach:**
- Uses echo markers (similar to heredoc pattern) to tag command start/end
- Captures exit code via shell's `$?` variable
- Parses markers from captured pane output
- Returns structured JSON: `{output: str, exit_code: int}`

**Why markers improve LLM repeatability:**
- Eliminates output parsing ambiguity (no guessing if "Error" means failure)
- Enables deterministic error handling workflows
- Allows retry logic based on exit codes (transient vs permanent failures)
- Makes multi-step orchestration reliable (abort on failure, continue on success)

## Working Implementation

I've implemented this feature in my fork and have it working in both local and remote modes:

**Branch:** https://github.com/ryancnelson/claude-code-tools/tree/feature/exit-code-extraction

**Key design decisions:**
1. **New `execute()` method** - Doesn't modify existing `send_keys()` / `capture()` for backward compatibility
2. **Helper function composition** - Reusable marker generation and parsing logic
3. **30-second default timeout** - Configurable per-call
4. **Returns exit_code=-1 on timeout** - Consistent with standard timeout behavior
5. **Works in both modes** - Local (panes) and remote (windows)

## Testing Approach

I've tested against live tmux sessions with:
- Success cases (exit_code=0)
- Various failure cases (exit_code=1, 127, etc.)
- Timeout scenarios
- Edge cases (output containing marker-like strings)

**Key implementation learning:** During testing, we discovered that markers can scroll off the visible screen in panes with command history. The solution is to capture pane history (last 100 lines) rather than just the visible viewport. This makes execute() reliable even in long-running sessions with significant scrollback.

**Future optimization:** Consider implementing reverse-pagination for scrollback capture - start with 100 lines, and if markers aren't found, progressively capture more (200, 500, 1000) until found. This would be efficient for short outputs (most common case) while remaining robust for long outputs. Since parsing uses simple Python string operations (not LLM processing), larger captures have minimal performance impact.

## Friction Analysis & Debug Tooling

My existing toolkit includes debug options that record Claude's failing attempts while driving tmux. This captures common mis-cues that LLMs make - things like:
- Commands that fail due to missing exit code detection
- Retry loops that could be avoided with proper status information
- Heuristic parsing failures where structured data would help
- Timing issues where wait_for_idle isn't sufficient

I analyze these logged failures to identify patterns and build guardrails. Exit code extraction is one of the key improvements that emerged from this friction-analysis process. The ability to systematically capture and study LLM-tmux interaction failures has been invaluable for improving reliability.

I'd be happy to contribute insights from this analysis to help improve claude-code-tools' LLM-friendliness over time.

## Future Extensibility

This design opens the door for additional friction-reduction features from my toolkit:
- REPL-specific marker implementations (MySQL SELECT, Python print(), etc.)
- Pre-flight bash syntax validation (prevents syntax errors before sending)
- Retry logic with exponential backoff (reduces manual intervention)
- Command timing metrics (helps identify performance issues)
- Platform detection for cross-environment workflows (macOS/Linux/SSH handling)
- Operation logging for post-mortem analysis (similar to my debug mode)

Each would be a separate proposal/PR after gathering feedback on this initial approach.

## Questions for Maintainers

1. **Design direction** - Does the marker-based approach align with project philosophy?
2. **API naming** - Is `execute()` the right name, or would you prefer `run_command()` or similar?
3. **Return format** - Start with minimal `{output, exit_code}` or include timing from the start?
4. **Testing requirements** - What level of test coverage do you expect for PRs?
5. **Documentation style** - Should I follow any specific patterns for docs updates?
6. **Debug/logging features** - Would you be interested in operation logging capabilities for analyzing LLM behavior?

## Why This Matters

After months of running LLMs against production infrastructure via tmux, I can confirm: **exit code reliability is the difference between "sometimes works" and "production-ready automation."** The friction I've removed from my own workflows - identified through systematic failure analysis and debug logging - can benefit the wider claude-code-tools community.

I'd like to share these battle-scars so others don't have to learn them the hard way.

Looking forward to your feedback!
