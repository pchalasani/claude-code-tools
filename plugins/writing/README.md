# writing plugin

Two skills for improving prose quality, both explicit-trigger only (they
never fire on ordinary writing tasks):

- **agent-style** — 21 literature-backed rules for FORMAL technical prose
  (papers, design docs, proposals, READMEs, commit messages). Based on
  [The Elements of Agent Style](https://github.com/yzhao062/agent-style),
  CC BY 4.0.
- **remove-ai-patterns** — remove AI-writing patterns ("AI-isms") from text
  at any register, with voice profiles and a deterministic Node.js
  detector. Wraps
  [avoid-ai-writing](https://github.com/conorbronsdon/avoid-ai-writing)
  (MIT) by Conor Bronsdon.

The two lanes are complementary: agent-style is a clarity/composition
ruleset for formal prose; remove-ai-patterns de-AIs text of any voice.
Pick one as the final gate on a given document; do not interleave them.

## Install

Works in both Claude Code and Codex; note the marketplace name differs
(Claude reads `.claude-plugin/marketplace.json`, Codex reads
`.agents/plugins/marketplace.json`):

```bash
# Claude Code
claude plugin marketplace add pchalasani/claude-code-tools
claude plugin marketplace update cctools-plugins   # refresh if already added
claude plugin install writing@cctools-plugins

# Codex
codex plugin marketplace add pchalasani/claude-code-tools
codex plugin marketplace upgrade cctools-codex-plugins  # refresh if already added
codex plugin add writing@cctools-codex-plugins
```

Then invoke by name in a session: "apply agent-style to this README",
"de-AI this post with remove-ai-patterns", "run the detector and iterate
until clean".

## Vendored upstream content

Each skill vendors a pinned snapshot of its upstream source (rule bodies,
pattern catalog, detector), so the skills work offline with no network or
npm installs; the detector only needs a `node` binary on PATH. Pins are
recorded in `skills/agent-style/references/UPSTREAM-PIN` and
`skills/remove-ai-patterns/upstream/UPSTREAM-PIN`. Maintainers refresh
them with:

```bash
plugins/writing/scripts/update-upstream.sh
```

and review the resulting diff before committing (fetched rule text is
third-party input). Upstream licenses are included alongside the vendored
files.
