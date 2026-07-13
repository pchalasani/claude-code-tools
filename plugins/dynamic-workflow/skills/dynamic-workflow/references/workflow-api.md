# Workflow API

The runtime accepts JavaScript script bodies rather than ordinary Node modules.
This permits a compatibility header, top-level `await`, and top-level `return`:

```javascript
export const meta = { name: "example", description: "Example workflow" }
const result = await agent("Do one focused task.", { id: "task" })
return result
```

`export const meta` is optional and currently informational. Other imports and
exports are rejected. The runtime injects the globals documented below.

## `agent(prompt, options?)`

Spawns one `codex exec --json` worker and returns its final message. If `schema`
is present, the final message is parsed and returned as structured JSON.

| Option | Meaning |
|--------|---------|
| `id` | Stable step ID used for durable cache replay |
| `label` | Human-readable name in state and logs |
| `cacheKey` | Extra dependency value included only in the cache fingerprint |
| `schema` | JSON Schema passed through `--output-schema` |
| `model` | Optional Codex model override |
| `reasoningEffort` | `minimal`, `low`, `medium`, `high`, or `xhigh` |
| `cwd` | Worker directory, relative to the workflow directory setting |
| `sandbox` | `read-only`, `workspace-write`, or `danger-full-access` |
| `addDirs` | Additional writable directories for a new Codex thread |
| `retries` | Automatic retry count after the first attempt; maximum 5 |
| `timeoutMs` | Worker deadline in milliseconds; default 30 minutes |
| `resumeThreadId` | Continue an existing Codex thread; see safety note below |
| `ignoreUserConfig` | Pass `--ignore-user-config` to the worker |

The default sandbox is `read-only`. Workers set approval policy `never` so a
headless process never waits for interactive approval. A resumed Codex thread
keeps its original working and sandbox configuration.

A script declaration alone cannot authorize writes. `run` and `resume` require
`--allow-workspace-write` before a `workspace-write` worker can launch, and
`--allow-danger-full-access` before a `danger-full-access` worker can launch.
Authorization is stored with the run. The danger flag also authorizes ordinary
workspace writes.
Authorization is bound to the current workflow source hash. Editing the script
invalidates an earlier write authorization until `resume` receives the matching
flag again.

The runner cannot inspect an existing Codex thread's inherited sandbox.
`resumeThreadId` therefore requires `--allow-danger-full-access`, even when the
author believes that thread was read-only. This does not apply to resuming the
workflow run itself with the CLI `resume` command.

The runner stores the worker thread ID and token usage in run state. Workflow
code receives only the final string or parsed JSON; inspect state for metadata.
Prompts and final results are limited to 1 MB each. Context-capacity failures
are non-retryable and explain how to chunk the input before resuming. Failed
steps retain any worker thread ID and token usage emitted before the failure.

## `pipeline(items, worker, options?)`

Runs a callback for each list item with bounded concurrency and returns results
in input order:

```javascript
const results = await pipeline(
  items,
  (item, index) => agent(`Review ${item.path}.`, {
    id: "review",
    label: item.path,
  }),
  {
    concurrency: 4,
    key: (item, index) => item.id ?? String(index),
    label: "reviews",
    maxItems: 50,
  },
)
```

`key` values must be unique within the pipeline. The pipeline scope plus the
agent `id` creates a stable step ID for cache replay. If one callback fails,
the pipeline rejects, but already completed agent results remain stored.
`maxItems` is a required safety practice for dynamic discovery; the runner
throws before fan-out when the list exceeds it. An omitted cap defaults to 100,
and an explicit cap cannot exceed 1,000. Also set `maxItems` and `maxLength` in
the discovery worker's JSON Schema so oversized output fails at its source.

## `parallel(tasks, options?)`

Runs heterogeneous async callbacks with the same ordered, bounded scheduler:

```javascript
const [security, tests, docs] = await parallel(
  [
    () => agent("Review security.", { id: "security" }),
    () => agent("Review tests.", { id: "tests" }),
    () => agent("Review docs.", { id: "docs" }),
  ],
  { concurrency: 3, label: "reviewers" },
)
```

## Other globals

- `args`: parsed JSON from `--input`; `undefined` when omitted
- `checkpoint()`: cooperatively honor pause or cancel inside local loops
- `log(...values)`: append a timestamped message to the workflow log
- `workflow.runId`: durable run identifier

## Cache identity

A completed step is reused only when its stable step ID and fingerprint match.
The fingerprint includes the prompt, schema, `cacheKey`, model, reasoning
effort, working directory, sandbox, additional directories, user-config mode,
and resumed thread ID. Labels, retries, and timeouts do not change the result
fingerprint.

Changing JavaScript control flow can change automatically generated IDs. Use
explicit `id` values and pipeline keys for reliable resume behavior.

Cache dependencies are explicit. If a downstream prompt is constant but its
answer depends on upstream results or workspace state, pass those results or a
version digest as `cacheKey`. Otherwise a resume can correctly identify the
same invocation while the author incorrectly expected it to rerun.

