---
name: dynamic-workflow
description: >-
  Create, review, run, inspect, pause, resume, and cancel durable JavaScript
  workflows that coordinate multiple headless Codex agents. Use for dynamic
  fan-out and fan-in, per-item analysis, multi-stage agent pipelines, loops or
  branches driven by worker results, long background runs, and ports of Claude
  Code dynamic workflows. Do not use for a small linear task that one Codex
  turn can handle directly.
---

# Dynamic Workflow

Use a deterministic JavaScript program to own control flow while separate
Codex workers do the reasoning and tool work. The runtime uses direct
`codex exec --json`; it does not require MCP or an API key beyond the normal
Codex CLI authentication.

## Locate the runner

Resolve `bin/workflow.mjs` two directories above this `SKILL.md` and use its
absolute path for every command. Do not assume the current repository contains
the plugin or that a plugin-root environment variable exists.

Set both paths explicitly, replacing the first value with the directory that
contains this loaded `SKILL.md`, then verify prerequisites:

```bash
SKILL_DIR="/absolute/path/to/skills/dynamic-workflow"
RUNNER="$(cd "$SKILL_DIR/../.." && pwd)/bin/workflow.mjs"
node --version
codex --version
node "$RUNNER" help
```

Node.js 20 or newer is required. The committed bundle needs no `npm install`.

## Decide whether to create a workflow

Use a workflow when JavaScript control flow materially reduces context or
coordinates at least one of these patterns:

- discover items, fan out one worker per item, then synthesize
- run heterogeneous agents in parallel and combine their results
- branch or loop based on structured worker output
- execute a long run in the background with durable progress
- reuse or port an existing dynamic workflow script

Continue directly for one or two ordinary sequential tasks.

## Author the script

Read [references/workflow-api.md](references/workflow-api.md) before writing or
debugging a workflow. Start from
[assets/workflow-template.js](assets/workflow-template.js) when useful.

Save project workflows under `.codex/workflows/<name>.js`. A workflow uses a
Claude-compatible script body with injected globals, top-level `await`, and a
top-level `return`:

```javascript
export const meta = {
  name: "audit-routes",
  description: "Audit every route for missing authorization",
}

const found = await agent(
  "Find every API route. Return method, path, and source file per route.",
  {
  id: "discover",
  schema: {
    type: "object",
    required: ["routes"],
    properties: {
      routes: {
        type: "array",
        items: {
          type: "object",
          required: ["method", "path", "file"],
          properties: {
            method: { type: "string" },
            path: { type: "string" },
            file: { type: "string" },
          },
        },
      },
    },
  },
  },
)

const audits = await pipeline(
  found.routes,
  route => agent(
    `Audit ${route.method} ${route.path} in ${route.file} for missing ` +
      "authentication and authorization. Return evidence and severity.",
    {
    id: "audit",
    label: `${route.method} ${route.path}`,
    sandbox: "read-only",
    },
  ),
  {
    concurrency: 4,
    key: route => `${route.method}-${route.path}`,
    maxItems: 50,
  },
)

const summary = await agent(
  `Deduplicate and rank these route audits:\n${JSON.stringify(audits)}`,
  { id: "synthesize", cacheKey: audits, sandbox: "read-only" },
)

return { audits, summary }
```

Follow these rules:

- Give every important `agent()` call a stable `id`.
- Use `schema` when later JavaScript reads fields from an agent result.
- Keep discovery and review workers in `read-only` unless writes are required.
- Use `workspace-write` only when the user authorized edits.
- Partition parallel write work by file or worktree to avoid conflicts.
- Set a task-specific `maxItems` on every dynamically discovered pipeline.
- Bound loops explicitly and call `checkpoint()` inside long local loops.
- Bound discovery arrays in JSON Schema with `maxItems` and string lengths.
- Request compact worker output; use chunked or tree reduction for large fan-in.
- Set explicit `timeoutMs` and use at most five retries for transient failures.
- Keep prompts self-contained because workers do not share conversation state.
- Put upstream results in downstream prompts or `cacheKey` to avoid stale cache.
- Return only the compact result the parent Codex session needs.

Workflow code has no injected filesystem, shell, `process`, or module import
access. It delegates all such work to sandboxed agents. The Node VM is a
capability boundary for accidental access, not a hardened hostile-code sandbox;
always review generated code before running it.

## Validate and obtain approval

Run syntax validation before launch:

