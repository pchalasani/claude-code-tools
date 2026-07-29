# brief v2 — frontend rewrite contract

Worktree `~/Git/claude-code-tools.feat-brief-v2`, branch `feat/brief-v2`,
base `060d90e`. Work only inside `packages/visual-brief/`. Never touch
`~/.claude/skills/visual-brief/` or `~/.claude/visual-brief/runs/` — live tool,
live data, a page the human is reading. Tests write to tmp_path only.

## Why a rewrite

The v1 frontend (12.6k lines) owns cursor, fold state and view modes as global
state that must be *reconciled* against every document change, and views write
into that state (`m` opens ancestors, search clears itself, publishes run
carry-over). Every bug the human hit in two days of real use — disappearing
chats, a destroyed search, drafts lost to navigation, folds moving under the
reader — lives in that reconciliation. v2 keeps the same features and inverts
the ownership so there is nothing to reconcile.

The Python package (5k lines: daemon, routes, typed verbs, fold/answer,
validation, linter, `/document`, assets stamp) is verified and UNCHANGED. The
document format is unchanged. There is no data migration.

## The four invariants

1. **One reactive document, everything derived.** The document lives in a
   keyed-reconcile store (port `live-document.ts` as-is). Rows, counts,
   outstanding-ness, ages: all lazy derivations. No derived thing is ever
   stored and reconciled.

2. **Human state is four small maps, written ONLY by human actions:**
   - `chosen: rowId -> open?` — explicit fold choices (space/Enter/clicks; E and
     C write bulk choices);
   - `cursor: rowId | null`;
   - `drafts: rowId -> text`;
   - `seen: threadId -> answerState` — which answers the human has visited.

   Nothing else writes these. Not a publish, not a view toggle, not an unmount.
   There is no carry-over step anywhere in the codebase.

3. **Openness is a pure function with a birth-default cache.**
   `open(row) = chosen[row.id] ?? bornDefault[row.id]`, where `bornDefault` is
   computed ONCE the first time a row is painted in this tab and cached for the
   tab's life: newest update open, lanes open, items open iff they contain an
   outstanding thread, threads open iff outstanding. *Outstanding* = awaiting
   an answer, OR carrying an answer the human has not seen (`seen` disagrees
   with current state). Consequences, all required: a thread answered while
   watched stays open until the human folds or visits it (v1 folded it shut —
   reported bug); rows arriving by live patch get sensible defaults with zero
   machinery; a publish can never change the openness of any existing row.

4. **Views are pure filters.** `m` (my chats) shows only rows on paths to
   threads with human turns; search shows matches plus ancestors. A filter
   affects only what renders this frame: it writes nothing, opens nothing,
   clears nothing. A filter reveals matches even inside folded containers
   without touching fold data. Leaving a view restores the page exactly,
   including an active search entered before `m`. Selecting a filtered-away row
   (structure map, click) drops the filter — that click is a human action.

## Behavioural requirements (the notes file distilled)

`docs-internal/visual-brief-feedback-notes.md` is context; this list is
contract.

- **Keyboard**: `j`/`k`/arrows walk EVERY painted row — update and lane headers
  included — in painted order, both directions, no dead ends at lane
  boundaries. `J`/`K`/shift-arrows jump by lane. `n` cycles outstanding
  threads (awaiting or unseen-answer), labelled "next open chat". `f` hints
  reach every painted row. `Enter` toggles the fold at the cursor; focus is
  never parked on an invisible control; a tabbing reader can still operate
  folds; typing targets swallow all shortcuts. `E`/`C` expand/collapse all.
- **Submit lifecycle, one presentation**: from the send until the answer, the
  human's words render as a REAL turn (same renderer, author, timestamp,
  markdown as a published turn) with the working sign beside it. The fold
  handover changes nothing visible. The sign is continuous — never vanishes
  and returns. After the answer lands the thread stays open, marked as a new
  answer until visited.
- **Drafts belong to the human**: kept per row, survive navigating away,
  folding, other chats, collapse-all, publishes and reloads; restored when the
  composer reopens at that row. Escape on a non-empty box requires a second
  press (or the cancel control) to discard. Only send or explicit discard
  destroys a draft.
- **Waiting**: one rail on the waiting row's left edge (quieter containment
  shade on ancestors), no repeated chips.
- **Chats badge**: counts outstanding conversations, not all-time.
- **Markdown**: everywhere prose renders (glance, summary, explanations, turns,
  evidence), escape-first allowlist; ordered lists keep their written numbers.
  Port `markdown.ts` — it is audited; do not rewrite it.
- **Live patch**: port the v1 poller stack (`reload.ts`, `document-feed.ts`,
  `page-meta.ts`) and its decide/patch/reload-fallback semantics unchanged,
  including the assets-stamp reload and heal-once standoff memory. A publish
  preserves scroll, cursor, folds, drafts, open composer, active
  search/view/overlay, and keeps unchanged rows' DOM nodes (node-identity test
  is blocking).
- **Structure map**: kept, as a pure render of the document; clicking a lane
  selects it even when filtered (drops the filter).
- Updates paint newest first, each with timestamp and human age; append-only.

## Port / rewrite / delete

- **Port near-verbatim**: `live-document.ts`, `document.ts`, `document-feed.ts`,
  `page-meta.ts`, `reload.ts`, `markdown.ts`, `markdown-view.tsx`, `age.ts`,
  styles (trim what dies).
- **Rewrite small**: outline/rows, open(), cursor, keyboard, composer+drafts,
  pending lifecycle, seen/freshness, views/filters, hints, view components.
- **Delete the concepts**: carry-over, view-modes-as-state, fold-set
  reconciliation, `waiting()`/landing machinery beyond what invariant 3 gives
  free.
- **Storage**: sessionStorage only, keys namespaced `visual-brief-v2:<run>`;
  drafts also mirrored to localStorage so they survive a browser restart. The
  two-origin limitation is documented where the keys are defined, not hidden.

## Size and quality

Frontend `src/` (excluding `*.test.*`) under **4,500 lines**, no file over 400.
TypeScript strict; Python rules unchanged (≤88 cols, typed, google docstrings).
Rebuild and commit the bundle via `make visual-brief-frontend` (it must keep
working, stamp included).

Tests: vitest for logic and jsdom paint; the existing Python browser suite
(CDP harness in `packages/visual-brief/tests`) is the behavioural baseline —
update tests only where this contract changes behaviour (continuous walk over
headers, answered-unseen staying open, badge scope, filters writing nothing),
never to make a weak implementation pass. Zero skips, zero failures:

```
uv run --package visual-brief pytest packages/visual-brief/tests -q -rs
cd packages/visual-brief/frontend && npm run typecheck && npx vitest run
make visual-brief-frontend
```

## Scope rulings

Out: the visual redesign proper (colours, dark toggle, cards, hideable panel),
select-a-phrase, Starlight docs, the "Ask" reversion, two-origin draft skew,
storage quota growth, browser-suite flakiness beyond tests this contract adds.
Pre-existing Python behaviour is not a finding. One trusted local user; page
text is untrusted. A finding premised on a ruling is not a finding.
