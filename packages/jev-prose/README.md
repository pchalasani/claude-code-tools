# jev-prose

Check English prose for specific editorial patterns using Jev. The writer
supplies a sentence or paragraph; the CLI evaluates a versioned question bank
and returns probabilities and rewrite guidance. It does not infer authorship,
generate rewrites, or certify factual accuracy.

## Install

From this repository:

```bash
uv tool install ./packages/jev-prose
jev-prose install-skill --target both
```

The question bank ships inside the package and works from any project. The
second command installs the writing-loop skill globally for Claude Code and
Codex. Existing different skills are preserved (use `--force` to replace this
skill deliberately). New sessions discover it. The same skill ships in the
repository's writing plugin; either installation method works.

Default backend: Jev through Cloudflare Workers AI. Set
`CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. Existing Wrangler logins
also work (`wrangler auth token`, or an already-installed `npx wrangler`).
The fallback uses a neutral Wrangler working directory, so repo-local `.env`
files and Wrangler config cannot replace global credentials. An explicitly
inherited `CLOUDFLARE_API_TOKEN` still takes precedence; if it is stale or lacks
access, correct or unset it in the calling environment. Do not print its value.
For existing sysone users, the account ID can come from
`[cloudflare] account_id` in `~/.config/sysone/config.toml` or `$SYSONE_CONFIG`.
Cloudflare third-party inference uses AI Gateway credit; HTTP 402 means check
that balance. Credentials are never embedded in reports.

Alternatively, use `--backend typesafe` with `TYPESAFE_API_KEY`, or
`--url https://your-server/v1/systemone` with optional `JEV_PROSE_API_KEY`.
`--model` selects a model/version; defaults are `typesafe/jev` on Cloudflare
and `jev-latest` elsewhere. Compatible local servers are supported, but their
accuracy and probabilities need separate evaluation.

## Check prose

```bash
jev-prose check paragraph.md > findings.json
printf '%s\n' 'The worker retries failed requests twice.' | jev-prose check
jev-prose check draft.txt --profile formal --audience 'API users' --format text
jev-prose questions > question-bank.json
```

Exit codes: **0** complete with no findings, **1** complete with findings,
**2** error (including invalid input or incomplete responses). With JSON
output, require `ran == true`, `ok == true`, and `answered == expected` before
using the status. `ok` means the check succeeded, not that the prose is clean.
CLI syntax errors use argparse's stderr and exit 2, not a JSON report.

Every selected question receives a noul probability. A probability at or above
`--threshold` (default 0.90) is a finding. `judgments` includes all answers;
`findings` includes IDs, questions, descriptions, and revision guidance.
Reports record bank version/hash, prompt version/hash, model, input hash,
profile, and threshold.
They omit the input prose. No report files are written automatically.

The default sends batches of 24 questions through at most four concurrent
requests. Each batch shares the same state; questions within it are independent.
Use `--batch-size` and `--workers` to adjust. Each request has a bounded
`--timeout`; failures are not retried or silently omitted. The checker waits
for its outstanding requests before exiting.

## Scope and preferences

- `general`: shared prose patterns, with genre-sensitive exceptions.
- `formal`: general checks plus technical-prose clarity/register checks.
- `strict`: formal checks plus optional editorial preferences, including the
  causal clause-joining use of "so". These preferences are not authorship signals.

Use `--voice` for an intended register and `--context context.txt` for surrounding
material or the writing task. Context is not itself checked. The prompt excludes
verbatim quotes, code, and deliberate examples of the patterns. These exclusions
are model judgments, not a parser guarantee. An isolated sentence cannot reveal
whole-document repetition or structure; run another check on a completed section.

Inputs are limited to 24,000 text characters and 8,000 context characters.
Check paragraphs or short sections rather than silently truncating a document.
The CLI sends supplied text/context to the chosen backend and leaves files
unchanged. For a custom bank use `--bank PATH`; IDs, profiles, scopes, and source
mappings are validated before inference.

## Writing loop

The writing plugin's `jev-prose` skill works in Claude Code and Codex. It checks
the current draft, asks the writer to review findings, and allows at most three
revision attempts (initial check plus three rechecks). It preserves facts,
qualifications, substantive claims, quotations, and intended voice, stopping on
clean results,
repeated text, unhelpful feedback, errors, or the revision limit. Remaining
findings are disclosed instead of claiming success. No automatic per-keystroke
hook is installed; invoke the skill or ask an agent to use it while writing.

Source provenance and coverage: [QUESTION-SOURCES.md](QUESTION-SOURCES.md).
Evaluation inputs and limitations: [eval/README.md](eval/README.md).

## Observed limits

The final independent smoke test flagged 8/10 deliberately problematic passages
and 0/10 clean passages, using agent-authored labels. Some obvious marketing
contrasts and assistant chatter were missed. The writing loop can stop with
unflagged awkward prose. See [recorded results](eval/RESULTS.md); these numbers
are not representative accuracy or probability calibration. The 0.90 default
was selected on a separate calibration set. Review a finding before rewriting.

## Development checks

From the repository root:

```bash
PYTHONPATH=packages/jev-prose/src uv run --no-project --with pytest \
  pytest -q packages/jev-prose/tests
uv build --package jev-prose
```

The offline tests use real localhost HTTP servers and isolated skill directories.
Live evaluation is explicit; see `eval/RESULTS.md` for commands and retained data.
The bundled and plugin copies of `SKILL.md` must match (the test suite checks it).
