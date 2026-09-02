# Visual Brief One-Briefing Publish Specification

Status: current. This specification replaces the earlier two-object contract
that this file once described. The historical filename remains to preserve
existing references.

## Purpose

A normal publish represents one substantial report. The report is one durable
record that starts as the latest briefing and later moves into the earlier
briefing ledger. The model does not maintain a second current-state portrait.

Visual briefs serve substantial implementation, investigation, review, and
design reports. Trivial updates remain in ordinary conversation.

Before a long work block, the agent sends a short visible acknowledgment. A
substantial request from a page thread receives the same immediate response.

## Publish Contract

`visual-brief publish` accepts one JSON object with exactly:

- `id`
- `timestamp`
- `headline`
- `summary`
- `lanes`

The object contains one to six lanes. Lane names and count fit the report.
Recent changes may have their own section when useful, but no recurring section
set is required.

The headline and summary use plain prose. They avoid arrows, status chains,
unexplained abbreviations, and internal process metrics. The payload contains
no `questions` field at any depth.

The retired `{current_state, changes}` envelope fails with a direct migration
message. No compatibility branch accepts it.

## Storage and Presentation

The command appends the validated object to `updates`. Append order is
chronological, so `updates[-1]` is the latest briefing. The page reverses that
list for display.

The latest briefing uses the existing prominent card tokens. A second or later
record appears below an earlier-briefing heading with quiet ledger styling.
Zero updates show no briefing card or ledger heading. One update shows only the
latest card.

The front end renders all updates through one keyed list. A live publish moves
the former latest into second position without replacing its DOM row. Fold
choices, drafts, cursor state, fresh-answer state, and pending submissions stay
bound to stable row ids.

A normal live publish patches document data into the open page. It does not
reload the page.

## Conversations

The briefing root, lanes, and items are all chat-addressable. Root questions
participate in:

- thread normalization and validation
- safe projection into the embedded page document
- linting and queue views
- unanswered counting, folding, and answering
- frontend pending-state reconciliation
- rendering, search, and keyboard navigation

The masthead attention control counts outstanding chats only within
`updates[-1]`. It includes root, lane, and item threads. Global next-chat
navigation still walks outstanding threads in older briefings.

## Legacy Migration

Legacy documents render before migration. The first direct publish performs
one migration in memory before writing any file:

1. Normalize legacy conversation pairs.
2. Merge valid queued messages against the legacy current-state anchors.
3. Convert `current_state` into one ordinary update with a generated stable id.
4. Rewrite stored root, lane, and item thread anchors to the archived paths.
5. Save persistent aliases from retired anchors to archived anchors.
6. Remove `current_state` and append the new briefing.

The persistent aliases cover `//current-state`, each state lane path, and each
state item path. They preserve queue entries submitted before migration and
late submissions from a stale open page. Counting, folding, legacy-pair
settlement, and linting resolve queue records through these aliases. The queue
file remains append-only.

The shipped four-claim state becomes one ordinary archived briefing. A second
direct publish finds no `current_state`, so it cannot archive the same legacy
state twice.

## Atomicity

Payload validation occurs before run resolution or mutation. Duplicate update
ids fail before legacy migration begins. The writer validates and renders the
complete candidate before replacing run files.

`content.json`, `index.html`, and `meta.json` use the existing rollback guard.
Malformed payloads, duplicate ids, candidate validation failures, render
failures, and partial write failures leave the original run bytes intact.

## Compatibility Commands

`add-update` remains for compatibility imports. It warns that normal briefings
use `publish`. It does not define the normal-model contract.

`render`, `fold`, `answer`, and `lint` continue to support legacy documents.

## Verification

Backend regression coverage includes:

- direct five-field payload validation
- one-to-six lane boundaries
- rejection of authored questions and old envelopes
- append-only identity across successive publishes
- root, lane, and item fold, count, project, lint, and answer paths
- structured and four-claim migration
- queued old-anchor migration and late stale-page submissions
- malformed, duplicate, validation, render, and write atomicity failures

Frontend regression coverage includes:

- zero, one, legacy-plus-update, and multi-update layout
- prominent latest styling and quiet ledger placement
- root, lane, and item latest-attention scope
- older-briefing global navigation
- DOM node, fold, and draft preservation during latest-to-ledger handoff
- full Vitest coverage without a browser

The committed frontend bundle and source stamp are rebuilt after source tests
pass. Final real-browser verification belongs to the parent workflow.
