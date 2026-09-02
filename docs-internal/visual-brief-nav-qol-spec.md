# visual-brief — navigation QoL and client-resilience contract

Builds on `ed249c1`. Work only inside `packages/visual-brief/`. Do not touch
`~/.claude/skills/visual-brief/`, `~/.claude/visual-brief/runs/`,
`docs-internal/visual-brief-canvas-delta-spec.md`, or anything under `docs/`.

Everything here was requested or reported by the human while using the live
page. Decisions already made are marked DECIDED — do not relitigate.

## 1. Expand all / collapse all

Buttons in the key bar plus shortcuts. Expand-all opens every row to the most
granular level; collapse-all folds everything back to lanes (updates and lanes
visible, lanes closed). Keys: choose against the existing map (`c` chat, `a`
alias, `j/k/J/K`, arrows, `n`, `/`, `g/G`, `?`, space, Escape are taken; the
browser owns Cmd-digits) and show the choice in the key bar and help overlay.

## 2. The chats view

One key plus a masthead button that surfaces **every conversation the human
has written in — answered or not**. After collapse-all those threads are
invisible and `n` only visits awaiting ones; this view is how the human finds
their chats. Reuse the search/filter machinery, filtered to thread rows with
ancestors opened; the same key (or Escape) leaves the view. Cycling with
`j`/`k` inside the view must work.

## 3. Quick jump (DECIDED: the hybrid)

- **Hint mode**: one key enters it; every expandable row sprouts a short
  fixed-length home-row label (single letters until exhausted, then two-letter
  pairs — fixed length per page so no ambiguity, Vimium-style). Typing a label
  moves the cursor there and leaves hint mode; Escape leaves without moving.
  Labels are painted state, not tooltips, and honour both themes.
- **Quiet ordinals**: small muted numbers on rows inside expanded lanes, for
  citing ("item 12") — display only, no input semantics, absent on collapsed
  content.

## 4. Keyboard parity for lane-level chat

Verify — with a real-browser test that presses the keys — that `J`/`K` onto a
lane row followed by `c` opens the lane's chat exactly as its button does. Fix
it if it does not. The rule to enforce and test: **every granularity the mouse
can chat at, the keyboard reaches.** Reflect it in the key bar text.

## 5. Client resilience across upgrades (a stranded tab must self-heal)

Observed in real use: a tab left open across a daemon upgrade stopped
receiving updates while showing the working sign forever; a hard refresh
fixed it.

- The page's poller must treat a **version or format mismatch as a command to
  reload the page**, never as an error to swallow: if `/render-version` (or
  its response shape) is unrecognisable, or a poll succeeds while referencing
  a page generation the server no longer speaks, force `location.reload()`.
- Transient poll failures retry with backoff; only a *successful response the
  client cannot interpret* forces the reload. The poller must be un-killable
  by exceptions: any thrown error inside the poll cycle schedules the next
  cycle.
- The working sign needs a deterministic off-switch that survives id changes:
  match the pending submission to its folded thread by the queue line's
  verbatim text + timestamp, not by any provisional id. If the fold renames
  or re-anchors, the sign still clears when that text+timestamp appears
  anywhere on the page; and a sign with no match after reload plus N polls
  degrades to a visible "submitted — refresh if this persists" state rather
  than spinning forever.

## 6. Post-submit continuity (the jarring repaint)

After a send, the self-reload currently wins the screen: awaiting chips bloom
at every level in one frame, the working sign the human was watching is
replaced, and their place is lost.

- Across the post-submit reload, anchor the viewport to the conversation the
  human just wrote in: the restored cursor lands on that thread and the
  scroll keeps it in reading position (the cursor-restore machinery already
  persists across reloads — extend it, do not build a parallel store).
- The working sign renders continuously across the reload (already stored;
  make sure the repaint does not flash it away and re-add it).
- Soften the chip bloom: the awaiting chips for ancestors of the just-sent
  conversation may appear, but must not restyle the entire page in one
  visible jolt — stagger or transition them, honouring prefers-reduced-motion.

## Out of scope

Turn colours and the theme toggle (design pass, next). Select-a-phrase
commenting (parked). Bounded memory and other standing deferrals. The
frontend bundle must remain single-file, zero external requests.

## Verification

- Full Python suite green, no skips (`-q -rs`); Chrome startup retries stay
  bounded to startup only. Frontend typecheck + vitest green. Bundle rebuilt
  and stamped via `make visual-brief-frontend`.
- New behaviour tested by pressing real keys and asserting painted state:
  hint labels visible and functional; expand-all/collapse-all reflected in
  open-state counts; the chats view shows exactly the human's threads; lane
  chat opens from the keyboard; the poller reload triggers on a simulated
  version-shape change; the working sign clears on text+timestamp match and
  degrades rather than spinning forever.
- Every file under 400 lines; Python 88-char typed google-docstring rules;
  real objects, no mocks; tests never touch the live runs directory.

Repo rules: commit each green round; reference this file in commit messages.