```bash
node "$RUNNER" validate .codex/workflows/<name>.js
```

Show the user the workflow source and summarize an agent-count formula and cap,
concurrency, overall runtime, worker timeout, sandbox modes, expected writes,
context-sensitive fan-in, and likely cost. A request to create a workflow is
not approval to launch newly generated code. Obtain explicit launch approval
after review. A request to run a previously reviewed script counts as launch
approval, but never silently escalate a worker to `danger-full-access`.

After approval, add `--allow-workspace-write` to `run` or `resume` when any
worker declares `workspace-write`. Add `--allow-danger-full-access` only for a
separately approved danger-full-access run. The runtime rejects write-capable
workers when the corresponding authorization is absent.
Authorization is bound to the reviewed source hash, so editing a write-capable
workflow requires the flag again on its next launch or resume.

## Run and monitor

Foreground execution is useful for short runs:

```bash
node "$RUNNER" run .codex/workflows/<name>.js \
  --cwd "$PWD" --input '{"target":"src"}'
```

Use detached execution for a long run:

```bash
node "$RUNNER" run .codex/workflows/<name>.js \
  --cwd "$PWD" --input '{"target":"src"}' --detach --json \
  --max-agents 50 --max-runtime-ms 7200000 --agent-timeout-ms 900000
```

Capture the returned run ID. Continue monitoring until the requested outcome
is terminal unless the user asked only to launch it. For a long run, poll
`status --json` at bounded intervals and provide periodic updates. Use `wait`
only when completion is expected soon or the user explicitly requested a
blocking wait.

```bash
node "$RUNNER" status <run-id> --json
node "$RUNNER" logs <run-id>
node "$RUNNER" wait <run-id> --json
```

Controls are cooperative. Pause lets active workers finish and blocks new
workers. Resume replays the script and returns cached results for completed
steps whose IDs and fingerprints still match.

```bash
node "$RUNNER" pause <run-id>
node "$RUNNER" resume <run-id>
node "$RUNNER" cancel <run-id>
```

Before resuming after a script edit:

1. Run `status <run-id> --json` and read the stored workflow path.
2. Review and validate the current file at that path.
3. Explain which rendered prompts or cache keys changed and will rerun.
4. Renew approval if sandbox, write scope, concurrency, model, or cost changed.
5. Run `resume <run-id> --json` with any newly approved authorization flag.
6. Monitor the resumed run to a terminal state.

Resume rereads the current workflow file and reuses the stored `args`, cwd, and
concurrency. It launches detached when the old runner is gone unless
`--foreground` is present. Unchanged siblings remain cached. A downstream step
reruns only if its own prompt, options, or `cacheKey` changes.

On failure, inspect the failed step and logs. Fix the script or environment,
then use `resume`; completed compatible steps remain cached. Do not delete the
run directory merely to retry.

Treat a context-capacity error as non-retryable. Reduce or chunk the failing
prompt, replace large fan-in with a tree reduction, validate the edit, and then
resume so compatible completed siblings stay cached.

## Safety boundaries

- Headless workers use approval policy `never`, so they fail instead of waiting
  for an unavailable prompt.
- The default Codex sandbox is `read-only`.
- `workspace-write` also requires the CLI flag `--allow-workspace-write`.
- `danger-full-access` requires a specific user decision for that workflow.
- `danger-full-access` also requires `--allow-danger-full-access` at launch.
- One failed pipeline item fails the run; completed sibling results persist.
- A run defaults to 100 worker launches and can be raised only up to 1,000.
- A run defaults to a four-hour deadline and workers to a 30-minute timeout.
- A pipeline without `maxItems` is capped at 100; explicit caps stop at 1,000.
- Agent retries are limited to five; prompts and results are capped at 1 MB.
- The default concurrency is 6; choose a lower value for write-heavy work.
- A supervisor can terminate runaway workflow JavaScript and worker trees.
- Resume and cancel remove engine or worker groups orphaned by a supervisor
  crash.
- The supervisor removes active worker groups after an unexpected engine exit.
- Workers receive prompts only after their process ownership is durable.
- Every worker exit drains surviving descendants in its owned process group.
- Persisted process IDs are signaled only when their process-start identity
  still matches, preventing cleanup from killing a reused PID.
- Unconfirmed cleanup remains recoverable and nonterminal for a later retry.
- Never leave `agent()`, `pipeline()`, or `parallel()` promises unawaited.
