# visual-brief — live patching contract

Builds on `477cd00`. Work only inside `packages/visual-brief/`. Do not touch
`~/.claude/skills/visual-brief/`, `~/.claude/visual-brief/runs/`,
`docs-internal/visual-brief-canvas-delta-spec.md`, or anything under `docs/`.

## The one thing this contract asks for

Today the agent publishes, the tab notices, and the tab throws itself away:
`reload.ts` calls `location.reload()`. Everything live is destroyed and rebuilt
— which is why the page flickers, why content jumps under the reader, and very
likely why the working sign flaps (shows → vanishes → returns).

The page must instead **stay alive and change**. It fetches the new document as
data, hands it to the running Solid application, and the application patches
only what actually changed. The human reading a paragraph when a publish lands
should notice nothing but the new material appearing.

This is not a performance change. It is the difference between a document
viewer and an interface: an interface does not restart itself while you are
using it.

## 1. The daemon serves the document as data

Add one run-scoped endpoint, `GET /document` (and `HEAD`), alongside `/version`
and `/render-version`, reachable at both address forms the daemon already
supports. It answers `application/json` with:

```json
{
  "generation": "<64 hex>",
  "assets": "<64 hex>",
  "document": { "title": "...", "summary": "...", "updates": [ ... ] }
}
```

- `generation` is exactly what `/render-version` would answer for the same page
  — the client compares the two, so they must be the same value under the same
  state.
- `assets` identifies the front-end bundle the served page carries.
- `document` is exactly the blob embedded in that page: same projection, same
  normalization, same merged pending follow-ups.

**All three fields must derive from one read of the served page.** The served
page is not `index.html` on disk — `read_served_page` merges valid pending
follow-ups and re-renders. A `document.json` written at publish time would be a
second source of truth that omits those follow-ups and drifts from the page
under every race. One read, three fields extracted from it: then `/document`
and `/run` cannot disagree, ever.

Unknown run → 404, as the other run endpoints do. A page the daemon cannot
build → 404 with the same message shape.

## 2. The page carries an assets stamp

`render_page` emits `<meta name="visual-brief-assets-version" content="...">`
next to the render-version meta. The stamp is derived from the inlined bundle
(script and style) and nothing else, so it changes when the code changes and
does not change when the document changes.

This meta is what makes patching safe. A generation change means *the page
would render differently* — which happens both when the agent publishes and
when the tool is reinstalled with a new bundle. Patching a document into a tab
running last week's code leaves that tab running last week's code forever. That
is a bug class this branch has already been bitten by once ("the Ask
reversion": buttons reverting, `c` dead and `a` alive, surviving a hard
refresh — a tab on an older bundle).

## 3. The poller patches, and reloads only as a fallback

`startVersionWatch` keeps polling `/render-version` — 64 bytes is the right
thing to poll every five seconds. When the served generation differs from the
page's, it fetches `/document` and then decides:

| Situation | Action |
| --- | --- |
| payload readable, `assets` matches the page's stamp | **patch in place** |
| payload's `assets` differs from the page's stamp | reload — new code, and only a reload loads code |
| endpoint missing, unreachable, unparseable, or the wrong shape | reload |
| applying the document throws | reload |
| daemon unreachable at all (`/render-version` answered nothing) | back off and retry, as today |

Every reload path goes through the existing heal-once memory
(`readHealedStandoff` / `rememberHealedStandoff`), so no situation can produce
a reload loop: a page that comes back into the same standoff stays put and
stays readable. A page that reloads onto the new bundle finds the stamps equal
and never reloads again — the memory does not obstruct the ordinary case.

After a successful patch the watch adopts **the generation the payload carried**
as its current one, not the generation the poll reported. The two can differ
when a second publish lands between the poll and the fetch; adopting the polled
value would make the page believe it is showing something it is not.

## 4. The application accepts a changing document

`main.tsx` reads the embedded document once, as now, and holds it in state that
can be replaced. `App` and `createBriefState` take a document that changes
rather than a construction-time constant; `state.brief` becomes a live read.
Everything derived from the document inside `createNavigation` — the outline,
the row index, the awaiting count, the chat count, freshness — becomes derived
rather than computed once at construction.

**Unchanged rows must keep their DOM.** Re-parsing JSON produces all-new object
references, so a naive swap makes every `For` throw away and rebuild every row:
the same flicker, now without the reload. Solid's answer is a store plus
`reconcile` with `key: "id"`, which diffs into the existing objects and leaves
the identity of anything unchanged intact. Whatever mechanism is chosen, the
requirement is behavioural and is tested by node identity (§6.7).

Watch `evidence-view.tsx:90`, which iterates `Row` objects rather than document
objects: `outline()` builds fresh `Row`s on every recompute, so that list needs
its own keying or it rebuilds the evidence subtree on every patch.

## 5. What must survive a patch

These are the acceptance criteria. Each one is a way the human has been thrown
out of their place by a reload, and each must now hold across a publish:

1. **Scroll position** is unchanged.
2. **The cursor** stays on the same row. If that row is gone from the new
   document, it moves to the nearest surviving ancestor — never to the top of
   the page, never nowhere.
