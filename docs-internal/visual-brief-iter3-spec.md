# visual-brief — iteration 3 contract (a real front end, and a cursor you can see)

Builds on `cdb927c`. Work inside `packages/visual-brief/`.

## What this is actually becoming

Build this as **an interface for working with a coding agent**, not as a report
viewer that happens to accept questions.

Today the human uses it to receive updates and ask about them. But the two-way
loop — the agent writes, the human asks, the agent answers in place — is the
seed of something larger: a surface where you follow what the agent is doing,
steer it, and answer it, instead of scrolling a terminal. The briefing is the
first use of that surface, not the point of it.

Two consequences for how you build this iteration, both about not foreclosing
that future:

- **The cursor and the keyboard are the control surface**, not a convenience.
  Treat selection, navigation and input as the application's core state machine,
  designed to carry more verbs later, rather than as three handlers bolted onto
  a document.
- **Composition belongs anywhere.** Today the only thing a human can write is a
  question attached to an item. Do not hard-wire that assumption into the state
  model; the thing being composed should be a first-class concept with a target,
  so that later it can be a reply, a comment on a phrase, a direction, or an
  answer to something the agent asked.

### But build the simple thing first

Read the above as *direction*, not as scope. **This iteration ships one thing: a
briefing page whose keyboard control visibly works.** That is the whole
deliverable. It is small, and it should stay small.

The ambition earns you exactly two decisions, both of which cost nothing today:

1. Selection lives in application state rather than borrowed from the browser.
   (Required anyway — it is the bug being fixed.)
2. The thing being composed has a target, instead of the target being implied by
   which button was clicked. (A field, not a subsystem.)

That is all. Concretely, **do not** build: a command palette, a plugin or action
registry, an event bus, a router, a generalised keymap layer, a state-management
library, a websocket channel, an undo stack, or any abstraction whose
justification is a feature that does not exist yet. If you find yourself writing
infrastructure for the second use case, stop — there is no second use case, and
the previous iterations of this project lost whole rounds to exactly that.

A reviewer finding speculative machinery built for the future should report it as
blocking.

## Why this iteration exists

The keyboard layer passed 152 tests, four review lenses, and real-browser tests
that press real keys — and it does not work for the human. Pressing `j` moves
focus and the page scrolls, but nothing visible changes, so the interface
appears dead while quietly moving underneath them.

The root cause is architectural, not a missing rule. The page borrows the
**browser's** notion of focus (`:focus` on `<summary>`) and hopes it renders
usefully. It does not. Tests asserted that `document.activeElement` changed,
which is true and useless: a human cannot see `activeElement`.

So iteration 3 replaces the hand-rolled enhancement layer with a real front-end
application that **owns its cursor as application state** and paints it
unmistakably. Selection stops being something the browser decides.

## The stack

- **Vite + Solid + TypeScript.** Solid because there is no virtual DOM: the
  cursor moves on every keypress across a document with hundreds of items, and
  fine-grained reactivity is the right tool. It is also small.
- Source in `packages/visual-brief/frontend/`.
- Built to **one JS bundle and one CSS bundle**, both **inlined** into the
  generated page by the Python renderer. The page remains a single file that
  makes **zero external requests** — verify with
  `grep -E 'https?://' index.html` returning nothing.
- The build output is **committed** to the repository, and the Python package
  ships it as package data, so `uv tool install visual-brief` needs no Node.
  This repo already does exactly this for `node_ui` via `hatch_build.py`; follow
  that pattern rather than inventing another.
- `make visual-brief-frontend` builds it; the existing test target must fail
  loudly if the committed bundle is stale relative to the sources.

**Content flow:** Python still owns validation, thread normalization, legacy
conversion, and counting — all of that is reviewed and working, do not touch its
logic. The renderer now emits the validated document as an embedded JSON blob
plus the inlined bundle. Solid renders the document.

**Dropped deliberately:** works-with-JavaScript-disabled. It protected a
scenario that does not exist — a local daemon serving a page to one person on
their own machine — and it is incompatible with owning the interaction model.
Remove the requirement from the tests rather than pretending to satisfy it.

## The cursor — the point of the whole iteration

**The application owns a selected-item index in state.** Never `:focus`, never
`document.activeElement`, never the browser's scroll anchoring.

Non-negotiable properties:

