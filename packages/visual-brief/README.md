# visual-brief

`visual-brief` turns structured session reports into self-contained local HTML
briefings. One loopback-only daemon serves every active run and a dashboard for
finding sessions that need an answer.

Use it for substantial implementation results, investigations, reviews, and
design reports. Routine status messages and trivial fixes do not need a visual
brief.

## Install

```bash
uv tool install visual-brief
```

## Create a Run

```bash
visual-brief new --label "Review parser changes" --port 8765
visual-brief serve --port 8765
```

Before long implementation work, send the human a short visible
acknowledgment. Then publish the completed report to the page.

## Publish a Briefing

Normal reports use one direct object:

```bash
visual-brief publish --file report.json
```

The object has exactly `id`, `timestamp`, `headline`, `summary`, and `lanes`:

```json
{
  "id": "parser-verification",
  "timestamp": "2026-08-04T12:00:00Z",
  "headline": "The parser now rejects truncated policies",
  "summary": "Focused comparisons pass, while one wider limit remains.",
  "lanes": [
    {
      "id": "verified-behavior",
      "name": "Verified behavior",
      "items": [
        {
          "id": "truncated-policy",
          "glance": "Truncated policies now return a syntax failure.",
          "explanation": "The result agrees with the reference parser.",
          "trust": "verified-by-me"
        }
      ]
    }
  ]
}
```

Choose one to six lanes. Their names and content should fit the report. A
briefing may describe new work, current behavior, limitations, decisions,
evidence, or next actions. A dedicated recent-changes section is optional.

The CLI appends the object to `updates`. The last record is the prominently
displayed latest briefing. After the next publish, that same stable-id record
moves into the quieter earlier-briefing ledger. Its conversations, folds,
drafts, and pending state remain intact.

There is no separate normal current-state object and no separate changes
object. The retired `current_state` plus `changes` envelope is rejected.

## Content Shape

Each lane has `id`, `name`, and `items`. Each item has `id`, `glance`,
`explanation`, and `trust`. Items may also carry `forensics`, `tables`, and up
to three `suggestions`.

Use plain prose for the briefing headline and summary. Put detailed evidence
under the claim it supports. The allowed trust values are:

- `verified-by-me`
- `reported-by-agent`
- `unverified`
- `known-limitation`

Do not include `questions` in a publish payload. Conversations are tool-owned.
The briefing root, every lane, and every item are chat-addressable.

## Conversation Workflow

```bash
visual-brief fold
visual-brief answer <thread-id> --text "..."
visual-brief lint
```

`fold` copies queued human text and timestamps into the document. `answer`
appends an agent turn. A substantial request from the page should receive a
short acknowledgment before the long work begins, followed by the completed
answer or briefing.

The page updates in place when a publish arrives. It does not reload for a
normal live publish, so open drafts and reader state survive.

## Legacy Migration

Legacy documents continue to render. On the first direct publish, a legacy
`current_state` becomes one ordinary archived briefing and is removed. Its
root, lane, and item conversations move to archived anchor paths. Persistent
aliases also preserve queued messages submitted from an old open page.

Migration and the new publish form one atomic write. Malformed payloads,
duplicate ids, validation failures, render failures, and write failures leave
the run unchanged.

## Other Commands

```bash
visual-brief add-update --file update.json # compatibility imports only
visual-brief render <run-id>               # render hand-edited content
visual-brief list                          # runs and unanswered counts
```

Runs live below `$VISUAL_BRIEF_HOME`, which defaults to
`~/.claude/visual-brief/runs/`. The dashboard is available at
`http://localhost:8765/`.

The renderer and server use only the Python standard library. The shipped page
is a committed SolidJS bundle. Build it with:

```bash
make visual-brief-frontend
```
