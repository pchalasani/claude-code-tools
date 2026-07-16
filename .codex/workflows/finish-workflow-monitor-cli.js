export const meta = {
  name: "finish-workflow-monitor-cli",
  description: "Finish and certify the monitoring CLI with transient retries",
}

const baseline = "77ffa50"
const retries = 3
const scope = `
Finish the observational codex-workflows monitoring CLI from the current worktree.
Preserve all correct work already completed since ${baseline}. A previous fixer may
have partially addressed the two seed findings below before model capacity ended its
worker; inspect current code and make every write idempotent.

In scope:
- claude_code_tools/workflow_cli*.py
- claude_code_tools/workflow_runs.py
- claude_code_tools/workflow_store_io.py
- claude_code_tools/workflow_validation.py
- claude_code_tools/workflow_processes.py
- tests/test_workflow_*.py
- codex-workflows packaging and documentation

Hard exclusions:
- Do not edit claude_code_tools/codex_server*.py or their tests.
- Do not edit app-server, callback, notification, or workflow-runner code.
- Do not work on GitHub issues #98, #99, or #100.
- Never use git add -A and never commit.
- Leave unrelated .claude, .astro, and older .codex/workflows artifacts untouched.

The CLI must remain strictly observational and bounded. It may not mutate runs,
signal processes, repair state, or launch agents. Keep every production Python file
under 1,000 lines. Stage exact newly created source/test files only.
`

const seedFindings = [
  {
    severity: "important",
    title: "Exact lookup is not bounded consistently across platforms",
    detail: "Exact safe-child lookup must remain bounded without scanning an entire " +
      "hostile catalog, while still handling canonical spelling on case-insensitive " +
      "POSIX and Windows filesystems.",
  },
  {
    severity: "important",
    title: "Escaped lone-surrogate step IDs invalidate the state snapshot",
    detail: "Projection must safely preserve or normalize hostile object keys before " +
      "UTF-8 encoding so one surrogate key cannot make an otherwise readable run " +
      "malformed. Add an end-to-end persisted-key regression.",
  },
]

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
      maxItems: 12,
      items: findingSchema,
    },
  },
}

const fixSchema = {
  type: "object",
  additionalProperties: false,
  required: ["summary", "changedFiles", "tests", "blockers"],
  properties: {
    summary: { type: "string", maxLength: 1600 },
    changedFiles: {
      type: "array",
      maxItems: 35,
      items: { type: "string", maxLength: 300 },
    },
    tests: {
      type: "array",
      maxItems: 20,
      items: { type: "string", maxLength: 500 },
    },
    blockers: {
      type: "array",
      maxItems: 10,
      items: { type: "string", maxLength: 800 },
    },
  },
}

const lenses = [
  {
    key: "architecture",
    prompt: "Review dependency direction, single contract authorities, module " +
      "cohesion, typed boundaries, duplicate code, and production file lengths.",
  },
  {
    key: "reliability",
    prompt: "Adversarially review bounded hostile-state reads, filesystem races, " +
      "Unicode, process identity, cross-platform behavior, and non-mutation.",
  },
  {
    key: "ux-release",
    prompt: "Review list/show/watch semantics, JSON and terminal bounds, errors, " +
      "packaging, documentation, and focused regression coverage.",
  },
]

let fixResult = await agent(
  `${scope}\n\nFinish root-cause fixes for both seed findings. First inspect whether the
previous interrupted worker left valid partial changes. Reconcile rather than stack
another patch. Add focused regressions and run the writable monitoring-CLI suite.
\nFindings:\n${JSON.stringify(seedFindings)}`,
  {
    id: "fix-seed-findings",
    label: "Finish two remaining fixes",
    sandbox: "workspace-write",
    reasoningEffort: "high",
    retries,
    timeoutMs: 2400000,
    cacheKey: seedFindings,
    schema: fixSchema,
  },
)

let converged = false
let completedRounds = 0
let lastResults = []

for (let round = 1; round <= 3; round += 1) {
  await checkpoint()
  completedRounds = round

  const verification = await agent(
    `${scope}\n\nWritable verification round ${round}. Do not change tracked files.
Hash "git diff --binary ${baseline}" before and after and require an exact match.
Run all focused workflow-monitoring tests, Ruff, Ruff format, Pyright, diff checks,
build/distribution checks, help, and representative list/show/watch/JSON smokes.
Use writable temporary directories. Confirm no excluded file changed and no required
new module or test is untracked.`,
    {
      id: `verify-round-${round}`,
      label: `Writable verification ${round}`,
      sandbox: "workspace-write",
      reasoningEffort: "high",
      retries,
      timeoutMs: 2400000,
      cacheKey: { round, fixResult },
      schema: reviewSchema,
    },
  )

  const reviews = await pipeline(
    lenses,
    lens => agent(
      `${scope}\n\nCold review round ${round}. Inspect the complete current code and
diff from ${baseline}. Do not edit. Report only reproducible actionable defects and
do not repeat resolved or stylistic concerns. Writable verification result:
${JSON.stringify(verification)}\n\nLens: ${lens.prompt}`,
      {
        id: `review-round-${round}`,
        label: `Round ${round} ${lens.key}`,
        sandbox: "read-only",
        reasoningEffort: "high",
        retries,
        timeoutMs: 2400000,
        cacheKey: { round, verification },
        schema: reviewSchema,
      },
    ),
    {
      concurrency: 3,
      key: lens => lens.key,
      label: `review-round-${round}`,
      maxItems: 3,
    },
  )

  lastResults = [verification, ...reviews]
  const remaining = lastResults.flatMap(result => result.findings)
  if (
    remaining.length === 0 &&
    lastResults.every(result => result.verdict === "green")
  ) {
    converged = true
    break
  }

  if (round === 3) {
    break
  }

  fixResult = await agent(
    `${scope}\n\nFix every valid round-${round} finding at its root. Keep changes
idempotent and the architecture coherent. Add regressions and run focused writable
tests. Stage exact new source/test files only.\n\nFindings:
${JSON.stringify(remaining)}`,
    {
      id: `fix-round-${round}`,
      label: `Fix round ${round}`,
      sandbox: "workspace-write",
      reasoningEffort: "high",
      retries,
      timeoutMs: 2400000,
      cacheKey: remaining,
      schema: fixSchema,
    },
  )
}

if (!converged) {
  throw new Error(
    `Monitoring CLI did not converge after ${completedRounds} rounds: ` +
      JSON.stringify(lastResults),
  )
}

const finalCertification = await agent(
  `${scope}\n\nFinal release certification. Do not change tracked files. Re-run the
complete focused workflow-monitoring suite and static/build/smoke checks in a
writable environment. Require the binary diff from ${baseline} to remain identical,
no excluded files to differ, all required new files to be staged, no obsolete
duplicates, and every production Python file below 1,000 lines. Return green only
when the monitoring CLI is independently ready to ship.`,
  {
    id: "final-certification",
    label: "Final monitoring CLI certification",
    sandbox: "workspace-write",
    reasoningEffort: "high",
    retries,
    timeoutMs: 2400000,
    cacheKey: { completedRounds, lastResults },
    schema: reviewSchema,
  },
)

if (
  finalCertification.verdict !== "green" ||
  finalCertification.findings.length > 0
) {
  throw new Error(
    `Final monitoring CLI certification failed: ${JSON.stringify(finalCertification)}`,
  )
}

return {
  converged: true,
  rounds: completedRounds,
  retriesPerWorker: retries,
  summary: finalCertification.summary,
}
