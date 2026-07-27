# visual-brief — canvas delta contract

Builds on `a4ed07f`. Work inside `packages/visual-brief/`.

## Purpose and provenance

`docs/claude-code-canvas-sidecar-spec.md` ("Claude Code Canvas Sidecar", Rev 2,
27 July 2026) was written by an agent with no knowledge of this branch. It is
untracked, which is why it does not appear in `git diff main...HEAD`. Most of
what it specifies already exists here, built differently and in several places
better; some of what it specifies has been explicitly rejected.

What the outside spec genuinely contributes — ideas nobody here had built,
queued, or ruled on — is short: the read verbs in P1 (its context-hydration
idea), the artifact-store question (parked in Open questions), and the push
that turned two known notification weaknesses into P4's three concrete
options. Everything else it proposes was already built, already queued,
already parked, or already ruled out. Treat the rest of that document as
corroboration, not as a backlog to mine.

This document is the delta. It contains only:

1. Corrections to the original spec's assumptions, so nobody re-implements a
   subsystem that already ships.
2. Net-new work, ordered P0 through P4.

**Do not modify the original spec.** It stays as written, as the record of an
outside view.

Rulings below marked **DECIDED** come from the visual-brief workstream session
(answers recorded 27 July 2026) and from the human directly. They are settled:
a reviewer must not re-open them, and a finding premised on re-opening one is
not a finding. Items marked **PROPOSED** need the human's nod before anyone
builds them.

### Tree state when this was written

`git status --short -uall` is clean apart from the untracked original spec.
HEAD is `a4ed07f` ("chat-box repair round 2"), one commit past the `4c1fed8`
that earlier notes name. `a4ed07f` contains exactly the two test files that
were sitting uncommitted when the chatbox-repair workflow `wf_d45598e9-c58`
was killed — `browser_support.py` (`landing_at()`, `Browser.read_until()`) and
`test_submission_browser.py`. They are no longer orphaned; see P0.

## What already exists

The original spec proposes these as work. They are built and tested. This
table is a map, not a backlog.

| Original spec | Already implemented as |
|---|---|
| Sidecar daemon (§12) | stdlib HTTP server, loopback, multi-run: `server/daemon.py` |
| Session registry (§12.2) | a run is a directory, scanned live: `server/registry.py` |
| `/api/health` (§12.1) | a `/health` route with a runs digest: `server/routes.py` |
| Report envelope (§10.1) | brief schema, updates/lanes/items: `render/validate.py` |
| `ReportNode.kind` (§10.2) | four fixed depths plus a trust chip; same file |
| Stable anchors (§6.4) | anchor path checked against position; same file |
| Anchored threads (§16.1) | element anchors, typed chronological turns |
| Question queue (§13) | append-only `questions.jsonl`: `server/queue.py` |
| Idempotent replies (§20.8) | parent, anchor and generation gates |
| Browser app (§15) | Solid + Vite, owned cursor, keyboard nav: `frontend/` |
| Progressive disclosure (§2.1) | four navigable layers, one evidence disclosure |
| Monitor bridge (§13.2) | `tail -n 0 -F`, armed before the URL: `SKILL.md` |
| Resume (§17.3) | stateless daemon, disk state, idempotent `serve` |
| Graceful fallback (§6.7) | render errors fall back to the saved page |
| Report identifiers (§17.2) | run ids; thread ids unique document-wide |
| Testing (§23) | 176 pytest tests plus Vitest; paint-level assertions |

Two things the original spec asks for that this implementation does better,
worth knowing before anyone "fixes" them:

- **The page is not blind between a question and its answer.**
  `merge_pending_followups` folds unanswered queue lines into the served
  document in memory, with a synthetic `q-pending-<hex>` id that is itself a
  valid reply target. The original spec has no equivalent.
- **The brief body is never server-rendered as HTML.** It ships as one
  allowlisted `<script type="application/json">` blob, so §15.3's sanitization
  requirement is satisfied structurally rather than by a sanitizer.

