# visual-brief — running feedback notes

Reported by the human from the live page. Not yet fixed, not yet specified.
Kept here so nothing is lost between sessions; each becomes a contract clause
when its turn comes.

## Confirmed still broken after the live-patch change (2026-07-28)

1. **The "agent is working" sign flaps.** On send: the sign appears, then
   vanishes as the awaiting chips arrive, then reappears up to a minute later.
   Removing the reload was assumed to fix this and did not, so the cause is
   something else and is currently unknown. Reported three separate times; the
   human calls it the ugliest thing on the page.

2. **A turn beginning with a number loses it.** An answer written as
   `7. And the point ...` renders with the `7.` gone: markdown reads it as an
   ordered-list marker and the number becomes a bullet the page does not show.
   The human asked what 3+4 was and the answer was silently eaten. Any turn
   whose first line looks like a list marker is affected.

3. **New conversations sort below old ones.** An item that already carries
   several threads lists the newest last, so a question just asked appears at
   the bottom, under everything the human wrote hours ago. They expected to
   land on what they just wrote.

## Still open from the handoff's fix list

4. Markdown does not render in an update `summary` or an item `glance` — two
   one-line view changes.

5. `publish-now` silently drops conversations whose anchor no longer exists; it
   reports the loss to stderr and exits 0. Cost the human ten threads.

6. The "Ask" reversion: chat buttons momentarily flip to "Ask", `c` stops
   working while `a` works. The assets stamp added on 2026-07-28 closes the one
   mechanism that was ever identified (a tab on an older bundle); unverified.

7. The visual redesign: bigger default text, a hideable left structure panel,
   human turns in blue against agent turns in neutral ink, an explicit
   dark/light toggle with dark the default and both palettes properly tuned,
   and non-linear layout — cards, columns, tables.

8. A SKILL rule: an agent must post a brief acknowledgement before disappearing
   into work a chat implies, then do it, then answer properly.

9. Starlight docs in `docs-site/`, once the design settles. The CLI verbs are
   agent plumbing and stay out of user-facing docs.

## The cursor does not walk what the reader is looking at (2026-07-28)

11. **`j`, `k` and the arrows skip every conversation.** They walk rows of kind
    `item` only (`cursor.ts:41`, requested by `navigation.ts:285`), so a chat
    row is never stepped onto in the ordinary view. Diagnosed as NOT a
    regression from the live-patch change: it has been so since the owned
    cursor was introduced. The human's expectation is the plain one — the
    cursor should move to the next thing they are looking at, and an open chat
    is a thing they are looking at. So the arrows should walk what is
    **painted**, conversations included, rather than one privileged kind.

12. **Nothing reaches a conversation that was just answered.** `n` visits only
    threads still *awaiting* (`cursor.ts:96`), so the moment an answer lands
    the row it lands in becomes unreachable by every key; only a hint label or
    a click gets there. `n` should walk anything **outstanding** — unanswered
    or newly answered and unseen — and its label should say so.

## A half-written message is thrown away by ordinary navigation (2026-07-28)

13. **Opening another row closes the chat box and loses the draft.** Type into
    a chat box without sending, then open a different lane or row: the row
    holding the box folds shut and the unsent words are gone. The human did not
    expect either half — they expected the row to stay open and the message to
    still be there. Today's work made a draft survive a *publish*; it does not
    survive the reader simply looking at something else, which is the more
    common case. A message the human has typed is theirs, and nothing but
    sending it or explicitly discarding it should be able to destroy it.

14. **Enter jumps to the "what I need from you" lane.** Enter is deliberately
    left to the browser so a keyboard reader can open a fold the way buttons
    open everywhere (`keys.ts:187`), which means it presses whichever button
    still holds focus — the masthead's unanswered counter runs
    jump-to-next-unanswered, landing the reader in the lane holding the open
    questions. Nothing is wandering; an invisible button is being clicked. Fix
    by giving Enter a meaning at the cursor, or by not leaving focus parked on
    a control the reader cannot see.

## The Now panel needs redesigning, not patching (2026-07-28)

