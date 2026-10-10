# Ask another session

Run the standalone helper with an installed `claude-code-tools` environment
(`aichat` must be on PATH):

```sh
python3 scripts/ask-session.py send SESSION --agent codex --fork -m "Question"
```

Use `--home PATH` to select a nondefault account directory.

Codex requires a CLI with native `codex exec fork` support, confirmed in
0.162.0. Check `codex exec fork --help`. Older clients fail visibly; there is
no manual transcript-copy fallback. Without `--fork`, the helper uses
`exec resume`; Codex requires explicit `--force` for that operation.

The helper runs Codex read-only by default. It prints the final answer to
stdout and the native `thread.started` session ID to stderr. `--raw` prints
Codex JSONL events instead. A nonzero exit, timeout, or missing/empty final
answer is a failure, including in raw mode. Claude retains its existing
resume/fork and helper-marking behavior.

This repository copy does not automatically replace an installed skill.
After verification, update the skill's helper file from
`scripts/ask-session.py`, or point its invocation directly at this checkout.
No package entry point or installation hook is added.
