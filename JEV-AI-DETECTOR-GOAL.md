# Jev prose-pattern detector

Build and validate a Jev-powered prose-pattern detector in
`~/Git/claude-code-tools.feat-jev-ai-detector`, an isolated worktree of
`~/Git/claude-code-tools` on branch `feat/jev-ai-detector`.

Derive a versioned, machine-readable bank of narrow detection questions from
the global agent-style and AI-pattern-removal skills. Build a CLI that batches
those questions against supplied prose through Jev and returns actionable
findings, probabilities, and rewrite guidance. Create a reusable skill for
Claude Code and Codex that supports checking and revising prose, with at most
three revision attempts and explicit preservation of meaning.

Make it work for my environment first, organized as a future shippable plugin
without hardcoded usernames or machine paths. Validate with unit tests, real
Jev calls, and end-to-end writing loops covering problematic and clean prose.
Assess false positives and tune detection thresholds. Document setup, usage,
and limitations. Review the final changes, commit, open a PR, and address valid
GitHub Codex review findings until the current PR head is stable.
