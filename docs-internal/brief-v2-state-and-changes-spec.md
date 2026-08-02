# Brief v2: Detailed Current State and Changes

This specification defines the current Visual Brief publishing contract. It
replaces the earlier four-claim current-state design while retaining read
compatibility for documents that already use that design.

## Purpose

Each normal publish carries two related records:

- `current_state` replaces the detailed account of where the work stands.
- `changes` appends one dated, immutable update about what changed.

The command writes both records atomically. The page never shows one without
the other.

## Publish Payload

`visual-brief publish` accepts `--file F` or `-`, plus the optional existing
`--run RUN` selector. Its payload has exactly two top-level fields:

```json
{
  "current_state": {
    "headline": "The detailed publishing contract is active",
    "summary": "Every important detail is individually addressable.",
    "lanes": [
      {
        "id": "working-now",
        "name": "What works now",
        "items": [
          {
            "id": "structured-state",
            "glance": "The current snapshot uses lanes and items.",
            "explanation": "It uses the same visible schema as dated updates.",
            "trust": "verified-by-me"
          }
        ]
      }
    ]
  },
  "changes": {
    "id": "detailed-state-contract",
    "timestamp": "2026-08-01T12:00:00Z",
    "headline": "Publishing gained a detailed current snapshot",
    "summary": "The snapshot changed while this update entered history.",
    "lanes": []
  }
}
```

The publish-side `current_state` object has exactly `headline`, `summary`, and
`lanes`. The command copies `changes.timestamp` into stored state as
`updated_at`; callers do not submit it.

Lanes and items use the same visible content schema as dated-update lanes and
items. A lane has `id`, `name`, and `items`, plus the existing optional `open`
preference. An item has `id`, `glance`, `explanation`, and `trust`, plus
optional `forensics` and `tables`.

Agents never submit `questions` anywhere inside `current_state`. The queue,
fold, and answer commands own stored conversations.

## Plain-Language Rules

The state headline and summary must use ordinary language that a reader can
understand without internal codenames, unexplained abbreviations, bare file
names, or compressed status notation. Detailed content belongs in lanes and
items rather than in a compressed root summary.

The command rejects mechanical forms that reliably indicate cryptic prose:
lists, headings, tables, code fences, arrows, and status chains. The headline
is one line of at least four words and at most 200 characters. The summary is
one punctuated sentence of at least four words and at most 480 characters.
Software cannot determine whether otherwise valid prose is understandable;
the writing contract supplies that semantic rule.

Items retain the existing mechanical lint rules. Enumerations belong in
separate items, tables, or forensic notes instead of a `glance`, an
`explanation`, or a conversation turn.

## Stored Document and Compatibility

New stored state has this shape:

```json
{
  "updated_at": "2026-08-01T12:00:00Z",
  "headline": "The detailed publishing contract is active",
  "summary": "Every important detail is individually addressable.",
  "lanes": [],
  "questions": []
}
```

`questions` is optional at the root, on lanes, and on items. It appears only
when the tool has stored conversations there.

Documents without `current_state` remain valid. Their first normal publish
adds detailed state. The already-shipped legacy object also remains valid:

```json
{
  "updated_at": "2026-08-01T10:00:00Z",
  "goal": "Keep the existing brief readable.",
  "focus": "The compatibility view remains active.",
  "blocker": null,
  "next": "Replace it with a structured publish."
}
```

Legacy state may render in its prior read-only card. The next normal publish
replaces it with structured state. New runs start with structured state.

`add-update` remains available for imports and compatibility. It appends
history without changing state and warns that normal reports use `publish`.

## Stable Identity Namespace

Current-state rows and chat anchors occupy a namespace that dated updates
cannot spell. Authored identifiers cannot contain `/`, so every state identity
starts with two slashes:

| Owner | Row and anchor identity |
| --- | --- |
| State root | `//current-state` |
| Lane `L` | `//current-state/lanes/L` |
| Item `I` | `//current-state/items/I` |

Lane identity depends only on the lane id. Item identity depends only on the
item id, not its containing lane. State item ids must therefore be unique
across every state lane. Moving item `I` between lanes keeps its row, evidence,
draft, cursor, and chat anchor identities.

Conversation rows append `#<thread-id>` to their owner's anchor. Evidence uses
the existing `#~evidence` suffix and stable named-note suffixes. Dated update
paths retain their existing `update/lane/item` semantics.

## Conversation Preservation

Before installing replacement state, `publish` indexes existing structured
state by root, lane id, and globally unique item id. It copies each existing
conversation list onto the matching replacement owner. The agent payload does
not repeat those conversations.

Moving an item with the same id preserves its conversations because the item
anchor does not include a lane id. If replacement state removes a lane or item
that directly owns any conversation, the command rejects the complete publish
with a clear error. No state or history byte changes.

Removing an empty owner remains valid. Removing a lane whose child item moved
elsewhere also remains valid unless the lane itself owns a conversation.

The dated `changes` object follows every existing update rule. A duplicate
update id rejects the complete publish. Existing dated updates retain their
content and paths; later `fold` and `answer` writes may still add conversation
turns through the established commands.

## Shared Backend Ownership

Structured state participates in the same owner and thread indexes as dated
updates. These indexes drive:

- queue folding and stale-anchor reporting;
- answer lookup and reply-target validation;
- unanswered and run-list counts;
- duplicate-thread detection;
- legacy thread normalization;
- mechanical linting;
- schema validation; and
- safe embedded-document projection.

The root, every state lane, and every state item are valid queue anchors. A
folded state question uses the same thread shape, timestamp ordering, pending
matching, and answer lifecycle as an update question.

## Shared Frontend Interaction

Detailed current state appears before dated history in a calm, compact visual
container. Its root, lanes, items, conversations, and evidence all enter the
same Solid row outline used by updates. State rows therefore support Chat,
keyboard selection, row and lane movement, folds, drafts, search, jump labels,
`m` reveal and restore, pending and working signs, new-answer state, map
navigation, and document counts.

The legacy four-claim card remains outside the outline and stays read-only.

## Live-Patch Invariants

A publish patches the running Solid document rather than replacing the page.
Stable row ids and keyed reconciliation preserve unchanged state and history
DOM nodes. Human-owned state remains untouched: folds, drafts, cursor, the
`m` snapshot, search, overlays, scroll, pending submissions, working signs,
and seen-answer records.

An item move may change its visual parent, but its row id, anchor, draft,
cursor, conversation identity, and evidence identity remain stable. Composer
cleanup rechecks an apparently absent row after reconciliation settles, so a
transient move cannot discard a draft.

## Verification

Focused tests cover atomic replacement, exact payload fields, plain-language
rejection, legacy compatibility, global state item identity, conversation
carry-forward, item moves, owner-removal rejection, backend chat lifecycle,
row interaction, live DOM preservation, and human-state preservation.

Release verification includes all non-browser Python tests, all frontend
tests, TypeScript checking, a production frontend build, the bundle stamp
check, and `git diff --check`. Browser automation is outside this feature's
verification scope.
