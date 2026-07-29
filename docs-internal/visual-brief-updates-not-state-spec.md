# visual-brief — updates, not state

Builds on `cded102`. Work only inside `packages/visual-brief/`. Do not touch
`~/.claude/skills/visual-brief/` or `~/.claude/visual-brief/runs/` — that is the
human's live tool and live data, and a page they are reading right now. Do not
touch `docs-internal/visual-brief-canvas-delta-spec.md` or anything under
`docs/`.

Every item here was reported by the human from the live page, or decided with
them in conversation. `docs-internal/visual-brief-feedback-notes.md` is the
running record and is context, not contract.

## The correction this pass makes

The page grew a Now panel: one update carrying the reserved id `now`, rewritten
in place on every publish, meant to hold the current state of the world.

That was a mistake, and the human named it precisely. Maintaining a portrait of
current state is a curation burden an agent is bad at — deciding what is still
true, what to retire, what would overwhelm the reader — and the failure it
produces is exactly what the live page now shows: "what works now" sitting
beside what worked hours ago, old conversations hanging under items whose
meaning has moved on, and no way to tell what arrived just now.

The original intention was narrower and better: **let the agent present its
updates in an easy-to-digest way.** So the primary object goes back to being one
dated update per publish, written to be read once and then left alone — which is
what an agent is actually good at producing, and what it already does in a
terminal. The page then differs from the terminal in one respect only: the words
go through the CLI verbs and land at the top of a browser page instead of
scrolling away.

This deletes machinery rather than adding it. Nothing is rewritten in place, so
there is no reserved id, no carrying conversations across a rewrite, and no way
for a publish to orphan a thread.

## 1. Updates are append-only, newest first

- The reserved `now` id stops being special. There is no pinned panel, no
  "Earlier updates" divider, and no update that is rewritten.
- Updates paint newest first. The newest is open on load; older ones are
  folded.
- Every update carries a visible age next to its timestamp — "4 minutes ago",
  "yesterday" — so what is new is answerable at a glance rather than from
  memory.
- `publish-now` goes away as a rewrite. Publishing means appending, which is
  what `add-update` already does. Keep exactly one verb for it; if the name
  `publish-now` is kept for muscle memory, it must append and must refuse to
  overwrite anything.
- Delete the carry-over machinery that moved conversations onto a rewritten
  panel, and the "not carried" reporting with it. With immutable updates a
  thread's anchor cannot move, so nothing can be orphaned.

**Migration is mandatory and is the risky part.** The human's live run holds an
update with id `now` and eighteen conversations anchored inside it. Nothing may
be lost. Convert an existing `now` update into an ordinary dated update, keeping
its timestamp and every anchor path that threads refer to, so every conversation
still resolves. Prove it with a test built from a document shaped like the live
one: a `now` update with threads at lane level and item level, migrated, with
every thread still reachable at the same anchor afterwards. If an anchor would
have to change, the migration must fail loudly rather than silently rehome it.

## 2. Waiting is shown once, on a rail, not as a badge at every level

The "Awaiting answer" chip is repeated on the conversation, on its item and on
its lane, and the human finds it ugly and noisy. Replace it with a highlight on
the **left rail** of the row that is waiting — a colour, optionally a slow
pulse — so one thing waiting produces one mark, in the place it belongs.

An ancestor containing something that waits may show a quieter version of the
same rail so a folded row does not hide the fact, but it must read as
containment, not as a second alarm.

The "agent is working" sign stays a moving line of text. It is the one thing
that must keep moving.

## 3. The working sign must stop flapping

Reported four times and still true after the reload was removed, so the
previous explanation was wrong and there is no accepted theory now. What the
human sees on sending: the sign appears, vanishes as the awaiting marks arrive,
and returns up to a minute later.

**Diagnose it before changing anything.** Reproduce it in a real browser,
identify the line, and fix that. A fix without a reproduction is not acceptable
here; if it cannot be reproduced, say so plainly and report what was ruled out.

## 4. The cursor walks what the reader is looking at

