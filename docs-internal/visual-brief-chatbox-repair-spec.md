# visual-brief — chat-box repair contract

Builds on `ea6a5f3`. Work only inside `packages/visual-brief/`. This pass has
no new subsystems: it repairs the box the human writes in, renames it, and
fixes the moment an answer arrives. Everything here was either found by tests
or requested by the human from the live page.

## Decisions already made by the human (do not relitigate)

- **Enter-to-toggle is removed.** Space keeps toggling; Enter goes back to the
  browser. The existing test `test_the_page_leaves_enter_to_the_browser`
  becomes correct again — align the code with it, not the reverse.
- **"Ask" becomes "Chat" everywhere.** The button, the reply affordance, the
  form's labels, CSS class names where cheap, and the docs: the human may be
  asking, answering the agent, or steering, and Chat covers all three. The
  keyboard key becomes `c`; keep `a` working as an undocumented alias.
- The queue threat model is unchanged: one trusted local user.

## 1. The six failing tests — fix the behaviour, never the assertions

At `ea6a5f3`, these fail:

```
test_folding_browser.py::test_browser_reply_survives_pending_thread_fold
test_folding_browser.py::test_identical_pending_threads_keep_their_own_replies_when_prepended
test_keyboard_browser.py::test_the_page_leaves_enter_to_the_browser
test_submission_browser.py::test_double_click_sends_one_question_while_request_is_in_flight
test_submission_browser.py::test_escape_during_a_send_still_shows_the_question_landing
test_submission_browser.py::test_a_question_the_daemon_refuses_is_not_lost
```

The three submission tests share a signature: the send's continuation never
paints (no status on refusal, no pending note after Escape, no dedup guard
visible). Diagnosis notes from probing the live page, to save you the hour:

- Opening the box via the keyboard (`a`) works and stays open.
- A real pointer click on the open button reads "closed" at click time and
  "open" moments later.
- A **programmatic** `.click()` opens it and it immediately closes again —
  which implicates the click-bubble path (`selectFromClick` in `app.tsx`)
  and/or hover-selection (`pointer.ts` / `pointAt`) interacting with
  `composer.toggleAt`: the click bubbles to the row-selection handler, the
  cursor moves, and something closes the box that just opened.
- The submit continuation guards on the composer still pointing where the
  send started (a deliberate earlier repair). If selection churn re-targets
  or closes the box mid-send, that guard turns the continuation into a no-op
  — which would produce exactly the three submission failures.

Fix the interaction at its root: opening, targeting, and the in-flight
continuation must be immune to cursor movement caused by the same click or by
hover. Do not fix it by weakening the mid-send guard — a reply landing on the
wrong conversation is strictly worse than a lost status line.

The two folding tests assert a reply survives its pending conversation being
folded into content, including two identical pending questions keeping their
replies apart. Treat them as the contract; the counting/folding code passed
these before the front-end rewrite.

## 2. Requested keyboard behaviour

- **Cmd-Enter sends** from the chat box on macOS; Ctrl-Enter everywhere else.
  Sending any other way keeps working. While focus is in the textarea, plain
  Enter types a newline as it does today.
- **Arrow Down / Arrow Up move between items** exactly like `j` / `k`. The
  human reports arrows do nothing today despite the code claiming aliases —
  find out why (likely the browser scrolls instead) and make them real, with
  a browser test that presses the actual arrow keys.
- `c` opens the chat box on the cursor row; `a` still works, undocumented.

## 3. The moment an answer lands

Two halves of one moment, requested from the page verbatim:

- **While a send or reply is in flight and until the answer arrives**, show an
  animated "agent is working" indicator where the answer will land — a
  shimmer/wave on the text, not static, and honouring
  `prefers-reduced-motion` by degrading to a static label. The wording is
  exactly "agent is working" — "agent", never a product name, so a different
  agent behind the page needs no relabel. The white awaiting chips stay.
- **When the answer lands, it must be seen.** Today the self-reload collapses
  the freshly answered conversation, because rows only hold themselves open
  while *awaiting*. Track what changed since the human last looked (the
  cursor-restore machinery in `reload.ts` already persists state across
  reloads — extend that, don't invent a parallel store): a conversation whose
  answer arrived since the last look opens itself, along with its ancestors,
  and carries a visible "new" treatment until visited. Clearing is by visit
  (cursor lands on it or it is toggled), not by timer.

## Out of scope

The bold non-linear design pass (cards/columns) — direction is recorded, it
comes after this. Select-a-phrase commenting — parked by the human. The
bounded-memory and other previously deferred items. Do not touch
`~/.claude/skills/visual-brief/` (read-only; live prototype) or
`~/.claude/visual-brief/runs/` (the human's live data).

## Verification

- `uv run --package visual-brief pytest packages/visual-brief/tests -q -rs`
  fully green, **no skips**. If Chrome fails to *start*, retry the startup —
  never an assertion.
- `cd packages/visual-brief/frontend && npm run typecheck && npx vitest run`
  green.
- `make visual-brief-frontend` rebuilt and stamped; the committed bundle
  matches sources.
- New behaviour gets browser tests that press the real keys and read what a
  human would see (painted state, not activeElement).
- Every file stays under 400 lines. Python under 88 chars, typed,
  google-style docstrings; frontend files under 400 lines too.

Repo rules: commit each green repair round; never `git add -A`; reference
this file in commit messages.