15. **State and events are jumbled together, and nothing says when.** The Now
    panel is rewritten on every publish, so it is a *snapshot of the present*.
    Conversations are the opposite: each one happened at a moment and stays
    true forever. Anchoring the second kind inside the first is what produces
    the confusion the human describes — old chats sitting beside fresh claims,
    with no way to tell which arrived just now and which has been there for
    hours, and stale threads hanging under items whose meaning has since
    moved on.

    The direction, in the human's words and mine: chats leave the Now panel
    entirely and live on their own dated timeline that merely *points* at what
    each was about; and everything the panel does show carries a visible sense
    of its own age, so "what changed since I last looked" is answerable at a
    glance rather than by memory. This is a design pass, and it belongs with
    the visual redesign (item 7) rather than being bolted on before it.

    Confirmed working in the same session: publishing into an open page left a
    half-written message intact. The live patching itself is not in question.

16. **Correction from the human: Now was never meant to be a state model.**
    The original intention for this whole feature was narrow and worth
    restating: *let Claude Code present its updates in an easy-to-digest way.*
    Asking the panel to reflect current state pushes a curation burden onto
    the agent that agents are bad at — they will happily emit prose, but
    deciding what still counts as true, what to retire, and what would
    overwhelm the reader is a judgement they get wrong, and the failure mode
    is precisely the accumulation the human is seeing ("what works now" ending
    up beside "what worked before").

    So the primary object goes back to being **one dated update per publish**,
    written to be read once and then left alone — which is what an agent is
    actually good at producing. Anything pinned above them must be small,
    fixed in shape and cheap to maintain; a decision queue ("these are waiting
    on you") is that, and a hand-curated portrait of the whole system is not.
    Treat this as superseding the state framing in item 15.

17. **No pinned panel either — just the newest update on top.** Even a small
    pinned region is a thing the agent has to maintain and can get wrong. If
    something is waiting on the human, the agent says so *in the update it is
    writing anyway*, exactly as it would in the terminal. The page then differs
    from the terminal in one respect only: the words go through the CLI verbs
    and land at the top of a browser page instead of scrolling past. That keeps
    the agent doing what it is trained to do, and keeps the session log a
    faithful record of the same conversation, only in JSON.

    **This deletes a bug class rather than fixing it.** Nothing is ever
    rewritten in place, so there is no reserved `now` id, no carry-over of
    conversations onto anchors that may have moved, and therefore no way for a
    publish to orphan a thread — item 5 above stops existing. It also ends the
    accumulation problem by construction: an update is a moment, and a moment
    never needs curating.

## How to have codex test against the real browser

Codex does have Chrome access, but not through the route to ask for by
default. Give it this prompt verbatim; it is the human's own wording and it
works:

> Use the existing Chrome plugin/extension connection to inspect my real
> browser. Do not use the built-in Browser or the chrome-devtools MCP.
> Read-only: list my open tabs and report the most recently focused tab's exact
> title and URL. Do not navigate or open a new tab. I expect: "A keyboard-first
> briefing page for working with Claude Code" at
> http://visual-brief.localhost:8765/.

Note also that `~/.codex/config.toml` sets `approval_mode = "approve"` on the
`chrome-devtools` MCP's `evaluate_script`, `new_page`, `take_screenshot` and
`take_snapshot`, so a detached unattended run stalls waiting for an approval
nobody can give. Browser testing in a loop goes through the extension
connection above, or through the package's own CDP harness in
`packages/visual-brief/tests`.

## Labelling

10. The `n` key is labelled "Awaiting" in the key bar, which does not say what
    the key does. It should read as an action — "next unanswered chat" — so the
    letter explains itself. (Reported earlier; verify whether it was already
    changed before spending anything on it.)

## Left undone after the updates-not-state pass (2026-07-29)

18. **The browser suite is flaky under load.** A full serial run passes 78 of
    78, but under contention a test occasionally fails and then passes alone —
    key delivery and observation race with the paint. The gate is therefore a
    weaker alarm than it looks. Fix by dispatching every tested key through the
    CDP harness and replacing fixed post-key sleeps with waits for the expected
    painted state.

19. **A draft does not follow the reader between the run's two addresses.**
    `<run>.localhost:PORT/` and `localhost:PORT/r/<run>/` are separate origins,
    so session storage is separate too. The cursor and the seen-answer marks
    have always had this limitation, and the store's own docstring claims
    otherwise. Either canonicalize onto one origin or stop claiming it.

20. **The plain walk stops at a lane boundary.** `j` and ArrowDown now step
    onto conversations and evidence, but lane and update headers were left to
    the shifted keys, so the walk ends at the last row inside a lane instead of
    carrying on into the next one. Same going up. The human's model is one
    continuous walk down everything painted, headers included, with the shifted
    keys as a fast skip between lanes rather than the only way to reach them.

## Confirmed working by the human on the live page (2026-07-29)

21. **`j` and the down arrow reach conversations as well as items.** Verified by
    the human on the live page, not only by a test. Note 11 is closed. Note 20
    (the walk stopping at a lane boundary) is the remaining half.

22. **An answered thread folds itself out of sight.** A conversation is opened
    while it is *awaiting*; the moment it is answered it stops qualifying and
    disappears inside its item, so the human went looking for a chat we had just
    finished and could not find it. A thread whose answer has not been read yet
    must stay open, and keep its new-answer mark, until it has been visited.

23. **The "N my chats" count grows forever and has stopped being useful.** It
    counts every conversation the human has ever written on the page, which
    under append-only updates only ever increases. Scope it to what is
    outstanding, or to the newest updates.

24. **Unreproduced: `m` showed only one conversation.** The human saw the
    my-chats view holding a single chat and one lane. Driving the live page from
    a fresh load, one press painted all twenty conversations across both
    updates. The one mechanism that would produce their reading is a search
    still being active, since entering the view only considers rows the search
    leaves on the page — awaiting confirmation from them before chasing it.

## v2 field notes (2026-07-29)

25. **Chat-thread text is needlessly tiny.** The thread row gets its own
    presentation with a deliberately small font; the human wants normal-size
    text, differentiated by colour or some other cue instead. Decision
    postponed to the design overhaul, along with the larger question of how
    chat threads relate to lanes/items visually.

26. **Bring back the colour-wave on "agent is working."** The human wants the
    Claude-Code-style shimmer sweeping through the text, alongside the pulsing
    dot. History: v1 had exactly that and it was removed deliberately — the
    travelling gradient left the words nearly invisible at the start of each
    cycle, which fed the "sign disappeared" reports. The redesign version must
    keep every glyph legible at every instant (e.g. a highlight sweeping OVER
    solid-colour text, never the text painted BY the gradient). Also note the
    per-frame fingerprint test asserts constant computed colour/background on
    the sign; restyling it means updating that test with it.

27. **A folded container should show it holds chats.** After collapse-all
    there is no visible cue which lanes or items have conversations inside, so
    the human cannot find where they have chatted without reopening
    everything. Whatever level is showing should mark containers with chats
    beneath — a variant of the containment rail, a count, or colour. Design
    decision deferred to the overhaul.

28. **A long question makes an awkward thread header.** The thread row titles
    itself with the question text; a long question overwhelms the header line
    and misplaces the controls beside it. Truncate the header to a short
    recognisable stub (the full text lives in the thread body anyway) so the
    row stays one tidy line. Ties into note 25's larger question of how thread
    rows are presented; small enough to fix before the overhaul if convenient.

29. **`m` must reveal, not filter.** Requirement correction from the human,
    superseding the my-chats *view*: pressing `m` should simply expand and
    reveal every conversation of theirs in place — the rest of the page stays
    exactly as it is; nothing disappears. No filter mode, no toggle state; a
    second press need do nothing (or collapse only what it opened). Under the
    v2 invariants this is a bulk human fold-action like E/C, writing `chosen`,
    which also deletes the last filter-view machinery. The active-search
    interaction stops existing because the mode stops existing. (The human has
    never used search; the search-preservation clauses only ever mattered
    because `m` was a filter.)
