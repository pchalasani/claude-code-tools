# voxtype plugin

Guides you through installing, configuring, and launching
[**voxtype**](https://pchalasani.github.io/claude-code-tools/tools/voxtype/) —
local, on-device voice dictation for macOS that types wherever your cursor is.

This plugin ships a single skill, `voxtype-install`. Ask your agent
"help me install voxtype" (or "set up voice dictation") and it walks you
through `uv tool install voxtype` → `voxtype setup` → launch, including the
macOS permission steps.

## Installing this plugin

The quickest way is the voxtype CLI, which sets the skill up in **both**
Claude Code and Codex (each has its own marketplace in this repo):

```bash
voxtype skill
```

Or add it manually per agent — note the marketplace name differs
(Claude reads `.claude-plugin/marketplace.json`, Codex reads
`.agents/plugins/marketplace.json`):

```bash
# Claude Code
claude plugin marketplace add pchalasani/claude-code-tools
claude plugin install voxtype@cctools-plugins

# Codex
codex plugin marketplace add pchalasani/claude-code-tools
codex plugin add voxtype@cctools-codex-plugins
```
