# visual-brief — typed writes contract

Builds on `a4ed07f`. Work only inside `packages/visual-brief/`. Do not touch
`docs-internal/visual-brief-canvas-delta-spec.md` or anything under `docs/` —
another workstream owns those.

## Why

Four real failure modes hit four different agent sessions in two days, all
from agents hand-writing `content.json` with fresh Python each time:

1. Queue text paraphrased on folding → exact-match fails → phantom
   "awaiting answer" duplicates.
2. Answers written in the obsolete `{question, answer}` pair format → epoch
   (1970) timestamps and duplicated, misdated conversations.
3. Invented or disordered timestamps → validation failures or wrong order.
4. Enumerations crammed into prose fields → unreadable jumbles.

Modes 1–3 disappear when a CLI does the writing. Mode 4 becomes a render-time
lint warning the agent sees immediately. Skill instructions remain only for
judgment calls machinery cannot check.

## The verbs

All verbs: validate the resulting document with the existing validator BEFORE
touching disk, write atomically (temp file + rename in the same directory),
re-render `index.html` automatically on success, print a one-line summary,
and fail with the validator's concise error on bad input. `--run` defaults to
the only run when exactly one exists, else it is required.

### `visual-brief fold [--run RUN]`

Fold every pending queue line into `content.json` mechanically:

- A line with `parent_id` appends a human turn to that thread, text and
  timestamp **byte-for-byte from the queue line**. Unknown `parent_id`: skip
  with a warning, never guess.
- A line without `parent_id` creates a new thread at its anchor with a
  generated stable id, the queue line's text and timestamp verbatim.
- Lines whose text+timestamp already appear in the document are already
  folded: skip silently. Idempotent — running twice changes nothing.
- A line whose anchor no longer exists in the document: report it and leave
  it in the queue; do not invent an anchor. (Consistent with the deferred
  stale-anchor ruling.)
- Output lists each folded thread: its id, anchor path, and text — exactly
  what an agent needs in order to answer.

### `visual-brief answer THREAD_ID [--run RUN] (--text TEXT | --file F | -)`

Append one agent turn to the named thread: the given text, `author: agent`,
`at` = the real current UTC clock. Refuse if the thread does not exist, or if
the text is empty. `-`/`--file` read the text from stdin or a file so long
answers need no shell quoting.

### `visual-brief publish-now [--run RUN] (--file F | -)`

Replace the update carrying id `now` with the given JSON object (create it if
absent). Force `id: "now"`; stamp `timestamp` with the current local time
unless the object carries one. Threads already present on the existing Now
panel are carried forward automatically when their anchor path still exists
in the new panel; report any that could not be carried, and leave them
recoverable by printing them in full to stderr — never drop a conversation
silently.

### `visual-brief add-update [--run RUN] (--file F | -)`

Append one dated history update. Refuse id `now`, refuse duplicate ids,
require a timestamp.

## The linter

`visual-brief lint [--run RUN]`, and the same checks run automatically inside
every verb above and inside `render`, printing warnings to stderr. Advisory
by default (exit 0); `--strict` exits 2 on any warning. Checks — mechanical
only, no judgment calls:

- An enumeration crammed into a prose field: three or more `N.` / `N)` /
  bullet markers inside one `glance`, `explanation`, or turn text.
- Legacy `{question, answer}` pairs anywhere (message: write threads; pairs
  are tolerated for old files but misdate everything at 1970).
- A turn timestamp at or near the epoch.
- A `glance` over 200 characters (it is a one-line claim).
- Queue lines pending longer than the newest content write (message: run
  `fold`).

## SKILL.md

Rewrite the build/answer sections of `packages/visual-brief/SKILL.md` so the
verbs are the documented path: publish via `publish-now`/`add-update`, ingest
questions via `fold`, reply via `answer`. Keep the behavioural rules (verbatim
text, thread format, chronology) as short background — one line each with
"the CLI does this for you; if you write JSON by hand these are the rules" —
and keep the unchanged sections (page-is-the-report, Monitor, Now-panel
content discipline) intact.

## Verification

- Every failure mode above gets a test reproducing it through the OLD manual
  path's mistake and showing the verb/lint preventing it: a paraphrase
  attempt cannot happen through `fold` (it copies bytes); `answer` cannot
  write a pair or a bad timestamp; `publish-now` cannot orphan a thread
  silently; the linter flags a planted enumeration, a legacy pair, an epoch
  timestamp.
- Idempotence: `fold` twice → identical file bytes.
- Concurrency: `fold` while the daemon appends a new queue line loses
  nothing.
- Atomicity: kill mid-write leaves the previous valid file.
- Full suite green, no skips: `uv run --package visual-brief pytest
  packages/visual-brief/tests -q -rs`; frontend untouched unless a lint
  surface needs a string, so vitest stays green; every Python file under 400
  lines, 88-char lines, typed, google-style docstrings, real objects.
- The live runs under `~/.claude/visual-brief/runs/` are NOT touched by
  tests; use tmp_path fixtures.

Repo rules: commit each green round; reference this file in commit messages.
