# PR #175 — `git add` root equivalents are blocked

The hard block on `git add .` was bypassable by naming the same thing another
way. `git add ./`, the repository-root magic `git add :/` and `git add :(top)`,
an empty pathspec, and any absolute path containing the working directory all
stage exactly what `git add .` stages, and are now blocked with it.

Before PR #174 those forms did not reach the block either: they asked for
approval, and only when a tracked file happened to be modified. PR #174 removed
that prompt, so they were allowed outright until this fix.

Paths below the root are unaffected — `sub/`, `./sub`, `:/sub`, `:(top)sub`,
and an absolute path pointing inside the working directory all stage normally,
as do exclusions such as `:!file`.

Reported by the Codex reviewer on PR #174.