- **Visible without hunting, always.** A solid left rail on the selected row,
  a tinted row background, and a clear contrast step from everything around it.
  If a screenshot of the page requires study to locate the cursor, it is wrong.
- **Never jammed against the viewport edge.** Keep it in comfortable reading
  position — `scroll-margin-block` around 35vh — so moving the cursor moves the
  page around the cursor, not the cursor to the edge of the page.
- **Survives re-render.** The page reloads itself when content changes; the
  cursor returns to the same item id, or the nearest surviving ancestor.
- **Moves with motion that explains it.** Use the View Transitions API for
  cursor movement and for expand/collapse, so the eye can follow what happened.
  Respect `prefers-reduced-motion`.
- **The mouse and the keyboard drive the same state.** Clicking an item makes it
  the cursor. There is exactly one selection model.

Bindings, unchanged from iteration 2: `j`/`k` items, `J`/`K` lanes, `space`
expand/collapse, `a` ask, `n` next awaiting answer, `/` search, `g`/`G`
top/bottom, `?` help, `Escape` closes. Bindings stay inert while typing in a
textarea, input, or contenteditable. **Verify shifted bindings specifically** —
`J`, `K`, `G`, `?` were all silently dead, and only lowercase keys worked.

## Design direction

The current look is cream paper, serif body, muted grey — which is the single
most common default an AI produces. Replace it.

The subject is an **instrument**: a keyboard-driven reading surface for someone
who is drowning in prose and wants to steer. Not a document, not a blog post,
not a fake terminal skin.

- **Signature element: the cursor itself**, plus a left rail that shows where
  you are in the structure — a scrollbar made of the document's own shape,
  showing lanes and which one holds the cursor. That is the one memorable
  thing; keep everything else quiet.
- **Palette:** move off cream. Choose a cool, low-chroma ground with one
  high-signal selection colour that appears nowhere else. Avoid the two other
  AI defaults: near-black with an acid-green or vermilion accent, and broadsheet
  hairlines with zero radius. Trust chips keep semantic colour — that is the one
  other place colour carries meaning, so keep those four distinguishable in both
  themes and to a red-green colourblind reader.
- **Type:** self-contained means system faces only; do not embed a webfont.
  Personality therefore comes from scale, weight, tracking, and rhythm rather
  than an exotic family. Use a monospace utility layer for keys, ids, counts and
  forensics, and set a deliberate type scale rather than default sizes.
- **Density:** this page carries long updates with deep forensics. It should
  feel dense and navigable, not airy — closer to a well-set reference manual
  than to a landing page.
- Light and dark both first-class. Responsive to mobile width. Reduced motion
  respected.

## What must not regress

Verify each of these directly; they are the reviewed guarantees of iterations
1 and 2:

- Zero external requests from the generated page.
- Daemon binds `127.0.0.1` only; both URL forms serve identical bytes.
- Legacy `{question, answer}` content renders, answered questions stay answered,
  and rendering never writes to the human's file. Check against the real file at
  `~/.claude/skills/visual-brief/demo-run/content.json` — copy it somewhere
  writable first; do not write into that directory.
- Threads: turns oldest first, reply box under the newest turn, anything
  awaiting an answer opens itself, ids stable across re-render.
- Awaiting-answer counts agree between the page, the dashboard badge, and
  `visual-brief list`.
- Questions are untrusted data: escaped, never executed, never in a path.
- Submitting is disabled while a request is in flight.
- Every Python file under 400 lines. Frontend source files under 400 lines too.

## Tests

- Keep the existing Python suite green, minus the no-JavaScript tests, which are
  removed on purpose.
- Frontend unit tests with Vitest for the cursor state machine: movement,
  wrapping, lane versus item, inertness while typing, restoration after
  re-render.
- Real-browser tests remain, and must now assert **what a human can see**, not
  that `activeElement` changed: after `j`, the element the application marks as
  the cursor must have a computed style measurably distinct from its neighbours
  — a real border or background difference, asserted numerically.
- Add a bounded retry on **browser startup only** (never on assertions) to stop
  transient Chrome start failures reddening the suite. It must still fail loudly
  when the browser genuinely is not installed.

## Out of scope

Select-a-phrase-to-comment stays out; the anchor union already carries room for
it and it is iteration 4. Do not build the computed "where things stand" panel,
diagrams, or syntax highlighting. Do not change the deferred items recorded in
earlier rounds.