## Corrections to the original spec

Each of these is **DECIDED**. Do not build them; do not propose them again.

- **The `sidecar.*` MCP/tool API (§9) and the `ReportNode`/`kind` schema
  (§10.2).** Superseded. The brief schema is the contract, and the transport
  is the existing CLI plus the net-new verbs in P1. A second schema would mean
  a migration of live data for no gain.
- **The daemon module layout, session registry, session lock, and input
  arbiter (§12).** All of it exists to arbitrate two writers into one Claude
  session. There is one writer. Nothing has been decided about SDK mode, so
  there is nothing to arbitrate.
- **The Agent SDK bridge and canvas-first Mode B (§8.2, §14), the browser
  free-form prompt composer (§15.1.5), and permission / `AskUserQuestion`
  brokering (§14.4).** The long-term vision — "an interface for working with a
  coding agent, not a report viewer" — is recorded in the iteration-3 contract
  and is not scheduled. The do-not-build list from that contract stands: no
  command palette, plugin or action registry, event bus, router, generalised
  keymap layer, state-management library, websocket channel, or undo stack.
  **A reviewer finding speculative machinery built for this future should
  report it as blocking.** Deferred is not dead: two future-proofing
  decisions are already bought — selection as application state, and
  composition always has a target — and P3 is the first real step in this
  direction. Scheduling canvas-first itself is the human's scope call alone;
  a future spec that opens it adds a phase, it does not contradict this one.
- **Security requirements beyond the settled threat model (§21).** The threat
  model is one trusted local user on loopback. No auth tokens, no `Origin` or
  CSRF defence, no rate limiting, no TLS. A hand-edited local file is not an
  attack; reachable-from-the-page is the bar. The loopback bind stays a
  constructor invariant.
- **`~/.local/share/claude-canvas/` and the SQLite projection (§17.1).**
  `~/.claude/visual-brief/runs/<run-id>/` with four files stands, and storage
  stays file-based. There is live data in that layout.
- **`MessageDisplay` (§19).** The original spec already says it is not
  required. Nothing to build; drop it from consideration entirely.
- **Highlight and quote anchors (§16.2 `TextAnchor`, §23.2 case 4).**
  **Parked by the human — "much later, not urgent."** The anchor union already
  reserves `{kind:"quote"}` and rejects it with a named error, so adopting it
  later migrates no saved thread. Present it as parked, never as near-term
  work.
- **The artifact store (`attach_artifact`, §9, §10.3).** The forensics layer
  carries raw evidence inline, verbatim and untrimmed, because "length costs
  you nothing here". No artifact store is scheduled. It survives only as an
  open question at the end of this document.

## P0 — Housekeeping

Small, blocking, and neither item is a feature.

### P0.1 Close out the killed workflow — DECIDED

The chatbox-repair workflow `wf_d45598e9-c58` was killed mid-flight when its
Claude Code process exited. Its partial work is now committed at `a4ed07f`.
Two things remain:

- **Confirm nobody resumes it twice.** Resuming via
  `resumeFromRunId: "wf_d45598e9-c58"` and taking the spec over by hand are
  both viable and mutually exclusive. Coordinate with the human before either.
- **Review `a4ed07f`.** It is the last edit to the tree and, by the repo's
  review gate, unreviewed in its current form. Send that diff through a
  cold-diff review before any new work lands on top of it.

Acceptance: the human has said which path the workflow takes, and `a4ed07f`
has been read by a reviewer who did not write it.

### P0.2 Sync SKILL.md, live → packaged — DECIDED

The live `/Users/pchalasani/.claude/skills/visual-brief/SKILL.md` is
**authoritative**. It carries three subsections the packaged
`packages/visual-brief/SKILL.md` lacks:

- Write conversations in the thread format, never the legacy pair format
  (legacy pairs are filed at the 1970 epoch and cannot match their queue line,
  so the human sees duplicated, misdated conversations).
