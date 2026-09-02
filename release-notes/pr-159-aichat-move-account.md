# `aichat move-account`: move a session between accounts

New sub-command that moves a Claude or Codex session between account
config dirs — e.g. a personal `~/.claude` / `~/.codex` and a work
`~/.claude-rja` / `~/.codex-rja` (the dirs normally selected via
`CLAUDE_CONFIG_DIR` / `CODEX_HOME`). Useful when a session was started
under the wrong account.

## Usage

```sh
aichat move-account SESSION --to ~/.codex-rja [--from ~/.codex]
    [--agent claude|codex] [--keep]
```

- `SESSION` may be a session UUID (full or partial), a Claude session
  name assigned with `/rename`, or a Codex thread name.
- Without `--from`, all local homes of the agent's kind are searched:
  the env-var home, the default home, and `~/.claude-*` / `~/.codex-*`
  siblings. Ambiguous matches are listed and refused.
- `--agent` is auto-detected from the config dirs (`projects/` means
  Claude, `sessions/` means Codex).
- `--keep` copies instead of moving, leaving the source account
  untouched.

## What moves

- **Claude:** the transcript keeps its
  `projects/<encoded-path>/<uuid>.jsonl` location in the target home,
  and its sidecar dir (subagents, tool-results, workflows) moves with
  it.
- **Codex:** the rollout file keeps its date-based
  `sessions/YYYY/MM/DD/` location, and the session's thread-name
  entries move in `session_index.jsonl`.

Nothing inside the transcript is rewritten: an account move keeps the
same project (cwd). The copy is verified before the source is removed,
and the move refuses to overwrite a session that already exists in the
target account. On success it prints a paste-ready resume command,
e.g. `cd <project> && CODEX_HOME=~/.codex-rja codex resume <uuid>`.

## Files

- `claude_code_tools/move_account.py` — implementation
- `tests/test_move_account.py` — 26 unit tests
- `claude_code_tools/aichat.py` — CLI wiring (`move-account` command)