- `j`, `k` and the plain arrows currently walk rows of kind `item` only
  (`cursor.ts:41`), so they step over every conversation on the page. They must
  instead walk the painted content rows in painted order — items,
  conversations and evidence rows alike. `J`, `K` and the shifted arrows keep
  walking lanes, so the two-level model survives.
- `n` currently reaches only threads still awaiting, which means a conversation
  becomes unreachable by every key the moment it is answered. It must walk
  anything **outstanding**: awaiting an answer, or answered since the human
  last looked. Relabel it in the key bar to say what it does — "next open chat"
  — rather than "Awaiting".
- Within an item, conversations paint newest first, so a question just asked is
  at the top of its item rather than beneath everything written hours ago.

## 5. A half-written message belongs to the human

Typing into a chat box and then opening another row today folds the row shut and
destroys the draft. Nothing but sending it or explicitly discarding it may
destroy what the human typed.

- A draft is kept per row. Navigating away, folding, opening another chat,
  collapsing the page and a publish all leave it intact, and returning to that
  row shows it again.
- Drafts survive a genuine page load, in the same session store the page
  already uses for what it must not lose.
- Discarding is deliberate: Escape on an empty box closes it, and a box with
  words in it needs a second Escape (or the cancel control) to be thrown away.

## 6. The small ones

- **Markdown in the two prose fields that still lack it**: an update's
  `summary` and an item's `glance`, under the same escape-first allowlist the
  rest of the page uses. This is the change the human believed meant markdown
  was broken entirely.
- **A turn beginning with a number loses it.** An answer written as
  `7. And the point ...` renders with the `7.` gone: it parses as an ordered
  list and the marker is dropped. An ordered list must render its own numbers,
  starting from the number written. The human asked what 3+4 was and the answer
  silently disappeared.
- **Enter presses an invisible button.** Enter is deliberately left to the
  browser (`keys.ts:187`) so a keyboard reader can open a fold, which means it
  activates whatever control still holds focus — the masthead's unanswered
  counter, jumping the reader elsewhere. Give Enter a meaning at the cursor, or
  stop leaving focus parked on a control the reader cannot see. Whichever is
  chosen must keep a tabbing keyboard reader able to open folds.

## 7. Scope rulings

A finding premised on one of these is not a finding.

- **Out of this pass:** the visual redesign proper (bigger default text, a
  hideable structure panel, speaker colours, a dark/light toggle, cards and
  columns); select-a-phrase commenting; Starlight docs. The rail in §2 is a
  targeted fix, not permission to restyle the page.
- **The "Ask" reversion** is not to be chased. The assets stamp closes the only
  mechanism ever identified and it has never been reproduced.
- **One trusted local user.** Hand-edited local files are not attacks. Text
  arriving through the page is untrusted and the markdown escaping rules
  continue to hold.
- **Pre-existing behaviour at `cded102`** is not a finding unless this contract
  names it — several items here are deliberately long-standing behaviour.
- **Still deferred:** two-store revision skew, older-daemon text matching,
  storage quota growth.
- Live patching stays as it is. This pass changes what is on the page, not how
  it gets there.

## 8. Repo rules

Python under 88 characters, fully typed, google-style docstrings; pytest with
real objects and no mocks; no file over 400 lines — split rather than exceed it.
The front end is Vite + Solid + TypeScript; rebuild and stamp the committed
bundle with `make visual-brief-frontend` and commit it, because it ships as
package data. This repo hides untracked files, so `git add` every new file at
the moment you create it.

The packaged skill at `packages/visual-brief/SKILL.md` documents the Now panel
and the rewrite-in-place rule. It is now wrong and must be rewritten to describe
appending updates. Do not touch the installed copy at `~/.claude/skills/`.

Verification before any claim of done:

```
uv run --package visual-brief pytest packages/visual-brief/tests -q -rs
cd packages/visual-brief/frontend && npm run typecheck && npx vitest run
make visual-brief-frontend
```

Zero skips tolerated in the Python suite. Where this contract names visible
behaviour, prove it in the browser suite rather than in jsdom alone.