- Copy queued text into the page **byte-for-byte** (the fold matches on exact
  text; tidying one comma makes the match fail silently and the badge lie).
- The answer must live on the page, complete — never a pointer to the
  clipboard, the terminal, or a file.

The live copy is also stale in one place: it says the page has an "Ask"
button. "Chat" won, by the human's decision from the page.

Do this when the tree is quiet: copy the three subsections into the packaged
skill, and fix "Ask" → "Chat" in the live copy in the same pass.

**This is the only sanctioned write to `~/.claude/skills/visual-brief/`.**
Outside this one task, build agents must not touch that directory or
`~/.claude/visual-brief/runs/` — both hold the human's live data.

Acceptance: `diff` between the two copies is empty, and neither says "Ask".

## P1 — Typed write-path CLI verbs

**DECIDED, and the centrepiece of this delta.** Everything below about *which
four verbs exist and what they guarantee* is settled. Argument syntax is
marked **PROPOSED** where noted.

### Why

Hand-editing `content.json` produced three distinct data failures in a single
day: a paraphrased fold that silently broke the queue match, an answer that
pointed at the clipboard instead of living on the page, and a legacy-format
write that landed a whole conversation at the 1970 epoch and had to be
repaired by hand. Every one of them is a class of error a validating verb
makes impossible.

**Hand-editing `content.json` must never again be the documented write path.**
Once these verbs exist, SKILL.md commands them.

### Shared contract — DECIDED

Every verb, without exception:

- **Validates.** The whole document goes through `validate_document` before
  anything is written. On failure nothing is written at all, and the anchored
  `ValueError` is printed verbatim after `error: ` on stderr — the existing
  style, e.g. `updates[0].lanes[0].items[0].forensics must be a list`.
- **Writes atomically.** `mkstemp` + `os.replace` through the existing
  `_run_output_file` guard, which refuses to replace a symlink. Same mechanism
  as `render` and `new`.
- **Re-renders on success.** `index.html` is rewritten and
  `meta.json.updated_at` touched, exactly as `visual-brief render` does, so
  the open page reloads within its five-second poll. No verb leaves the page
  disagreeing with the file.
- **Exits 0 on success and 2 on any failure.** `main()` already maps
  `CliError`, `ValueError` and `RuntimeError` to `error: {message}` and 2, and
  argparse uses 2 for usage errors. Do not add failure codes; nothing consumes
  them and the anchored message is the contract.
- **Preserves the file's shape.** `json.dumps(..., ensure_ascii=False,
  indent=2) + "\n"`, matching `_initialize_run`.

Non-goal: cross-process write coordination. Two agents writing one run is not
a supported configuration. A browser reply racing a verb is already handled —
the daemon's `content_generation` gate returns 409 and the page refreshes.

### `answer` — append an agent turn to a thread

DECIDED: the verb, and that it appends rather than replaces.
PROPOSED: the flags.

```
visual-brief answer <run-id> --thread <thread-id> [--text TEXT] [--at ISO]
```

- The body comes from **stdin** by default. Answers are long, multi-line, and
  full of quotes, backticks and shell metacharacters; argv is the wrong
  channel for them. `--text` exists for one-liners.
- `--thread` alone is sufficient: `validate_document` already enforces
  `question thread ids must be unique` document-wide, so no anchor path is
  needed to locate a thread.
- A `q-pending-<hex>` id must fail with a message naming `fold` as the fix.
  Those ids exist only in the daemon's in-memory merge and are on no disk.
- `--at` defaults to now. **PROPOSED:** when now is earlier than the thread's
  newest turn, clamp forward to that turn's timestamp rather than failing —
  existing turns are never rewritten, and `_validate_turns` rejects
  non-chronological turns outright.
- On success, print the row id the answer landed on:
  `<update>/<lane>/<item>#<thread-id>`.

