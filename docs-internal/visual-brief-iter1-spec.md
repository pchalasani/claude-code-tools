# visual-brief — iteration 1 contract (plumbing)

Goal: turn the working single-run prototype into an installable package with a
**single shared daemon** that serves **many concurrent sessions** without port
collisions, plus a dashboard listing every run.

Iteration 1 is PLUMBING ONLY. Do NOT build keyboard controls, threaded
follow-ups, selection anchors, diagrams, or syntax highlighting. Those are
iterations 2+.

## Source material

- Working prototype (READ ONLY — it is serving a live session, do not modify,
  do not delete, do not move): `~/.claude/skills/visual-brief/`
  - `render.py` (515 lines), `server.py` (412), `SKILL.md`, `example.json`
- Package layout to copy: `packages/voxtype/` in this repo (uv workspace
  member, own pyproject, src/ + tests/, Makefile targets).

Copy the prototype files into the package; do not `git mv` from the skill dir.

## Deliverable layout

```
packages/visual-brief/
  pyproject.toml          # name = "visual-brief", requires-python >=3.11
  README.md
  LICENSE                 # copy from packages/voxtype/LICENSE
  src/visual_brief/
    __init__.py
    cli.py                # argparse entry point
    render/               # the split renderer (was render.py, 515 lines)
      __init__.py         # render_content(data) -> html ; public API
      validate.py         # schema validation + error messages
      html.py             # escaping + element builders
      css.py              # the stylesheet constant
      page.py             # page assembly
    server/
      __init__.py
      daemon.py           # the multi-run HTTP server
      routes.py           # request routing
      registry.py         # run discovery from the filesystem
      dashboard.py        # the index page
  tests/
    test_render.py
    test_registry.py
    test_routes.py
    test_daemon.py
```

Every file stays under 400 lines. No file may exceed 500.

## Hard constraints (carried from the prototype — do not regress)

1. **Standard library only** for renderer + server. No third-party imports in
   `src/visual_brief/`. (pygments/mermaid arrive in a later iteration.)
2. **Self-contained pages.** The generated HTML must make zero external
   requests. Verification: `grep -E 'https?://' index.html` returns nothing.
3. **Loopback only.** The daemon binds `127.0.0.1`. Never `0.0.0.0`.
4. **Question text is untrusted data.** Escape on render; never execute, never
   interpolate into a shell command.
5. **Malformed content JSON exits with a concise message, not a traceback**, and
   the message names the offending path (e.g. `updates[2].lanes[0].items[1].trust`).
   The existing precise errors for the two known traps must survive:
   `forensics` must be a list (of strings or `{title, body, children}` objects),
   and a `tables` entry needs `caption`, `columns`, `rows`.
6. Progressive disclosure keeps working with JavaScript disabled (`<details>`).

## The multi-run model

### Run directories

Runs live under `$VISUAL_BRIEF_HOME` (default `~/.claude/visual-brief/runs/`).
One directory per run:

```
runs/<run-id>/
  content.json      # written by the agent
  index.html        # generated
  questions.jsonl   # append-only; the reverse channel
  meta.json         # {run_id, label, cwd, repo, branch, created_at, updated_at}
```

`<run-id>` MUST match `^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$`. Reject anything else
at every entry point. This is security-critical: the run id arrives from the
**Host header** and from URL paths, so a bad id must never reach a filesystem
path. Resolve the final path and confirm it is inside the runs root before any
read; reject if not.

There is no registration protocol — the registry is a scan of the runs
directory. A directory without a readable `meta.json` is listed with a degraded
label rather than crashing the dashboard.

### Routing

One daemon, one port (default 8765), routed by `Host`:

| Request | Serves |
| --- | --- |
| `Host: localhost` or `127.0.0.1` (no subdomain), path `/` | the dashboard |
| `Host: <run-id>.localhost`, path `/` | that run's `index.html` |
| any host, path `/r/<run-id>/` | that run (fallback form, must work identically) |
| `GET /health` | `{"service": "visual-brief", "version": "..."}` |
| `GET /version` (within a run) | hash of that run's `content.json` |
| `POST /ask` (within a run) | append one JSON line to that run's `questions.jsonl` |

Both URL forms must work for every run-scoped endpoint. Strip the port before
parsing the Host header. Treat an absent/garbage Host as the dashboard.

### Dashboard

`http://localhost:8765/` lists every run, newest activity first. Per row:

- the run's label, its repo and branch if known
- when it last changed, in words
- a badge when that run has questions with no answer yet — this is the "waiting
  on you" signal, and it is the dashboard's main job
- links to both `http://<run-id>.localhost:8765/` and the `/r/<run-id>/` form

The dashboard auto-refreshes on a timer (it is a status board, not a document).
It must render with zero runs, and must not break on a malformed run dir.

### Unanswered-question counting

A question is unanswered when it appears in `questions.jsonl` and no matching
answered entry exists in `content.json`. Iteration 1 has one answer per
question, so match on `anchor_id` + question text. Keep this in one function in
`registry.py`; iteration 2 replaces it when questions get stable ids.

## CLI

```
visual-brief serve [--port 8765]     # start the daemon (idempotent, see below)
visual-brief new --label LABEL [--run-id ID]   # create a run dir, print both URLs
visual-brief render <run-id>         # re-render that run's content.json
visual-brief list                    # runs + unanswered counts, as text
```

`serve` is idempotent: try to bind; on `EADDRINUSE`, GET `/health` on that port
— if it answers as visual-brief, print the dashboard URL and exit 0; if it is
some other service, exit non-zero with a clear message. `new` auto-generates a
run id from the label plus a short random suffix when `--run-id` is absent, and
must not collide with an existing directory.

## Repo wiring

- Add to the uv workspace (already `members = ["packages/*"]` — verify it picks
  it up).
- Makefile targets mirroring voxtype's: `visual-brief-test`, `visual-brief-install`,
  `visual-brief-build`, `visual-brief-release`, `visual-brief-publish`. Follow the
  existing style in the root Makefile exactly.
- The umbrella package must NOT depend on visual-brief.

## Tests (pytest, real objects, no mocks)

Required coverage:

- renderer: the bundled `example.json` renders; output has no `http://` or
  `https://`; each of the two known schema traps produces a precise error naming
  the path; an unknown trust chip is rejected.
- run ids: a table of hostile values (`../../etc`, `a/b`, `A`, ``, 60 chars,
  `.`, `-x`, `x-`) are all rejected; valid ones accepted.
- path containment: a crafted run id can never resolve outside the runs root.
- routing: both URL forms reach the same run; bare host reaches the dashboard;
  Host with a port suffix parses; unknown run gives 404 not 500.
- registry: zero runs; a run missing `meta.json`; a run with malformed JSON —
  none crash the dashboard.
- unanswered counting: a question with no answer counts; one with an answer does not.
- daemon: binds 127.0.0.1 only; `/health` responds; `POST /ask` appends exactly
  one line and does not execute anything.

Run the suite and make it green: `uv run --package visual-brief pytest packages/visual-brief/tests -q`

## Out of scope for iteration 1

Keyboard controls · threaded follow-ups · selection anchors · freshness stamps ·
the computed "where things stand" panel · diagrams · syntax highlighting ·
installing over the live skill.

Leave `~/.claude/skills/visual-brief/` exactly as it is.
