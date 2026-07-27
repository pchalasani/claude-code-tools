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
visual-brief publish-now --file now.json   # rewrite the pinned Now panel
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
| `j` / `k` | Next / previous item |
| `J` / `K` | Next / previous lane |
| `Space` | Expand or collapse the cursor row |
| `c` | Chat about the cursor row: ask, answer the agent, or steer it |
| `⌘`/`Ctrl` + `Enter` | Send what you have written |
| `n` | Next question awaiting an answer |
| `/` | Search items |
| `g` / `G` | Top / bottom |
| `?` | Show the key list |
| `Escape` | Close an overlay, or leave a text box |

Keys stay inert while you are typing, so a question can contain the letter `j`.
Inside the chat box plain `Enter` starts a new line; sending is the chord.
`Enter` outside a text box belongs to the browser, so a control you tabbed to
still opens the way it does anywhere else.

While a message is on its way and until its answer arrives, the page says
`agent is working` where the answer will land. When an answer arrives during a
self-reload, its conversation opens itself and is marked `New answer` until you
go to it.

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
