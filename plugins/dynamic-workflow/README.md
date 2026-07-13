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