### `fold` — fold queue lines into the page, byte-for-byte

DECIDED: the verb, byte-for-byte copying, and carrying the queue timestamp.
PROPOSED: the selection flags and the folded thread id.

```
visual-brief fold <run-id> [--all | --thread <thread-id>]
```

- **Reuse `merge_pending_followups`.** The daemon already computes exactly
  which queue lines are unfolded and what thread each would become. `fold`
  must produce that same result on disk, so folding is visually a no-op: what
  the page showed as pending becomes what the page shows as saved.
- **Byte-for-byte.** The `text` field is copied from the queue record exactly
  as stored. No strip, no case fix, no typo fix, no re-encoding. Note that
  `queue.py` already stripped the text on ingest, so the stored bytes are
  canonical.
- **The queue timestamp travels with the turn.** `at` is the record's own
  `timestamp` (millisecond precision, `Z`), never the fold time. A fold that
  would break chronology fails loudly with the anchored path; it must never
  reorder existing turns to make room.
- A record with `parent_id` null becomes a new thread on its anchor; a record
  with `parent_id` set appends a `human` turn to that thread.
- **`fold` never touches `questions.jsonl`.** It is idempotent by
  construction: a second run finds nothing unfolded, because "unfolded" is
  computed by matching saved content against queue lines.
- **PROPOSED:** give a newly folded thread the same `q-pending-<hex>` id the
  merge already synthesised, so a reply naming that id still resolves after
  the fold with no alias needed. Check this against `pending_aliases` in
  `server/counting.py` before adopting it.

### `rewrite-now` — replace the Now panel in place

DECIDED: the verb, in-place replacement, fresh timestamp.
PROPOSED: the timestamp format and the thread-survival rule.

```
visual-brief rewrite-now <run-id> [--timestamp LABEL]   # update object on stdin
```

- Reads one complete update object from stdin and replaces the update whose id
  is `now`. If none exists, it creates one. File position is cosmetic — the
  frontend hoists `now` to the top wherever it sits — so keep an existing
  object at its existing index.
- The `id` on stdin must be `now` or absent; any other id is an error naming
  `append-update` as the right verb.
- **PROPOSED:** `timestamp` defaults to local `%Y-%m-%d %H:%M`, rendered by
  the page as "as of ...". The field is a free-form display label and is never
  parsed as a date.
- **PROPOSED, and the sharp edge here:** rewriting in place can delete an
  anchor that carries a question thread, which both destroys the conversation
  and makes its queue line look unfolded again. `rewrite-now` must refuse,
  with an anchored error listing the thread ids whose anchor would disappear,
  unless the caller keeps those ids or passes an explicit override. Decide the
  exact shape of the override before building.

### `append-update` — append a dated history update

DECIDED: the verb and append-only semantics.
PROPOSED: the flags.

```
visual-brief append-update <run-id> [--timestamp LABEL]  # update object on stdin
```

- Appends one complete update object to `updates`. History is append-only and
  is never edited after the fact.
- The id `now` is rejected, naming `rewrite-now`. Duplicate ids are rejected
  by validation already.
- `timestamp` defaults to the same local format as `rewrite-now`.

### Read verbs — PROPOSED, not decided

Neither of these is scheduled. Both make context hydration one command, which
is the one genuinely good idea in §5 and §16.3 of the original spec.

- `visual-brief pending <run-id>` — every unanswered thread and every unfolded
  queue line, one per line, as `<row-id>` + timestamp + first line of text,
  with `--json` for exact text. `server/counting.py` already computes this
  set; the verb would only print it. This is what an agent needs the moment a
  Monitor event fires, and what it needs again after a session boundary.
- `visual-brief show <run-id> --thread <id>` — the exact item and full thread
  as JSON, so an agent answering a question about old content re-reads the
  canonical text instead of trusting compacted context.

Acceptance for P1 as a whole:

