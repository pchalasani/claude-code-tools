# visual-brief

`visual-brief` turns structured session updates into self-contained local HTML
briefings. One loopback-only daemon serves every active run and a dashboard for
finding sessions that are waiting for an answer.

## Install

```bash
uv tool install visual-brief
```

## Use

Create a run, then start the shared daemon:

```bash
visual-brief new --label "Review parser changes" --port 8765
visual-brief serve --port 8765
```

The content of a run is written by verbs rather than by hand. Each one
validates the whole document first, writes atomically, and re-renders the
page:

```bash
visual-brief add-update --file update.json # append one dated update
visual-brief fold                          # queued questions into the page
visual-brief answer <thread-id> --text "…" # reply to one conversation
visual-brief lint                          # the checks the verbs already run
visual-brief render <run-id>               # re-render a hand-edited file
```

Runs are stored below `$VISUAL_BRIEF_HOME`, which defaults to
`~/.claude/visual-brief/runs/`. The dashboard is available at
`http://localhost:8765/`.

The renderer and server use only the Python standard library. A rendered page
is one self-contained file that makes zero external requests: it carries the
validated brief as an embedded JSON document plus the inlined interface.

## Reading the page

The page is keyboard-driven. A cursor — a solid rail and a tinted row — marks
where you are; it lives in the application's own state, so it never depends on
where the browser happens to have put focus, and the page scrolls to keep it in
comfortable reading position. Clicking a row moves the same cursor.

| Key | What it does |
| --- | --- |
| `j` / `k` | Next / previous painted content row |
| `J` / `K` | Next / previous lane |
| `Space` | Expand or collapse the cursor row |
| `E` / `C` | Expand everything / collapse back to lanes |
| `f` | Label every row on the page, then type a label to jump there |
| `c` | Chat wherever the cursor is: update, lane, item or conversation |
| `⌘`/`Ctrl` + `Enter` | Send what you have written |
| `n` | Next open chat: unanswered or a fresh unseen answer |
| `m` | Show every conversation you have written in |
| `/` | Search items |
| `g` / `G` | Top / bottom |
| `?` | Show the key list |
| `Escape` | Close an overlay, leave the chats view, or leave a text box |

Every granularity the mouse can chat at, the keyboard reaches: `J`/`K` onto a
lane and `c` opens exactly the box that lane's own Chat button opens.

`m` is how you find your own conversations again. Collapsing the page hides
them, while `n` visits anything that still needs your attention: an unanswered
conversation or a fresh answer you have not seen. The chats view collects every
thread you have written in — answered or not — and `j`/`k` walk them. Newer
conversations appear first. Inside an expanded lane each item also carries a
small muted number, so a conversation can refer to "item 12".

Keys stay inert while you are typing, so a question can contain the letter `j`.
Inside the chat box plain `Enter` starts a new line; sending is the chord.
`Enter` outside a text box opens or closes the cursor row. A control reached
with Tab keeps its ordinary keyboard behavior.

Drafts belong to their rows and survive navigation, folding, publishing and a
reload. Opening another chat does not replace the first draft. Sending clears
only the message that was sent. An empty draft closes with `Escape`; a nonempty
one requires a second `Escape`, or the explicit discard control, before it is
erased.

While a message is on its way and until its answer arrives, the page says
`agent is working` where the answer will land. When an answer arrives, its
conversation opens itself and is marked `New answer` until you go to it.

A publish does not take the page away from you. The tab fetches the new
document and changes only what actually changed: your scroll position, your
cursor, every fold you chose, an open chat box and the words in it, the search
you were running and any panel you had up all stay exactly as they were, and
rows nobody edited keep the very elements they were drawn as. New material
arrives under the ordinary rules, so nothing lands hidden.

The waiting sign follows the message rather than the page. It is recognised by
its own words and the instant the daemon queued them, so it is retired wherever
the fold puts it. If a message takes several polls to appear, the page adds
`submitted — refresh if this persists` without taking the working sign away.

An open tab also looks after itself. It asks the local daemon for the current
page generation on a timer, backs off while the daemon is unreachable, and
replaces itself in exactly two situations: the daemon is serving a different
front-end bundle — only loading a page loads code — or the new document cannot
be fetched or understood at all, which is what a tab left open across an
upgrade used to sit through, silently, forever. It replaces itself once per
such state, so a page that comes back the same stays readable instead of
reloading in a loop.

## Development

```bash
uv run --package visual-brief pytest packages/visual-brief/tests -q
```

The verification suite requires the `agent-browser` executable and fails
loudly, rather than skipping browser regressions, when it is unavailable.

### The front end

The interface is a Vite + Solid + TypeScript app in `frontend/`. It builds to
exactly two artifacts — `visual-brief.js` and `visual-brief.css` — which are
committed under `src/visual_brief/static/` and shipped as package data, so
installing the tool never needs Node.

```bash
make visual-brief-frontend   # npm ci, typecheck, vitest, build, re-stamp
```

`make visual-brief-test`, `make visual-brief-build` and
`make visual-brief-publish` all refuse to run when the committed bundle no
longer matches the front-end sources, so a rebuild is never optional and never
silent. The fingerprint that decides this lives in `tools/bundle-stamp.json`,
outside the directory the Vite build empties. Rebuild and commit
`src/visual_brief/static/` and that stamp with any front-end change.

MIT license.
