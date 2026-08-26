# msg First-mate machine contract

Status: unreleased; based on claude-code-tools 1.25.6.

This change adds a versioned `msg.cli.v1` boundary for Claude Code and Codex,
including exact TUI registration identities, atomic retarget, bounded
peek/journal/ack delivery, continuation heartbeats, verified watcher lifecycle,
and a fail-closed maintenance gate.

The paired msg plugins are version 1.15.0. Their native `PostToolUse`, `Stop`,
and `UserPromptSubmit` hooks keep `first-mate.v1` responsibilities in the agent
loop. First-mate deliveries never use watcher tmux injection; legacy behavior
remains the default.

Completed evidence includes real disposable Claude/Codex plugin installation,
Codex hook-hash trust, and native UserPromptSubmit/PostToolUse/Stop lifecycle.

`plugins/msg/release-evidence.json` binds the current contract, exact source
commit, built wheel SHA-256, and plugin payload hashes. The immutable external
version ledger, 1.14→1.15 upgrade, warm-cache negative test, and complete tmux
smoke bundle remain release gates while this branch is unreleased.