- Every verb has tests against real run directories and real files, no mocks.
- A fold test asserts the saved turn's text is byte-identical to the queue
  line's, including trailing whitespace and a deliberate typo.
- A test asserts that a failed validation leaves `content.json` unchanged on
  disk.
- SKILL.md is rewritten to command the verbs, and no longer describes writing
  `content.json` by hand.
- Every touched file stays under 400 lines. `cli.py` is 393 lines today, so
  the verbs go in new modules under `visual_brief/`, not into `cli.py`.

## P2 — Navigation quality of life

**DECIDED** that both features are wanted and queued behind P1. **PROPOSED**:
every key letter below.

### Expand all / collapse all

Today only Space toggles, one row at a time. A human landing on a long brief
wants the whole thing open, or wants it all shut so they can see the shape.

**PROPOSED:** `e` expands every visible row, `E` collapses every row. Shift
meaning "the coarser or opposite variant" is the existing convention (`j`/`J`,
`k`/`K`, `g`/`G`).

### A chats view

`n` cycles **unanswered** conversations only. There is no way to revisit a
conversation that has been answered, which is most of them.

**PROPOSED:** a filter mode restricting the stream to threads holding at least
one `human` turn, keeping their ancestors, cleared by Escape exactly like
search. This is an extension of the existing `filterRows`, not a new
subsystem. `n` keeps its unanswered-only meaning; the filter changes what is
visible, not what `n` means. Key: `C`, next to `c` for chat.

Taken keys, which a proposal must not collide with: `j`, `k`, `J`, `K`, Space,
`c` (with `a` as the hidden legacy alias), `n`, `/`, `g`, `G`, `?`, Escape,
and the arrow keys.

Acceptance:

- Vitest covers the filter and the expand/collapse functions directly.
- A browser test presses the real keys and reads painted state — never
  `activeElement`, never a grep of generated source.

## P3 — The visual design pass

**DECIDED** as direction and as sequence: it comes after P1 and P2. **The
human judges a visual proposal on the page before implementation begins** —
build the proposal as a real brief in a real run, not as a document describing
one.

What the direction commits to:

- Non-linear layout: cards, columns, tiles. The single vertical stream goes.
- A changed-since strip, so the human sees what moved since they last looked.
- Speaker colours in conversations: human turns blue, agent turns neutral ink.
- An explicit dark/light toggle, dark by default, with both palettes tuned
  rather than one derived from the other.
- Evidence stays exactly one level down. Disclosure rearranges detail; it
  never reduces it.

The constraint that will bite: **the cursor must stay coherent in two
dimensions.** `cursor.ts` is pure functions over a flat `Row[]` with
`moveByKind` never wrapping. A tiled layout either keeps that flat order as
the authoritative traversal, or gains real 2-D motion. Decide which before
writing any layout code, and keep `cursor.ts` free of DOM reads either way —
the application owns selection, and nothing there may consult `:focus`,
`document.activeElement`, or scroll position.

The do-not-build list still applies. A grid is a layout, not a framework.

## P4 — Notification-channel hardening

**PROPOSED in its entirety.** Monitor plus `tail -n 0 -F questions.jsonl` is
current, not final, and no replacement has been designed.

Two known weaknesses, both real:

- **The monitor dies with the session.** SKILL.md mandates `persistent: true`,
  arming before the URL is handed over, and re-arming at every session
  boundary — because resuming does not revive a monitor. Nothing enforces it.
- **Rewriting the queue file re-triggers the tail.** This has already caused a
  real duplicate event.

Any proposal must preserve arm-before-URL, per-run isolation, and persistence.
Do **not** propose a daemon that pushes into sessions, a websocket channel, or
hooks machinery — all three are on the do-not-build list or outside the threat
model.

The minimal shape worth considering, in preference order:

