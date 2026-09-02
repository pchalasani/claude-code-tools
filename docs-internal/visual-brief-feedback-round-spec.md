# visual-brief — feedback round contract

Builds on `5b3c0fb`. Work only inside `packages/visual-brief/`. Do not touch
`~/.claude/skills/visual-brief/`, `~/.claude/visual-brief/runs/`,
`docs-internal/visual-brief-canvas-delta-spec.md`, or anything under `docs/`.

Every item below was reported by the human from the live page during a
testing session. Nothing here is speculative.

## 1. The working sign flickers at the reload boundary

Reported twice, second time precisely: *"as soon as I submit, I do see the
agent is working spinner, but then that disappears when the awaiting chips
come up. And then after a while again the agent is working shows up."*

So the sequence today is: painted live on send → **lost when the fold's
publish reloads the page** → restored only once the stored record is read
back and the poller has run. The last pass made the *record* survive the
reload; the *paint* evidently does not.

Make the sign continuous across the post-send reload. It must be visible
from the moment the submission is accepted until its turn appears on the
page, with no gap at the reload and no dependence on a poll cycle to
reappear. The awaiting chips arriving must not replace or hide it — both can
be true at once, and the sign is the more informative of the two.

## 2. A lane-level chat lands beneath all of the lane's items

Chatting on a lane puts the conversation at the bottom of that lane's item
list, far from the lane header it belongs to. It should sit directly under
the lane's own header, above its items, so the conversation is next to the
thing it is about.

The human acknowledged this is fiddly. Constraints: item ordering must not
change; a lane's conversations keep their relative order among themselves;
the cursor outline order must match the painted order exactly (they are one
list — `outline()` and the rendered tree cannot disagree, or the cursor and
the page drift apart); hint labels and `m` must still reach them.

## 3. Chat buttons momentarily flicker to "Ask"

Reported, **not reproduced**. Investigation so far: the shipped bundle
contains no `Ask` string literal, and no run page renders one — the single
"Ask about anything" on the page is prose in an item about a future feature.
The leading theory is a browser tab still running the pre-relabel bundle,
which would mean the version-healing reload did not catch it.

Do not invent a fix for a phenomenon you cannot reproduce. Instead: audit the
healing path for a case where a stale tab keeps rendering old code — in
particular whether a tab that never polls successfully (or polls before the
new bundle is served) can persist indefinitely — and add a test for whatever
gap you find. If you find no gap, say so plainly and leave it unfixed rather
than guessing.

## 4. Markdown in answers and item text

Agents write markdown by habit; it currently shows as raw asterisks and
backticks. Render it in agent turn text and in item `explanation` (and
`glance` if that is coherent).

**The hard constraint:** human-written text is untrusted and must never
become live markup. Escape first, then apply a strict allowlist of inline
and block forms — emphasis, strong, inline code, fenced code, lists, links
with a safe scheme allowlist, headings if you want them. No raw HTML pass
through, ever, and no external library that pulls a parser with its own
sanitiser semantics unless it can be inlined and audited. A test must plant
`<img src=x onerror=alert(1)>`, a `javascript:` link, and a fenced block
containing markup, and prove none of them execute or produce live elements.

Human turn text renders markdown too if that is safe under the same rules —
decide, and say which you chose and why.

## 5. Forensics cannot be reached from the keyboard

The cursor walks updates, lanes, items and conversations. Forensics folds are
not rows at all, so the deepest layer — the raw evidence — is mouse-only.

The human suggested a `d` key. Consider instead making forensics **real rows
in the outline**, so hint labels, `j`/`k`, expand-all, search and everything
else reach them for free; a bespoke key would leave every other navigation
still blind to them. Choose deliberately and justify it in your notes. If
they become rows, check what that does to counts shown in the masthead, to
`collapse-all`, and to the structure map.

## Out of scope

The visual redesign (cards, columns, themes, human-versus-agent turn
colours) — that is the next pass and must not be started here. Select-a-
phrase commenting. The three deferred tail cases recorded on the page
(two-store revision skew, older-daemon text matching, storage quota growth).

## Verification

- Full Python suite green, no skips: `uv run --package visual-brief pytest
  packages/visual-brief/tests -q -rs`.
- Frontend: `npm run typecheck` and `npx vitest run` green.
- `make visual-brief-frontend` rebuilt and stamped; committed bundle matches
  sources.
- New behaviour tested by pressing real keys and asserting painted state, not
  source text. The markdown safety cases above are mandatory.
- Every file under 400 lines; Python 88-char, typed, google-style docstrings;
  real objects, no mocks; tests never touch the live runs directory.

Repo rules: commit each green round; reference this file in commit messages.
