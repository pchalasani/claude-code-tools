# Brief v2 — behavior fix round

Builds on `b862c22` in the `feat/brief-v2` worktree. Work only inside
`packages/visual-brief/`. Do not change the visual design in this round.

## The required change

The `m` key is a reversible fold-layout toggle. It never filters the document.

When the human presses `m`, or clicks the matching masthead control:

- capture the exact current open/closed layout on the first press;
- expand every conversation written by the human in its existing place;
- expand every ancestor needed to show those conversations;
- leave unrelated content present and otherwise unchanged; and
- on the second press, restore the exact layout captured before the reveal.

Both transitions preserve cursor, scroll position, drafts, seen-answer state,
search query, overlays, and all unrelated human-owned state. A later first press
captures the then-current layout.

If a document patch lands between presses, restore captured state only for row
IDs that still exist. Genuinely new rows keep their normal birth defaults, and
removed rows must not be resurrected through stale stored fold choices.

Implement this as one explicit human fold action backed by the existing state
and navigation abstractions. Remove the chats-only filter and Escape's special
handling for that old mode. The masthead and key-bar controls must run the same
toggle as the `m` key and expose their pressed state accessibly.

The masthead number beside this action is the number of conversations needing
attention: unanswered conversations plus unseen answers. It must not be an
all-time conversation count. Label it "need attention", not "chats".

## Regression gates from earlier field notes

These behaviors appear to be implemented already. Do not redesign them; add or
retain honest tests proving them, and change product code only if a test exposes
a real failure.

- `j`, `k`, and the arrow keys walk one continuous painted-row sequence across
  update, lane, item, conversation, and evidence boundaries.
- An answer that arrives opens its conversation and ancestors until the human
  visits it; visiting it retires the new-answer state.
- The masthead attention count excludes old answers the human has visited.
- A long question stays on one tidy thread-header line with ellipsis, while the
  turn count and chat control remain visible. The full question remains in the
  thread body and in an accessible label or tooltip.

## Tests

Tests must prove the behavior through the mounted application and the real
browser harness, not only through helper functions.

The focused `m` regression starts from collapse-all, manually reopens one lane,
snapshots painted row IDs and all four human-state maps, presses `m`, and proves:

- every human conversation is now painted and open in place;
- every row painted before the command is still painted;
- unrelated rows retain their previous open/closed state;
- the four human-state maps differ only in `chosen`, and only as needed to
  reveal the conversations and their ancestors;
- cursor, draft, seen state, query, overlay, and scroll position are unchanged;
- there is no chats-only filter;
- the second press restores the exact captured layout and human state;
- a later reveal captures the then-current layout; and
- patches between presses restore surviving IDs, leave new rows on normal
  defaults, and do not retain choices for removed rows.

Run the focused frontend tests, the full frontend unit suite, and typecheck,
then rebuild and stamp the generated frontend bundle. Do not run the flaky
Python browser tests in this round.

## Scope rulings

Deferred to the design round: font size and colors, shimmer styling, visual
chat-containment marks, cards, grids, diagrams, and the broader layout. The
cross-origin draft-storage limitation and general browser-suite flakiness are
also outside this focused round.

Do not commit. New implementation files must be staged; changes to existing
files remain unstaged for the human to inspect.