3. **Folding**: every row the human expanded stays expanded, every row they
   folded stays folded. Rows that did not exist in the previous document follow
   the ordinary default-open rules, so new material is not hidden.
4. **An open composer** stays open with its typed text intact, unless the row
   it is written at has gone; then it closes.
5. **Search text, the my-chats view, and any open overlay** survive. The help
   overlay stays up.
6. **Hint labels** never resolve to a row that is no longer painted.
7. **The working sign** for a pending submission is continuous: retired the
   moment its text appears in the patched document, and otherwise unchanged. A
   patch may never re-create, reset, or blink a sign.
8. **Freshness** still works: an agent turn arriving by patch marks its thread
   as a new answer exactly as it did after a reload, and only visiting clears
   the mark.
9. **Counts and title** follow the new document: masthead counts, awaiting
   count, my-chats count, the structure map, and `document.title`.

## 6. What gets deleted

The self-reload marker machinery exists solely because reloads destroy state.
With the reload gone it is dead weight and a live bug source, so it goes:

- `markSelfReload`, `consumeSelfReload` and their storage key in
  `session-store.ts`;
- `isHumanRefresh`, `forgetLoadClassification` and the load classification in
  `pending.ts`;
- `MAX_REFRESHES`, the `refreshes` field on `SentRecord`, and its validation;
- `loads` and the load-based half of the stall rule.

A pending submission is then governed by one rule: it is retired when its exact
text and timestamp appear in the document, and after `STALL_POLLS` polls
without appearing it degrades from an animated sign to a plain statement that
it was submitted and has not landed. It is not deleted after that. A message
the human wrote and the page cannot account for is worth showing indefinitely;
this is deliberate and is to be stated in the code, not discovered later.

Delete the tests that covered the deleted behaviour. Do not delete a test by
weakening it into one that passes vacuously.

## 7. Tests

Frontend (vitest), each asserting behaviour rather than source text:

1. A generation change fetches `/document` and applies it, and
   `location.reload` is never called.
2. A payload whose `assets` differ reloads and does **not** apply the document.
3. A missing, unparseable, or wrong-shaped payload reloads once, then stays put
   on the second identical standoff.
4. A patch with a composer open at a surviving row: still open, draft intact.
5. Folding survives: human-expanded stays expanded, human-folded stays folded,
   a brand-new row follows the default-open rule.
6. The cursor holds; when its row vanishes it lands on the nearest surviving
   ancestor.
7. **Node identity**: hold the DOM node of a row that does not change, apply a
   patch that changes a different row, and assert the held node is still the
   node in the document. This is the anti-flicker proof and it is blocking.
8. A pending note retires when its text arrives by patch, with no reload.
9. An agent turn arriving by patch marks its thread fresh; visiting clears it.
10. Search text and the chats view survive a patch.

Python:

11. `/document` answers a generation identical to `/render-version` under the
    same state, and a document that parses equal to the blob embedded in the
    page `/` serves at that moment.
12. `/document` reflects merged pending follow-ups, not just `index.html`.
13. `/document` on an unknown run is 404; `HEAD` is supported like its
    neighbours.
14. The assets meta is present, changes when the bundle changes, and does not
    change when the document changes.

Browser (the existing CDP suite): publishing to an open page must change what
the page shows **without a navigation**. Prove it with a marker the page sets
once at mount and that a reload would destroy — assert the new content is
present and the marker is the same one. Update, rather than delete, the
existing browser tests that assume a reload.

## 8. Scope rulings

A finding premised on one of these is not a finding.

- **Out of this pass, deliberately:** the visual redesign (bigger text,
  hideable side panel, speaker colours, dark/light toggle, cards and columns);
  markdown in update `summary` and item `glance`; `publish-now` dropping
  conversations; select-a-phrase commenting.
- **The "Ask reversion" is not to be chased here.** The assets stamp closes the
  plausible mechanism; say so in the commit message if it does, and do not
  build anything further for a phenomenon that has never been reproduced.
- **One trusted local user.** Hand-edited local files are not attacks. Text
  arriving through the page is untrusted and the markdown escaping rules
  continue to hold.
- **Pre-existing behaviour at `477cd00` is not a finding** — check the base
  commit before reporting anything.
- **Still deferred:** two-store revision skew, older-daemon text matching,
  storage quota growth.
- A reload is not a failure when this contract calls for one. The goal is that
  publishing never reloads; a new bundle and an unreadable payload still do.

## 9. Repo rules

Python under 88 characters, fully typed, google-style docstrings; pytest with
real objects and no mocks; no file over 400 lines — split rather than exceed it,
which the navigation refactor may well require. The front end is Vite + Solid +
TypeScript; rebuild and stamp the committed bundle with
`make visual-brief-frontend` and commit it, because it ships as package data.

Verification before any claim of done:

```
uv run --package visual-brief pytest packages/visual-brief/tests -q -rs
cd packages/visual-brief/frontend && npm run typecheck && npx vitest run
make visual-brief-frontend
```

Zero skips tolerated in the Python suite.
