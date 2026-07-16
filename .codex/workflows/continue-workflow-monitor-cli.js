export const meta = {
  name: "continue-workflow-monitor-cli",
  description: "Fix the final monitoring CLI findings and certify the result",
}

const baseline = "77ffa50"
const scope = `
Continue the architectural pass for the observational codex-workflows monitoring
CLI from the current worktree. Preserve good changes already made since ${baseline}.

In scope:
- claude_code_tools/workflow_cli*.py
- claude_code_tools/workflow_runs.py
- claude_code_tools/workflow_store_io.py
- claude_code_tools/workflow_validation.py
- claude_code_tools/workflow_processes.py
- tests/test_workflow_*.py
- codex-workflows packaging, help, README, and its Starlight documentation

Hard exclusions:
- Do not edit claude_code_tools/codex_server*.py or their tests.
- Do not edit app-server, callback, notification, or dynamic-workflow runner code.
- Do not investigate or fix GitHub issues #98 or #99.
- Do not delete or stage pre-existing unrelated untracked files.
- Never use git add -A and never commit.

The CLI is strictly observational. It must not mutate runs, signal processes,
repair state, or launch agents. All production Python files must remain under 1,000
lines. Stage exact newly created source/test files only. Remove duplicate untracked
modules created by the preceding workflow when they are superseded, but do not
touch unrelated .claude, .astro, or older .codex/workflows artifacts.
`

const findings = [
  {
    severity: "important",
    title: "JSON emitter mutates the versioned payload contract",
    detail: "Normalize hostile Unicode at one contract boundary so Python payload " +
      "callers and emitted JSON agree; the emitter must be encoding-only.",
  },
  {
    severity: "important",
    title: "Optional terminal fingerprints reject valid mixed snapshots",
    detail: "Define one coherent compatibility policy for state/callback pairs where " +
      "only one legacy side has terminalFingerprint, and test it.",
  },
  {
    severity: "important",
    title: "Projection and validation have separate mutable schema authorities",
    detail: "Use one neutral immutable v1 manifest to derive projection and validation " +
      "field policy. Frozen wrappers containing mutable dictionaries are insufficient.",
  },
  {
    severity: "important",
    title: "Production bypasses the tested raw-to-typed record boundary",
    detail: "Make production use the authoritative raw-pair parser/factory, or remove " +
      "the misleading factory and test the actual production boundary directly.",
  },
  {
    severity: "minor",
    title: "Process policy depends on a platform-probe near-monolith",
    detail: "Separate pure identity grammar/comparison/context policy from native " +
      "Linux, Darwin, and Windows probes through an injected typed provider.",
  },
  {
    severity: "important",
    title: "Exact lookup mishandles case-insensitive POSIX filesystems",
    detail: "Recover canonical directory spelling after verified open on APFS-like " +
      "filesystems, or reject the alias; never fabricate a malformed run.",
  },
  {
    severity: "important",
    title: "Resumed runs are incorrectly classified as malformed",
    detail: "A resumed supervisor may have runnerStartedAt later than the workflow's " +
      "original startedAt. Remove the invalid chronology constraint and test resumes.",
  },
  {
    severity: "minor",
    title: "Invalid filter arguments can produce unbounded stderr",
    detail: "Bound echoed Click values for status, limit, and watch parameters while " +
      "preserving useful diagnostics and nonzero exits.",
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
    prompt: "Review module boundaries, dependency direction, duplicate authorities, " +
      "typed domain boundaries, maintainability, and production file lengths.",
  },
  {
    key: "reliability",
    prompt: "Adversarially review bounded reads, hostile state, immutable contracts, " +
      "filesystem races, process identity, cross-platform behavior, and non-mutation.",
  },
  {
    key: "ux-release",
    prompt: "Review list/show/watch behavior, JSON stability, terminal bounds, errors, " +
      "packaging, documentation, and focused regression coverage.",
  },
]

let fixResult = await agent(
  `${scope}\n\nFix all eight unresolved findings below at their root. Reconcile and
remove competing duplicate modules left by the prior workflow. Preserve one clear
authority for every contract. Add regressions and run focused tests in this writable
worker.\n\nFindings:\n${JSON.stringify(findings)}`,
  {
    id: "fix-final-findings",
    label: "Fix eight final findings",
    sandbox: "workspace-write",
    reasoningEffort: "high",
    timeoutMs: 2400000,
    cacheKey: findings,
    schema: fixSchema,
  },
)

let converged = false
let completedRounds = 0
let lastResults = []

for (let round = 1; round <= 4; round += 1) {
  await checkpoint()
  completedRounds = round

  const verification = await agent(
    `${scope}\n\nWritable verification round ${round}. Do not change tracked files.
Hash "git diff --binary ${baseline}" before and after and require an exact match.
Run the complete focused workflow-monitoring pytest set, Ruff, Ruff format check,
Pyright on in-scope modules, git diff --check, build/distribution checks, help and
representative JSON/list/show/watch smoke tests. Use writable temporary directories.
Confirm no excluded file changed and no required production/test module is untracked.
Return concrete failures only.`,
    {
      id: `verify-round-${round}`,
      label: `Writable verification ${round}`,
      sandbox: "workspace-write",
      reasoningEffort: "high",
      timeoutMs: 2400000,
      cacheKey: { round, fixResult },
      schema: reviewSchema,
    },
  )

  const reviews = await pipeline(
    lenses,
    lens => agent(
      `${scope}\n\nCold adversarial review round ${round}. Inspect the complete current
implementation and diff from ${baseline}. Do not edit and do not repeat resolved or
stylistic concerns. Return green only when this lens has no reproducible actionable
defect. Writable verification result for context:\n${JSON.stringify(verification)}
\nLens: ${lens.prompt}`,
      {
        id: `review-round-${round}`,
        label: `Round ${round} ${lens.key}`,
        sandbox: "read-only",
        reasoningEffort: "high",
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

  if (round === 4) {
    break
  }

  fixResult = await agent(
    `${scope}\n\nFix every valid finding from round ${round} at its root. Keep the
architecture coherent and add regressions. Run focused writable tests. Stage exact
new source/test files only; do not commit.\n\nFindings:\n${JSON.stringify(remaining)}`,
    {
      id: `fix-round-${round}`,
      label: `Fix continuation round ${round}`,
      sandbox: "workspace-write",
      reasoningEffort: "high",
      timeoutMs: 2400000,
      cacheKey: remaining,
      schema: fixSchema,
    },
  )
}

if (!converged) {
  throw new Error(
    `Monitoring CLI continuation did not converge after ${completedRounds} rounds: ` +
      JSON.stringify(lastResults),
  )
}

const finalCertification = await agent(
  `${scope}\n\nFinal release certification. Do not change tracked files. Re-run the
complete focused workflow-monitoring tests and all static/build/smoke checks in a
writable environment. Verify "git diff --binary ${baseline}" is unchanged across the
run, no excluded file changed, every required new module/test is staged, no obsolete
duplicate module remains, and every production Python file is below 1,000 lines.
Return green only if the monitoring CLI is independently ready to ship.`,
  {
    id: "final-certification",
    label: "Final monitoring CLI certification",
    sandbox: "workspace-write",
    reasoningEffort: "high",
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
  summary: finalCertification.summary,
}
