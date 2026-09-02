# visual-brief — iteration 2 contract (keyboard control + threaded follow-ups)

Builds on the package landed in commit `f304ad5`
(`docs-internal/visual-brief-iter1-spec.md`). Work only inside
`packages/visual-brief/`.

Iteration 2 is the two interface changes the human feels every session, plus a
schema decision made once now so iteration 3 does not have to migrate saved
questions a second time.

## Out of scope — do not build

Select-text-to-comment (iteration 3) · freshness stamps and the computed "where
things stand" panel · diagrams · syntax highlighting · installing over
`~/.claude/skills/visual-brief/`, which stays untouched and is serving a live
session.

## 1. Keyboard control

The page is currently mouse-only. Terminal users are not. Add a visible focus
ring and these bindings. **The mouse must keep working exactly as it does now,
and every key must have a visible equivalent on the page.**

| Key | Does |
| --- | --- |
| `j` / `k` | Move focus to the next / previous item |
| `J` / `K` | Move focus to the next / previous lane |
| `space` | Expand or collapse whatever has focus |
| `a` | Ask about the focused item or lane (focuses its question box) |
| `n` | Jump to the next thread awaiting an answer |
| `/` | Search the page |
| `g` / `G` | Jump to top / bottom |
| `?` | Show the key list |
| `Escape` | Close the help or search, or leave a text box |

Requirements that are easy to get wrong, so they are stated as tests:

- **Never hijack a key while the human is typing.** When focus is inside a
  textarea, input, or any `contenteditable`, every binding above is inert —
  pressing `a` types the letter `a`. This includes `/`, `?`, `space` and `n`.
- **Focus must be visible at all times** and must survive a re-render: after the
  page reloads itself on new content, focus returns to the same item id if it
  still exists, otherwise to the nearest surviving ancestor.
- Moving focus to an item inside a collapsed lane opens that lane.
- `n` cycles through threads awaiting an answer in document order and wraps.
  With none awaiting an answer it does nothing visible and does not throw.
- `/` filters items to those matching the query and shows a match count;
  `Escape` restores the full page. Search must not execute the query as HTML.
- The help overlay is reachable by mouse too (a visible `?` control), traps
  focus while open, and closes on `Escape`.
- Keyboard support is a JavaScript enhancement: with JavaScript disabled the
  page must still render and the `<details>` disclosure must still work.
- Every focusable element carries correct `aria-expanded` / `aria-controls`, and
  the focus ring is not removed for keyboard users.

## 2. Threaded follow-ups

Today a question carries exactly one answer, so a conversation cannot continue.
Give each question a stable identity and a list of turns.

### Schema

Replace the `{question, answer}` pair with a thread object:

```json
{
  "id": "q-3f9a2c",
  "anchor": {"kind": "element", "path": "update-id/lane-id/item-id"},
  "turns": [
    {"author": "human", "text": "…", "at": "2026-07-25T19:08:29Z"},
    {"author": "agent", "text": "…", "at": "2026-07-25T19:11:02Z"}
  ]
}
```

- `id` is stable across re-renders; never renumber an existing thread.
- `author` is `"human"` or `"agent"`.
- `anchor.kind` is `"element"` in this iteration. **Model it as a tagged union
  now** so iteration 3 can add `{"kind": "quote", "quote": …, "prefix": …,
  "suffix": …, "nearest_id": …}` without rewriting saved threads. Validate
  unknown kinds with a clear error rather than crashing; do not implement
  `"quote"`.

### Backward compatibility (required — real data exists)

`content.json` files in the wild contain the old `{question, answer}` pairs,
including the one serving the live session right now. On read, convert a legacy
pair into a thread with a generated stable id and one or two turns (`human`,
then `agent` if an answer is present). Never mutate the human's file as a side
effect of rendering; conversion happens in memory. `visual-brief render` must
succeed on a legacy file unchanged.

### Rendering

- Turns render in order, oldest first.
- The reply box sits **under the newest turn**, not at the top of the thread.
- A thread whose newest turn is from the human is *awaiting an answer*: it opens
  itself, and its item and lane open too, carrying the existing "Answered"
  treatment (an unread thing should never be hidden behind a closed disclosure).
- Threads whose newest turn is from the agent stay collapsed by default.

### Server and queue

- `POST /ask` accepts an optional `parent_id`. With it, the queue line records a
  follow-up on that thread; without it, a new thread.
- The queue line gains `parent_id` and keeps every existing field, so an older
  reader does not break.
- Question text stays untrusted: escaped on render, never executed, never
  interpolated into a path or a command.

### Unanswered counting

Replace the iteration-1 placeholder in `registry.py`. A run's awaiting-answer
count is the number of threads whose newest turn is from the human, computed
from `content.json` plus any queue lines not yet folded in. The dashboard badge
and `visual-brief list` both use it.

## 3. Two small carry-overs

- **`visual-brief new` prints the wrong port.** It hardcodes 8765 even when the
  daemon runs elsewhere. Print URLs that reflect the port that will actually be
  used, or omit the port rather than state a wrong one.
- **Package the skill contract.** Copy `~/.claude/skills/visual-brief/SKILL.md`
  into `packages/visual-brief/SKILL.md` and update its build steps for the new
  CLI (`visual-brief new` / `serve` / `render`) and the two URL forms. Preserve
  its first two sections verbatim in meaning — the rule that page content is
  never repeated in the terminal, and the rule that the question watcher is
  armed with a `Monitor` before the URL is handed over. Those two rules exist
  because both failures happened repeatedly in real use.

## Verification

- `uv run --package visual-brief pytest packages/visual-brief/tests -q` green,
  with new tests for every "easy to get wrong" bullet above, thread ordering,
  legacy conversion, and awaiting-answer counting.
- The rendered example still makes zero external requests
  (`grep -E 'https?://'` finds nothing).
- Disclosure still works with JavaScript disabled.
- The daemon still binds `127.0.0.1` only.
- Rendering the live legacy file at
  `~/.claude/skills/visual-brief/demo-run/content.json` succeeds — copy it
  somewhere writable first; do not write into the skill dir.
- Every file stays under 400 lines; none may exceed 500.

Repo rules: Python lines under 88 characters, google-style docstrings, full type
annotations, pytest with real objects rather than mocks.