Resume rereads the workflow file stored in `state.json` and retains the run's
original `args`, cwd, and concurrency. It does not invalidate every step merely
because source text changed. Each completed step is evaluated independently.

## CLI reference

```text
run <file> [--input JSON|@FILE] [--cwd DIR] [--concurrency N]
           [--max-agents N] [--max-runtime-ms N]
           [--agent-timeout-ms N]
           [--notify-current-thread]
           [--app-server-endpoint unix://PATH]
           [--notify-timeout-ms N]
           [--detach] [--json] [--allow-workspace-write]
           [--allow-danger-full-access]
validate <file>
status <run-id> [--json]
list [--json]
logs <run-id>
wait <run-id> [--json]
notify <run-id> [--force] [--json]
pause <run-id>
resume <run-id> [--foreground] [--json] [--allow-workspace-write]
                [--allow-danger-full-access]
cancel <run-id>
```

State defaults to `~/.codex/workflows/runs/<run-id>/`. Set
`CODEX_WORKFLOW_HOME` to move the state root and
`CODEX_WORKFLOW_CODEX_BIN` to select another Codex executable.

Every `run` and `resume` command requires explicit host execution for that
exact reviewed command. The supervisor writes durable state outside the normal
workspace and launches headless Codex processes whose model connections cannot
run under an inherited outer sandbox. Do not approve a generic `node` prefix.
The workflow VM remains restricted, and workers still use their declared
sandboxes with approval policy `never`. State-changing control and notification
commands need the same narrow approval when the sandbox cannot write the state
root; read-only inspection commands do not.

`--notify-current-thread` requires `--detach` and the `CODEX_THREAD_ID` that
Codex automatically supplies to tool shells. Before creating the run, the CLI
connects to `--app-server-endpoint` (default `unix://`) and verifies that the
thread is loaded there. The TUI must have been started with the same endpoint,
preferably with the optional `codex-dynamic` launcher from the
`claude-code-tools` Python package. The equivalent manual setup runs
`codex app-server --listen unix://` with `codex --remote unix://`.

Callbacks require Codex CLI 0.136.0 or newer. Version 0.136.0 is the first
compatible CLI release for this callback protocol. Codex still marks the
app-server and remote TUI interfaces as experimental, so their CLI and protocol
surfaces may evolve. Callback preflight also validates the version reported by
the connected App Server, so a stale external server fails before run creation.
The approved host execution for `run` lets the trusted notifier reach the Unix
socket; it never changes worker sandboxes.

The notifier begins only after the workflow is terminal. It uses `turn/start`
for an idle thread and `turn/steer` to extend an active one, then confirms the
echoed client message ID. Passive supervision adds no model calls. Reporting
invokes the model and consumes tokens whether it starts a new turn for an idle
thread or extends the active turn. The retry deadline defaults to 24 hours and
is capped at seven days, with no more than five delivery submissions. Callback
status is independent of run status and appears under `completionNotification`
in JSON status output.

`notify <run-id>` manually retries a definite callback failure and requires the
same command-scoped host approval as callback launch. An `unknown` status, or a
`sending` status after submission began, indicates ambiguous
delivery and requires `--force` after the target thread has been inspected for
the original message.

Each run directory contains:

- `state.json`: atomic run and agent state
- `control.json`: external pause, resume, or cancel request
- `events.jsonl`: raw lifecycle and Codex JSONL events
- `workflow.log`: concise human-readable progress
- `runner.log`: stdout and stderr for a detached runner
- `notification.log`: stdout and stderr for a callback sidecar
- `completion-notification.json`: independent callback delivery state
- `notification.lock/`: cross-process claim preventing duplicate notifiers
- `runner.lock/`: cross-process claim preventing duplicate runners
- `schemas/`: JSON Schemas supplied to structured workers
- `workflow-snapshots/`: exact source revisions executed by the runner

The default run budget is 100 worker launches, four hours per runner execution,
and 30 minutes per worker. `--max-agents` can raise the launch cap to at most
1,000. The supervisor owns the runner lock and force-stops runaway JavaScript
or worker process trees after graceful cancellation cannot finish. Engine and
worker process IDs are persisted so resume or cancel can remove an execution
orphaned by a supervisor crash before another engine starts. Each PID is paired
with its process-start identity. Cleanup refuses to signal a live PID when that
identity is missing or changed, which protects unrelated processes after PID
reuse.

If the workflow engine exits unexpectedly while its supervisor remains alive,
the supervisor terminates and verifies active worker process groups before it
records the run as failed and releases the runner lock.

A newly spawned worker receives its prompt only after its PID and process-start
identity are durable. If exit confirmation times out, the run stays in the
nonterminal `canceling` state with process identities intact. Retry `cancel` or
`resume` to continue cleanup; the runner does not report false completion.
Normal, failed, and canceled worker exit paths all drain the worker's process
group so descendants cannot outlive their recorded agent step.

Write-capable workers have at-least-once side-effect semantics if the operating
system kills the runner between a side effect and its completion record. Make
write steps idempotent and inspect workspace state before resuming a crashed
run.
