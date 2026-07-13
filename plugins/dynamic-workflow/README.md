# Dynamic Workflow

`dynamic-workflow` is a Codex plugin for deterministic, durable JavaScript
orchestration over headless Codex workers.

The model writes a small script. The local runtime owns loops, branches,
bounded concurrency, state, cache replay, pause, resume, and cancellation.
Workers run through `codex exec --json`; MCP and the OpenAI API are not required.

## Features

- Claude-style workflow bodies with `agent()`, `pipeline()`, and `args`
- structured worker output through JSON Schema
- ordered fan-out and fan-in with configurable concurrency
- durable state and completed-step cache replay across Codex sessions
- foreground or detached execution
- optional completion callbacks to the originating Codex thread
- cooperative pause, resume, cancel, retry, logs, and raw JSONL events
- supervisor-enforced run, agent, fan-out, prompt, and result limits
- process-tree cancellation and durable executed-source snapshots
- read-only workers by default with per-agent sandbox declarations
- committed dependency-free runtime bundle

## Install

Node.js 20 or newer and an authenticated Codex CLI are required.

```bash
codex plugin marketplace add pchalasani/claude-code-tools
codex plugin add dynamic-workflow@cctools-codex-plugins
```

Restart Codex after installation, then invoke `$dynamic-workflow` or ask Codex
to create a durable multi-agent workflow.

For local development from the repository root:

```bash
codex plugin marketplace add "$PWD"
codex plugin add dynamic-workflow@cctools-codex-plugins
```

## Direct CLI use

The skill resolves the installed runner automatically. From this directory:

```bash
node bin/workflow.mjs validate path/to/workflow.js
node bin/workflow.mjs run path/to/workflow.js --detach --json \
  --max-agents 50 --max-runtime-ms 7200000 --agent-timeout-ms 900000
node bin/workflow.mjs status <run-id> --json
node bin/workflow.mjs wait <run-id>
```

## Notify the originating Codex thread

Ordinary detached runs can be observed with `wait`, but a passive wait process
cannot wake an idle model turn. For a true callback, run the TUI through Codex's
shared app server. The optional Python CLI helper provides the simplest setup:

```bash
uv tool install claude-code-tools
codex-dynamic
```

If the package is already installed, refresh it with
`uv tool install --force claude-code-tools`. To continue the most recent
conversation, run `codex-dynamic resume --last` instead.

The plugin itself does not depend on that Python package. The equivalent manual
setup is:

```bash
# Terminal 1
codex app-server --listen unix://

# Terminal 2
codex --remote unix://
```

Callbacks require Codex CLI 0.136.0 or newer. Version 0.136.0 is the first
compatible CLI release combining the WebSocket HTTP Upgrade protocol with the
echoed client message IDs used to confirm callback delivery. Codex still marks
the app-server and remote TUI interfaces as experimental, so their CLI and
protocol surfaces may evolve.
Callback preflight validates the connected App Server too, so a stale external
server fails before the workflow is created.

To move an existing conversation onto the server manually, exit its current TUI
and run `codex resume --remote unix://`, then select that session. An
already-running local TUI cannot reconnect in place.

From that TUI, ask `$dynamic-workflow` to run the workflow in the background and
notify the current thread. Every `run` or `resume` needs host execution for that
exact reviewed supervisor command. This lets the supervisor write durable state
and launch headless workers without inheriting the outer tool sandbox. For a
callback, the same authorization lets its notifier reach the host Unix socket.
Workflow JavaScript still runs in the restricted VM, and every Codex worker
keeps its declared sandbox. The runner uses this opt-in launch form:

```bash
node bin/workflow.mjs run workflow.js --detach --notify-current-thread
```

A sandboxed tool launch stops before creating a run and explains the required
approval. Approve only the exact runner command, never a generic `node` prefix.
Do not grant `danger-full-access` to workers merely to enable the callback.

When the workflow finishes, a bounded sidecar connects to the same Unix socket.
It starts a turn if the thread is idle. If a turn is active, it steers the
completion into and extends that turn, so you can keep chatting while the
workflow runs. This uses the Codex app server directly; it does not use MCP or
require another API key. The optional Python helper only manages the local
server process.

Passive supervision and the local app server add no model calls. Completion
reporting starts a new turn only while the thread is idle; otherwise it extends
the active turn. Either reporting path invokes the model and consumes tokens.
If delivery cannot be confirmed, workflow success is preserved and callback
status is available separately:

```bash
node bin/workflow.mjs status <run-id> --json
node bin/workflow.mjs notify <run-id>
```

The `notify` retry also opens the host socket and changes durable state, so it
requires the same explicit, command-scoped host approval as the original run.

The callback defaults to a 24-hour retry deadline and at most five delivery
submissions. A callback in `unknown` state, or one left in `sending` after
submission began, is ambiguous and is not automatically duplicated. Inspect
the target thread before retrying it with `notify <run-id> --force`.

After reviewing a write-capable workflow, authorize it explicitly:

```bash
node bin/workflow.mjs run workflow.js --allow-workspace-write
```

Run `node bin/workflow.mjs help` for every command. Durable state defaults to
`~/.codex/workflows/runs/`.

## Develop

```bash
npm ci
npm run typecheck
npm test
```

`npm test` rebuilds `bin/workflow.mjs` and runs the fake-worker integration
suite. The committed bundle lets installed plugins run without npm packages.

## Security model

Workflow scripts run in a restricted Node VM without injected filesystem,
shell, process, or module APIs. That VM limits accidental capabilities but is
not a hardened sandbox for hostile JavaScript, so inspect generated scripts.

Each worker uses Codex sandboxing separately. Workers default to `read-only`
and use approval policy `never`, which makes unavailable permissions fail
instead of hanging a background process.

The runtime also refuses `workspace-write` and `danger-full-access` workers
unless the launcher supplies the matching authorization flag. That decision is
persisted with the run and bound to the reviewed source hash. Editing the script
requires renewed write authorization on resume.

Runs default to 100 worker launches, a four-hour runner deadline, and a
30-minute worker timeout. A supervisor owns each run, force-stops runaway
workflow JavaScript, and terminates complete worker process groups. Context
capacity failures are non-retryable so an unchanged oversized prompt is not
blindly repeated.

Completion callbacks are opt-in, require detached execution, and accept only a
local `unix://` app-server endpoint. Launch verifies that `CODEX_THREAD_ID` is
loaded on that server before creating the run. Notification retries have a hard
deadline, use a stable client message ID for confirmation, and never answer
approval or user-input requests on behalf of the TUI. Callback failure does not
change a workflow's terminal result.

If a supervisor crashes, its engine notices the lost owner during normal
execution. Resume and cancel also terminate any persisted orphan engine or
worker groups before changing the run state or starting replacement work.
An unexpected engine exit makes the supervisor terminate active worker groups
before recording the run as failed.
Before signaling a persisted PID, the runner verifies its recorded process
start identity and refuses to signal a PID that has been reused.
Workers receive their prompts only after ownership is durable. If process-exit
confirmation times out, the run remains nonterminal with cleanup state intact
so cancel or resume can retry safely.
Every worker exit path also drains its owned process group, including children
left behind after an abnormal worker exit.
