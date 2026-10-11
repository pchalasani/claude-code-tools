# voxtype plugin

Guides you through installing, configuring, and launching
[**voxtype**](https://pchalasani.github.io/claude-code-tools/tools/voxtype/) —
local, on-device voice dictation for macOS that types wherever your cursor is.

This plugin ships a single skill, `voxtype-install`. Ask your agent
"help me install voxtype" (or "set up voice dictation") and it walks you
through `uv tool install voxtype` → `voxtype setup` → launch, including the
macOS permission steps.

## Getting the skill

One command, any agent (Claude Code, Codex, Cursor, Gemini CLI, …):

```bash
npx skills add pchalasani/claude-code-tools -s voxtype-install
```

That installs just this skill (not the rest of the repo) into the current
project; add `-g` to install it for every project. Then ask your agent:
"install voxtype and set it up".

The `voxtype skill` CLI command (once voxtype is installed) does the same
for Claude Code and Codex via their plugin marketplaces; the marketplace
route is still available as a fallback:

```bash
# Claude Code
claude plugin marketplace add pchalasani/claude-code-tools
claude plugin marketplace update cctools-plugins   # refresh if already added
claude plugin install voxtype@cctools-plugins

# Codex
codex plugin marketplace add pchalasani/claude-code-tools
codex plugin marketplace upgrade cctools-codex-plugins  # refresh if already added
codex plugin add voxtype@cctools-codex-plugins
```
