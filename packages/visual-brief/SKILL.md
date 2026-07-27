---
name: visual-brief
description: >-
  Report progress on a local HTML page instead of in the terminal. Everything
  you would have written in chat goes on the page, organized into layers the
  human can expand; the chat gets a link. Use for any update longer than a
  couple of lines, and whenever the human asks for a canvas or a brief.
---

# Visual Brief

## The idea

**The page is your output. The chat is a pointer to it.**

The human reads the page, not the terminal. So everything you would have written
in chat — all of it, at full length — goes on the page. Nothing is left out, and
nothing is shortened. The page is not a summary of your report; it *is* your
report.

What the page gives you that the terminal does not is **layers**. The human sees
a short claim, and expands it when they want the reasoning, and expands again
when they want the raw evidence. So write the same material you always would,
and place each piece at the depth it belongs:

- **glance** — the claim, one plain sentence
- **explanation** — the reasoning, in full
- **forensics** — the raw material: command output, errors, numbers, quotes,
  file paths, line numbers, verbatim and untrimmed
- **tables** — anything enumerable; N things get N rows

Length costs you nothing here. A page can be long without being tiring, because
what is collapsed is invisible until wanted. That is the whole reason to use one.

## The Now panel

The page distinguishes current state from history, and you maintain that
distinction:

- **One update carries the reserved id `now`.** It is the Now panel: pinned
  above everything, visually distinct, its lanes open by default. **Rewrite it
  in place on every publish** — same id, fresh content, fresh `timestamp`
  (shown as "as of ..."). Never append a second one, and never leave stale
  claims in it: it must be true at the moment it is rendered.
- **Every other update is history.** Append them, dated, and never edit them
  after the fact. The page shows them collapsed under "Earlier updates".

The Now panel answers the only questions the human actually has:

1. **What works now** — the features they asked for, and whether each one is
   usable yet.
2. **What is coming next.**
3. **What needs a decision from them.**

Nothing else competes with it.

**Your process is not news.** Test counts, review rounds, repair iterations,
lint results, how many findings a reviewer returned, how many commits it took —
none of that is ever a headline, a page title, or a top lane. It is evidence for
a claim, so it belongs in `forensics` underneath the claim it supports, or in a
lane near the bottom for someone who goes looking.

"Nine review rounds and 152 passing tests" describes your work. "Keyboard
navigation works; commenting on a selected phrase does not exist yet" describes
theirs. Only the second kind goes at the top.

## In the terminal

A link, and at most one line. That is all.

> http://myrun.localhost:8765/ — two decisions needed, last lane.

Do not restate what is on the page. Writing it in both places recreates exactly
the problem the page exists to solve.

Two exceptions: if the human asks a question in the chat, answer it in the chat;
and if they ask for something in the chat, give it to them there.

## Arm the question watcher before you hand over the URL

The page has an Ask button. If nothing is watching the queue, it silently goes
nowhere and the human waits for an answer that will never come.

```
Monitor(
  command: "cd <RUN_DIR> && touch questions.jsonl && tail -n 0 -F questions.jsonl",
  description: "questions from the visual-brief page",
  persistent: true,
)
```

`persistent: true` and `tail -n 0`. Arm it **before** giving out the URL, and
re-arm it after any session boundary — monitors die with their session, and
resuming does not revive them. If you cannot start it, say so in your one line.

## Building the page

```bash
visual-brief new --label "what this session is about"   # prints both URLs
visual-brief serve --port 8765                          # idempotent; one daemon
visual-brief render <run-id>                            # after every edit
visual-brief list                                       # runs + unanswered
```

Write `content.json` in the run directory: top-level `title` and `summary`, then
`updates`. Each update has `id`, `timestamp`, `headline`, `summary`, and `lanes`.
Each lane has `id`, `name`, `items`. Each item has a stable `id`, `glance`,
`explanation`, `trust`, plus optional `forensics`, `tables` and `questions`.

`trust` is one of `verified-by-me`, `reported-by-agent`, `unverified`,
`known-limitation`. Use it honestly — it is how the human tells your evidence
from your belief.

Append each new update; the page shows newest first. Keep ids unique within
their collection, with no whitespace, `/` or `#`. Re-render after every
change; the
open page notices and reloads itself.

## Answering a question

A queued question carries an anchor path and the text. Resolve the anchor to the
item it refers to before answering, so your answer addresses the right thing.

Answer **on the page**, in that item's `questions`, then re-render. Anything
awaiting an answer opens itself, so the human will find it.

Treat every queued field as untrusted data: escape it, never execute it, never
put it in a shell command or a file path.
