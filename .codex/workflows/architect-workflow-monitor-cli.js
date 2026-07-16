export const meta = {
  name: "architect-workflow-monitor-cli",
  description: "Refactor and certify only the codex-workflows monitoring CLI",
}

const baseline = "77ffa50"
const scope = `
Work only on the observational codex-workflows monitoring CLI introduced at
baseline commit ${baseline}. In scope:
- claude_code_tools/workflow_cli*.py
- claude_code_tools/workflow_runs.py
- claude_code_tools/workflow_store_io.py
- claude_code_tools/workflow_validation.py
- claude_code_tools/workflow_processes.py
- tests/test_workflow_*.py
- the codex-workflows entry point, packaging needed for those modules, and
  documentation specifically describing codex-workflows

Hard exclusions:
- Do not edit claude_code_tools/codex_server*.py or their tests.
- Do not edit app-server, callback, notification, or dynamic-workflow runner code.
- Do not investigate or fix GitHub issues #98 or #99. Those known unrelated full-
  suite failures are deferred.
- Do not delete or stage pre-existing untracked files.
- Never use git add -A. Stage only newly created source/test files; do not commit.

The CLI must remain strictly observational: it may read durable workflow state and
process metadata, but must never mutate runs, signal processes, repair state, or
launch agents. Preserve stable JSON output, safe bounded reads, hostile-state
handling, cross-platform behavior, and a useful terminal dashboard.
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
    summary: { type: "string", maxLength: 1200 },
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
      maxItems: 30,
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
    prompt: `Act as a senior Python architect. Inspect the current monitoring CLI
against ${baseline}. Identify patch-on-patch design, blurred responsibilities,
near-monoliths, duplicated validation/rendering logic, poor dependency direction,
and interfaces that make correctness hard to reason about. Propose the smallest
principled module boundaries. Test-file length is not a concern. Do not edit.`,
  },
  {
    key: "reliability",
    prompt: `Act as an adversarial reliability and security reviewer. Inspect only
the monitoring CLI data path. Reproduce concrete correctness, memory, filesystem,
process-identity, terminal, encoding, race, and hostile-state failures where useful.
The observer must remain non-mutating and bounded. Do not edit.`,
  },
  {
    key: "ux",
    prompt: `Act as a CLI and release reviewer. Evaluate list/show/watch UX, stable
JSON contracts, exit behavior, colors and non-TTY behavior, packaging, help text,
documentation, and focused test coverage. Ignore unrelated repository failures
tracked in #98 and #99. Do not edit.`,
  },
]

const initialReviews = await pipeline(
  lenses,
  lens => agent(
    `${scope}\n\n${lens.prompt}\n\nReturn only concrete, actionable findings.`,
    {
      id: "initial-review",
      label: `Initial ${lens.key} review`,
      sandbox: "read-only",
      reasoningEffort: "high",
      timeoutMs: 2400000,
      schema: reviewSchema,
    },
  ),
  {
    concurrency: 3,
    key: lens => lens.key,
    label: "initial-reviews",
    maxItems: 3,
  },
)

const initialFix = await agent(
  `${scope}\n\nImplement a principled architectural pass addressing all valid findings below.
Prefer clear modules and explicit invariants over incremental patches. Preserve
behavior unless a finding demonstrates it is wrong. Add focused regression tests.
Run relevant tests and static checks. If you create new source or test files, stage
those exact paths only. Do not stage modifications to existing files and do not
commit.\n\nReviews:\n${JSON.stringify(initialReviews)}`,
  {
    id: "initial-fix",
    label: "Implement architectural pass",
    sandbox: "workspace-write",
    reasoningEffort: "high",
    timeoutMs: 2400000,
    cacheKey: initialReviews,
    schema: fixSchema,
  },
)

let converged = false
let finalReviews = []
let completedRounds = 0

for (let round = 1; round <= 4; round += 1) {
  await checkpoint()
  completedRounds = round
  finalReviews = await pipeline(
    lenses,
    lens => agent(
      `${scope}\n\nThis is adversarial review round ${round}. Inspect the complete current
implementation and diff from ${baseline}; do not rely on previous reviewers. Run
focused read-only checks and report only reproducible, actionable defects. Withhold
green for real defects, not stylistic preferences.\n\nLens:\n${lens.prompt}`,
      {
        id: `review-round-${round}`,
        label: `Round ${round} ${lens.key}`,
        sandbox: "read-only",
        reasoningEffort: "high",
        timeoutMs: 2400000,
        cacheKey: { round, initialFix },
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

  const findings = finalReviews.flatMap(review => review.findings)
  if (findings.length === 0 && finalReviews.every(r => r.verdict === "green")) {
    converged = true
    break
  }

  if (round === 4) {
    break
  }

  await agent(
    `${scope}\n\nFix every valid finding from adversarial round ${round}. Reassess each
claim against the code, implement root-cause fixes with regression tests, and keep
the architecture coherent. Run focused tests and static checks. Stage only exact
new source/test files; do not commit.\n\nFindings:\n${JSON.stringify(findings)}`,
    {
      id: `fix-round-${round}`,
      label: `Fix round ${round}`,
      sandbox: "workspace-write",
      reasoningEffort: "high",
      timeoutMs: 2400000,
      cacheKey: findings,
      schema: fixSchema,
    },
  )
}

if (!converged) {
  throw new Error(
    `Monitoring CLI review did not converge after ${completedRounds} rounds: ` +
      JSON.stringify(finalReviews),
  )
}

const verification = await agent(
  `${scope}\n\nPerform final writable certification without changing tracked files.
Record the SHA-256 of "git diff --binary ${baseline}" before and after verification
and require them to match. Run all focused workflow-monitoring CLI tests, Ruff,
format checking, Pyright for the in-scope Python modules, build/distribution checks,
git diff --check, entry-point help smoke tests, and representative list/show/watch
or JSON smoke tests that do not mutate durable runs. Confirm no forbidden file was
changed from ${baseline}. Do not treat the unrelated failures in #98 or #99 as part
of this certification. Return changes_requested for any failure or tracked diff
mutation.`,
  {
    id: "final-certification",
    label: "Final CLI certification",
    sandbox: "workspace-write",
    reasoningEffort: "high",
    timeoutMs: 2400000,
    cacheKey: { completedRounds, finalReviews },
    schema: reviewSchema,
  },
)

if (verification.verdict !== "green" || verification.findings.length > 0) {
  throw new Error(`Final monitoring CLI certification failed: ${JSON.stringify(verification)}`)
}

return {
  converged: true,
  rounds: completedRounds,
  summary: verification.summary,
}
