# visual-brief

`visual-brief` turns structured session updates into self-contained local HTML
briefings. One loopback-only daemon serves every active run and a dashboard for
finding sessions that are waiting for an answer.

## Install

```bash
uv tool install visual-brief
```

## Use

Create and render a run, then start the shared daemon:

```bash
visual-brief new --label "Review parser changes"
visual-brief render review-parser-changes-a1b2
visual-brief serve
```

Runs are stored below `$VISUAL_BRIEF_HOME`, which defaults to
`~/.claude/visual-brief/runs/`. The dashboard is available at
`http://localhost:8765/`.

The renderer and server use only the Python standard library. Rendered brief
pages are self-contained and remain readable without JavaScript.

## Development

```bash
uv run --package visual-brief pytest packages/visual-brief/tests -q
```

MIT license.
