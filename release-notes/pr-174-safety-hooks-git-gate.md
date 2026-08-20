# PR #174 — commit allowance that does not decay

## Fix: `CCTOOLS_ALLOW_GIT` replaces a flag file that `/tmp` deletes

The `git commit` gate allowed a commit only while
`/tmp/claude/allow-git-commit.<session_id>` existed. macOS reaps `/tmp` after a
few days, so a session that outlived the reaper started asking for approval
again partway through its life — stalling unattended runs on a prompt nobody is
there to answer.

Set `CCTOOLS_ALLOW_GIT=1` in the environment (for example in the `env` block of
`~/.claude/settings.json`) to allow commits in every session. Nothing has to
survive in `/tmp`, and the setting reaches every process the hook runs in.

Per session, `>allow-git off` writes a deny flag that overrides the environment
variable, and `>allow-git` clears it. `>allow-git status` says which of the
three is in effect.

## Change: staging specific paths is no longer gated

`git add <paths>` never asks for approval, whether the paths are new, modified,
or a named directory. Blanket staging — `git add -A`, `git add .`,
`git add *` — stays blocked, including behind `env -C`, `env -S`, and `GIT_DIR=`
prefixes. `git add --pathspec-from-file` still asks, since the hook cannot read
the path list it would stage.

`>allow-git staging` is gone along with the gate it controlled.
