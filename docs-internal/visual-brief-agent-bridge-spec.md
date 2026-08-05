# Visual Brief agent bridge contract

## Purpose

Let the same Visual Brief run receive questions in either a Claude Code or a
Codex session without cloning the browser, queue, fold, answer, render, or run
discovery code.

The existing file queue remains the one durable reverse channel. Agent-specific
code only decides how a newly appended queue record wakes its owning session.

## Shared command

Add one command:

```text
visual-brief watch --agent claude|codex [--run RUN]
```

Both agents use the same queue parsing and file-following rules. The public
follower used by Claude starts at the current end of `questions.jsonl`, matching
the existing `tail -n 0 -F` contract, and follows a replaced file as well as an
appended file. It emits only complete, valid question records and never mutates
the briefing document.

The Claude adapter writes each accepted record to standard output immediately.
The skill's persistent `Monitor` wraps this command instead of spelling out a
separate `tail` pipeline.

The Codex adapter sends each accepted record to the current Codex thread. It
defaults to `CODEX_THREAD_ID` and `CCTOOLS_CODEX_CALLBACK_ENDPOINT`, which are
provided when the TUI is launched with `codex-dynamic`. Explicit `--thread-id`
and `--endpoint` options may override those defaults for manual setups.

Codex durably records its queue inode and acknowledged byte offset inside the
run directory. On the first watch it records the current end before validating
the delivery target, so existing history is ignored without losing a question
appended during validation. It advances the cursor only after confirmed
delivery or an explicit legacy-record skip. A restarted watcher resumes the
same queue position; replacement or truncation starts at byte zero, where
deterministic message IDs make any replay reconcilable.

Only local `unix://` app-server endpoints are supported in this iteration.
Missing or incompatible setup must fail before watching, with a short message
that explains how to restart or resume through `codex-dynamic`. An ordinary
already-running TUI cannot be retrofitted in place.

## Queue identity

Every newly accepted question record gains an opaque `message_id`. Existing
readers must continue accepting old records without it. The browser's current
text-and-timestamp pending identity remains unchanged.

The Codex `clientUserMessageId` is deterministic from the Visual Brief run
instance and queue `message_id`. A retry must therefore be reconcilable against
the thread history without posting the same human message twice.

## Codex delivery

The Codex adapter uses the current app-server protocol:

- verify that the requested thread is loaded on the selected server;
- use `turn/start` when the thread is idle;
- use `turn/steer` for an ordinary active turn;
- if review or manual compaction rejects steering, wait for idle and then use
  `turn/start`;
- pass `clientUserMessageId` and confirm the echoed user-message item;
- after an ambiguous disconnect, inspect thread history before retrying;
- bound retries and diagnostics rather than retrying forever.

The model-visible message identifies the Visual Brief run and preserves the
human's text byte-for-byte inside an explicitly untrusted envelope. It asks the
agent to fold the queue, reply briefly on the page first when substantial work
will follow, and answer the resulting thread through the existing CLI. The
adapter does not fold or answer on the agent's behalf.

## Reuse requirement

Do not create a second WebSocket or app-server delivery implementation. Extract
the generic thread-message delivery used by the dynamic-workflow completion
callback, and use it from both callers. Parameterize the app-server client
identity instead of hard-coding the dynamic-workflow client name.

The Visual Brief Codex helper may be a committed Node bundle included as
package data. Its source must import the shared app-server client and delivery
module. Generated bundles are expected duplication; source logic is not.

The Python package remains responsible for run selection and queue following.
It invokes the bundled helper with structured arguments and sends message text
through standard input, never through shell interpolation.

## Compatibility and scope

- No browser or document-schema changes are part of this work.
- Existing runs and old queue records remain readable.
- The default run root remains compatible with the existing installation;
  `VISUAL_BRIEF_HOME` continues to override it.
- The existing dynamic-workflow callback behavior must remain unchanged.
- No automatic answer mirroring is added. The agent still uses `fold` and
  `answer`, preserving the current conversation model.
- Do not add a second Visual Brief daemon or a second per-agent content store.
- Do not commit the implementation. New implementation files must be staged.

## Verification

Add focused tests for:

- shared queue following, including append and file replacement;
- Claude output and Codex invocation using the same accepted record;
- environment and explicit Codex target resolution;
- stable `message_id` propagation and legacy records without it;
- idle start, active steer, non-steerable wait, confirmation, and ambiguous
  retry behavior in the shared app-server delivery code;
- unchanged dynamic-workflow callback behavior;
- package inclusion of the generated Codex helper.

Run the full Visual Brief Python and frontend suites, the dynamic-workflow
typecheck and tests, rebuild all committed bundles, and perform a no-model-call
smoke test against a temporary Codex app server.
