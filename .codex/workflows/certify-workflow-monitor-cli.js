export const meta = {
  name: "certify-workflow-monitor-cli",
  description: "Narrow final certification for the workflow monitoring CLI",
}

const baseline = "77ffa50"
const retries = 3
const scope = `
Certify only the observational codex-workflows monitoring CLI in the current
worktree. The broad architecture pass is complete. The last known issue was a Ruff
format mismatch in claude_code_tools/workflow_store_io.py and it has been formatted
locally. Do not reopen resolved stylistic concerns.

In scope: claude_code_tools/workflow_cli*.py, workflow_runs.py,
workflow_store_io.py, workflow_validation.py, workflow_processes.py,
tests/test_workflow_*.py, and codex-workflows packaging/documentation.

Do not edit codex_server files, callback/app-server/notification/runner code, or work
on issues #98, #99, or #100. Never use git add -A or commit. Leave unrelated
untracked artifacts untouched. The CLI must remain strictly observational and every
production Python file must remain below 1,000 lines.
`

const findingSchema = {
  type: "object",
  additionalProperties: false,
  required: ["severity", "title", "detail", "file", "line"],
  properties: {
    severity: { type: "string", enum: ["blocking", "important", "minor"] },
    title: { type: "string", maxLength: 200 },
    detail: { type: "string", maxLength: 1800 },
    file: { type: ["string", "null"], maxLength: 300 },
    line: { type: ["integer", "null"], minimum: 1 },
  },
}

const reviewSchema = {
  type: "object",
  additionalProperties: false,
  required: ["verdict", "summary", "findings"],
  properties: {
    verdict: { type: "string", enum: ["green", "changes_requested"] },
    summary: { type: "string", maxLength: 1400 },
    findings: {
      type: "array",
      maxItems: 10,
      items: findingSchema,
    },
  },
}

const fixSchema = {
  type: "object",
  additionalProperties: false,
  required: ["summary", "changedFiles", "tests", "blockers"],
  properties: {
    summary: { type: "string", maxLength: 1400 },
    changedFiles: {
      type: "array",
      maxItems: 20,
      items: { type: "string", maxLength: 300 },
    },
    tests: {
      type: "array",
      maxItems: 15,
      items: { type: "string", maxLength: 500 },
    },
    blockers: {
      type: "array",
      maxItems: 8,
      items: { type: "string", maxLength: 800 },
    },
  },
}

const lenses = [
  {
    key: "correctness",
    prompt: "Cold-review correctness, bounded hostile-state handling, filesystem and " +
      "process behavior, Unicode, immutability, and observational guarantees.",
  },
  {
    key: "release",
    prompt: "Cold-review CLI and JSON behavior, terminal bounds, packaging, docs, " +
      "tests, module boundaries, duplication, and production file lengths.",
  },
]

let previousFix = { summary: "Local Ruff formatter applied to the final known issue" }
let completedRounds = 0
let finalResults = []

for (let round = 1; round <= 2; round += 1) {
  await checkpoint()
  completedRounds = round

  const verification = await agent(
    `${scope}\n\nWritable release certification round ${round}. Do not change tracked
files. Hash "git diff --binary ${baseline}" before and after and require equality.
Run the complete focused workflow-monitoring pytest suite, Ruff lint and format
checks, Pyright, git diff --check, distribution/build tests, installed help, and
representative list/show/watch/JSON smoke tests using writable temporary directories.
Confirm no excluded files changed, every required new source/test file is staged,
no obsolete duplicate remains, and all production modules are below 1,000 lines.`,
    {
      id: `verify-round-${round}`,
      label: `Final writable certification ${round}`,
      sandbox: "workspace-write",
      reasoningEffort: "high",
      retries,
      timeoutMs: 2400000,
      cacheKey: { round, previousFix },
      schema: reviewSchema,
    },
  )

  const reviews = await pipeline(
    lenses,
    lens => agent(
      `${scope}\n\nFinal cold review round ${round}. Do not edit. Inspect the full
current implementation and diff from ${baseline}. Report only reproducible defects
that should block release; do not repeat resolved or purely stylistic concerns.
Writable certification result:\n${JSON.stringify(verification)}\n\nLens:
${lens.prompt}`,
      {
        id: `review-round-${round}`,
        label: `Final ${lens.key} review ${round}`,
        sandbox: "read-only",
        reasoningEffort: "high",
        retries,
        timeoutMs: 2400000,
        cacheKey: { round, verification },
        schema: reviewSchema,
      },
    ),
    {
      concurrency: 2,
      key: lens => lens.key,
      label: `final-reviews-${round}`,
      maxItems: 2,
    },
  )

  finalResults = [verification, ...reviews]
  const remaining = finalResults.flatMap(result => result.findings)
  if (
    remaining.length === 0 &&
    finalResults.every(result => result.verdict === "green")
  ) {
    return {
      converged: true,
      rounds: completedRounds,
      retriesPerWorker: retries,
      summary: verification.summary,
    }
  }

  if (round === 2) {
    break
  }

  previousFix = await agent(
    `${scope}\n\nFix every valid finding below at its root. Keep changes idempotent
and narrow, add focused regressions, and run writable tests. Stage exact new source
or test files only.\n\nFindings:\n${JSON.stringify(remaining)}`,
    {
      id: `fix-round-${round}`,
      label: `Final bounded repair ${round}`,
      sandbox: "workspace-write",
      reasoningEffort: "high",
      retries,
      timeoutMs: 2400000,
      cacheKey: remaining,
      schema: fixSchema,
    },
  )
}

throw new Error(
  `Final CLI certification did not converge after ${completedRounds} rounds: ` +
    JSON.stringify(finalResults),
)