1. **Make "never rewrite `questions.jsonl`" an explicit invariant.** Nothing
   in the code truncates, rotates or rewrites it today; the rule just is not
   written down. Pair it with a deliberate `visual-brief archive-queue`
   verb that renames the file aside and creates a fresh empty one, for when a
   run's queue gets long. `tail -F` follows by name and re-opens cleanly.
   (The human already did this by hand once, so the verb paves a cowpath.)
2. **Cover the session-death hole with the read verb rather than a
   mechanism.** One line in SKILL.md — re-arm the monitor *and* run
   `visual-brief pending` once — replays everything missed while nothing was
   listening, with no new file and no new invariant.
3. **A cursor file** recording the last consumed byte offset, only if 1 and 2
   prove insufficient in practice. It is the option that adds state.

## Deferred — already adjudicated

Reviewers: these were decided by the human and are not findings. Re-reporting
one costs a round.

- **Unbounded memory in dashboard pending-merge counting.**
  `_merge_pending_content` materialises all pending records. One local user;
  the fix is bigger than the problem.
- **Parentless stale-anchor validation is accepted, not rejected.** Changing
  it changes API semantics.
- **Pre-upgrade pages get HTTP 409 on submit.** It fails safely and a refresh
  fixes it. The proper fix needs renderer-generation plumbing.
- **Undated same-text legacy pairs can swap identity** on middle or append
  insertion. Genuinely ambiguous without a format change. The prepend case is
  fixed and tested.
- **Chrome startup flake is handled by a bounded retry on browser startup
  only** — never on an assertion.
- **Diagrams and syntax highlighting** are out of scope and have been since
  iteration 2. Never scheduled, never rejected.
- **The repo-derived "where things stand" panel** (commits, gate exit codes,
  files on disk) remains future work. The Now panel is the implemented first
  step.

## Ground rules

Stated once, binding on every section above.

- **The terminal gets a link and at most one line.** Page content is never
  repeated in chat. Two exceptions: a question asked in chat is answered in
  chat, and a thing requested in chat is delivered there.
- **Disclosure rearranges detail; it never reduces it.** Length costs nothing
  on a page where what is collapsed is invisible.
- **Process is never a headline.** Test counts, review rounds, repair
  iterations and lint results are evidence under a claim, never a page title
  or a top lane.
- **Byte-for-byte verbatim folding.** The thread format is required; legacy
  pairs produce 1970-epoch timestamps and duplicate conversations.
- **Zero external requests.** The frontend bundle is committed as package data
  so `uv tool install` needs no Node, and the staleness and tamper stamps stay
  — nothing is tested, built or published against a stale bundle.
- **Loopback is a constructor invariant**, not a convention.
- **Files under 400 lines, absolute ceiling 500.** Python under 88 columns,
  fully type-annotated, google-style docstrings.
- **Real objects in tests, not mocks.** The browser gate fails loudly and
  never skips. Tests assert painted state — never `activeElement`, never a
  grep of generated source.
- **The mid-send retarget guard must never be weakened.** A reply landing on
  the wrong conversation is strictly worse than a lost status line.
- **Works-without-JavaScript was removed deliberately.** Do not re-propose it.
- **Enter-to-toggle is dead.** Space toggles; Enter belongs to the browser.
- **"agent is working" is the exact wording** — "agent", never a product name.

## Open questions

Only the genuinely open ones.

1. **The read-verb surface.** Do `pending` and `show` get built at all, and if
   so does `pending` ship with P1 (it is the same counting code) or wait?
2. **The notification mechanism.** Which of the three P4 options, and is the
   never-rewrite invariant plus an archive verb enough on its own?
3. **`rewrite-now` and surviving threads.** Refuse-by-default with an explicit
   override, or carry threads forward automatically when their anchor id
   survives? The first is louder; the second is friendlier and hides a
   deletion.
4. **P2 key letters.** `e`/`E` for expand and collapse, `C` for the chats
   filter — or something else.
5. **An artifact store.** Forensics carry raw evidence inline today and that
   has not hurt. Is there a size at which inline stops being right? No answer
   is needed until it is.
