import { readFileSync } from "node:fs";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { CodexRunner } from "../src/codex-runner.js";
import { compileWorkflow, WorkflowEngine } from "../src/engine.js";
import { StateStore } from "../src/state-store.js";
import type { RunState } from "../src/types.js";
import { isPidRunning, nowIso, sha256 } from "../src/utils.js";
import { createFakeCodex, waitFor } from "./helpers.js";

let temporaryDirectory: string;
let originalHome: string | undefined;
let originalCodexBin: string | undefined;
let originalFakeLog: string | undefined;

beforeEach(async () => {
  temporaryDirectory = await mkdtemp(path.join(tmpdir(), "workflow-engine-"));
  originalHome = process.env.CODEX_WORKFLOW_HOME;
  originalCodexBin = process.env.CODEX_WORKFLOW_CODEX_BIN;
  originalFakeLog = process.env.FAKE_CODEX_LOG;
  process.env.CODEX_WORKFLOW_HOME = path.join(temporaryDirectory, "state");
  process.env.CODEX_WORKFLOW_CODEX_BIN = await createFakeCodex(
    temporaryDirectory,
  );
  process.env.FAKE_CODEX_LOG = path.join(temporaryDirectory, "codex.jsonl");
});

afterEach(async () => {
  restoreEnvironment("CODEX_WORKFLOW_HOME", originalHome);
  restoreEnvironment("CODEX_WORKFLOW_CODEX_BIN", originalCodexBin);
  restoreEnvironment("FAKE_CODEX_LOG", originalFakeLog);
  await rm(temporaryDirectory, { force: true, recursive: true });
});

describe("compileWorkflow", () => {
  test("accepts the Claude-style meta block and top-level return", () => {
    expect(() =>
      compileWorkflow(
        "export const meta = { name: 'demo' }; return 42;",
        "demo.js",
      ),
    ).not.toThrow();
  });

  test("rejects module imports", () => {
    expect(() =>
      compileWorkflow("import fs from 'node:fs'; return fs;", "bad.js"),
    ).toThrow(/import statement|Unexpected token/);
  });
});

test("persists worker ownership before delivering its prompt", async () => {
  let allowRegistration: (() => void) | undefined;
  const registrationGate = new Promise<void>((resolve) => {
    allowRegistration = resolve;
  });
  let observedSpawn: (() => void) | undefined;
  const spawned = new Promise<void>((resolve) => {
    observedSpawn = resolve;
  });
  const runner = new CodexRunner(
    undefined,
    async () => {
      observedSpawn?.();
      await registrationGate;
    },
  );
  const controller = new AbortController();
  const running = runner.run({
    defaultTimeoutMs: 3_000,
    options: {},
    prompt: "ownership gate",
    runDirectory: temporaryDirectory,
    signal: controller.signal,
    stepId: "root/gated",
    workflowCwd: temporaryDirectory,
  });

  await spawned;
  await new Promise((resolve) => setTimeout(resolve, 100));
  expect(await invocationCount()).toBe(0);
  allowRegistration?.();
  await expect(running).resolves.toMatchObject({
    text: "result:ownership gate",
  });
  expect(await invocationCount()).toBe(1);
});

test("does not expose Node globals through injected host functions", async () => {
  const safeSource = "return typeof process";
  const safeStore = await createStore("safe-vm-run", safeSource);
  const safe = await new WorkflowEngine(safeStore, safeSource, () => {}).run();
  expect(safe.result).toBe("undefined");

  const escapeSource = 'return agent.constructor("return process")()';
  const escapeStore = await createStore("escape-vm-run", escapeSource);
  const escaped = await new WorkflowEngine(
    escapeStore,
    escapeSource,
    () => {},
  ).run();
  expect(escaped.status).toBe("failed");
  expect(escaped.error).toMatch(/Code generation from strings disallowed/);
});

test("enforces item caps before fan-out", async () => {
  const source = `
return await pipeline(
  ["a", "b"],
  item => agent(item, { id: "work" }),
  { maxItems: 1 },
)
`;
  const store = await createStore("capped-run", source);
  const final = await new WorkflowEngine(store, source, () => {}).run();
  expect(final.status).toBe("failed");
  expect(final.error).toMatch(/maximum is 1/);
  expect(final.agentInvocations).toBe(0);
});

test("applies a default pipeline cap when maxItems is omitted", async () => {
  const source = `
return await pipeline(
  Array.from({ length: 101 }, (_, index) => index),
  item => agent(String(item), { id: "work" }),
)
`;
  const store = await createStore("default-cap-run", source);
  const final = await new WorkflowEngine(store, source, () => {}).run();
  expect(final.status).toBe("failed");
  expect(final.error).toMatch(/maximum is 100/);
  expect(final.agentInvocations).toBe(0);
});

test("enforces the persisted run-level agent budget", async () => {
  const source = `
await agent("one", { id: "one" })
await agent("two", { id: "two" })
return await agent("three", { id: "three" })
`;
  const store = await createStore("agent-budget-run", source);
  await store.update((state) => {
    state.maxAgentInvocations = 2;
  });
  const final = await new WorkflowEngine(store, source, () => {}).run();
  expect(final.status).toBe("failed");
  expect(final.error).toMatch(/2-agent safety limit/);
  expect(final.agentInvocations).toBe(2);
});

test("uses cacheKey to invalidate an otherwise identical step", async () => {
  const firstSource = `
return await agent("same prompt", { id: "same", cacheKey: "version-1" })
`;
  const store = await createStore("dependency-run", firstSource);
  const first = await new WorkflowEngine(store, firstSource, () => {}).run();
  expect(first.status).toBe("completed");
  expect(await invocationCount()).toBe(1);

  await store.writeControl("run");
  const secondSource = `
return await agent("same prompt", { id: "same", cacheKey: "version-2" })
`;
  const second = await new WorkflowEngine(store, secondSource, () => {}).run();
  expect(second.status).toBe("completed");
  expect(await invocationCount()).toBe(2);
});

test("uses the supported Codex resume argument surface", async () => {
  const source = `
return await agent("continue", {
  id: "continued",
  resumeThreadId: "11111111-1111-4111-8111-111111111111",
})
`;
  const store = await createStore("thread-run", source);
  const authorization = {
    dangerFullAccess: true,
    workflowHash: sha256(source),
    workspaceWrite: true,
  };
  await store.update((state) => {
    state.authorization = authorization;
  });
  await store.writeControl("run", authorization);
  const final = await new WorkflowEngine(store, source, () => {}).run();
  expect(final.status).toBe("completed");

  const invocation = (await readInvocations())[0];
  expect(invocation?.args.slice(0, 3)).toEqual(["exec", "resume", "--json"]);
  expect(invocation?.args).not.toContain("--sandbox");
  expect(invocation?.args).not.toContain("--color");
});

test("requires persisted authorization for write-capable workers", async () => {
  const source = `
return await agent("edit one file", {
  id: "edit",
  sandbox: "workspace-write",
})
`;
  const deniedStore = await createStore("write-denied-run", source);
  const denied = await new WorkflowEngine(
    deniedStore,
    source,
    () => {},
  ).run();
  expect(denied.status).toBe("failed");
  expect(denied.error).toMatch(/--allow-workspace-write/);
  expect(denied.agentInvocations).toBe(0);

  const allowedStore = await createStore("write-allowed-run", source);
  const authorization = {
    dangerFullAccess: false,
    workflowHash: sha256(source),
    workspaceWrite: true,
  };
  await allowedStore.update((state) => {
    state.authorization = authorization;
  });
  await allowedStore.writeControl("run", authorization);
  const allowed = await new WorkflowEngine(
    allowedStore,
    source,
    () => {},
  ).run();
  expect(allowed.status).toBe("completed");
  expect((await readInvocations())[0]?.args).toContain("workspace-write");

  const changedSource = source.replace("edit one file", "edit a different file");
  await allowedStore.update((state) => {
    state.workflowHash = sha256(changedSource);
  });
  await allowedStore.writeControl("run", authorization);
  const invalidated = await new WorkflowEngine(
    allowedStore,
    changedSource,
    () => {},
  ).run();
  expect(invalidated.status).toBe("failed");
  expect(invalidated.error).toMatch(/does not match the current workflow/);
});

test("fans out, preserves order, and reuses completed agent results", async () => {
  const source = `
export const meta = { name: "audit" }
const discovered = await agent("discover", {
  id: "discover",
  schema: {
    type: "object",
    required: ["items"],
    properties: { items: { type: "array", items: { type: "string" } } },
  },
})
const results = await pipeline(
  discovered.items,
  item => agent(\`work \${item}\`, { id: "work", label: item }),
  { concurrency: 2 },
)
return results
`;
  const store = await createStore("cache-run", source);
  const first = await new WorkflowEngine(store, source, () => {}).run();

  expect(first.status).toBe("completed");
  expect(first.result).toEqual([
    "result:work a",
    "result:work b",
    "result:work c",
  ]);
  expect(Object.keys(first.steps)).toHaveLength(4);
  const firstInvocations = await invocationCount();
  expect(firstInvocations).toBe(4);
  const invocations = await readInvocations();
  expect(invocations[0]?.args).toContain("read-only");
  expect(invocations[0]?.args).toContain('approval_policy="never"');
  expect(invocations[0]?.args).toContain("--output-schema");

  await store.writeControl("run");
  const second = await new WorkflowEngine(store, source, () => {}).run();
  expect(second.status).toBe("completed");
  expect(await invocationCount()).toBe(firstInvocations);
  expect(await store.readLog()).toContain("cache hit");
});

test("pauses between active agents and resumes cooperatively", async () => {
  const source = `
const first = await agent("[delay=250] first", { id: "first" })
const second = await agent("second", { id: "second" })
return [first, second]
`;
  const store = await createStore("pause-run", source);
  const running = new WorkflowEngine(store, source, () => {}).run();

  await waitFor(
    () => store.snapshot().steps["root/first"]?.status === "running",
  );
  await store.writeControl("pause");
  await waitFor(() => store.snapshot().status === "paused");
  expect(store.snapshot().steps["root/second"]).toBeUndefined();

  await store.writeControl("run");
  const final = await running;
  expect(final.status).toBe("completed");
  expect(final.result).toEqual([
    "result:[delay=250] first",
    "result:second",
  ]);
});

test("cancels an active Codex worker", async () => {
  const source = `
return await agent("[delay=5000] slow", { id: "slow" })
`;
  const store = await createStore("cancel-run", source);
  const running = new WorkflowEngine(store, source, () => {}).run();

  await waitFor(
    () => store.snapshot().steps["root/slow"]?.status === "running",
  );
  await store.writeControl("cancel");
  const final = await running;
  expect(final.status).toBe("canceled");
  expect(final.steps["root/slow"]?.status).toBe("canceled");
});

test("fails only after unawaited agents are canceled and drained", async () => {
  const source = `
agent("[delay=5000] forgotten", { id: "forgotten" })
return 42
`;
  const store = await createStore("unawaited-run", source);
  const final = await new WorkflowEngine(store, source, () => {}).run();

  expect(final.status).toBe("failed");
  expect(final.error).toMatch(/await every agent/);
  expect(
    Object.values(final.steps).every((step) => step.status !== "running"),
  ).toBe(true);
});

test("drains slow siblings before a failed parallel run returns", async () => {
  const source = `
return await parallel([
  () => agent("[fail] fast", { id: "fast" }),
  () => agent("[delay=5000] slow", { id: "slow" }),
])
`;
  const store = await createStore("sibling-failure-run", source);
  const final = await new WorkflowEngine(store, source, () => {}).run();

  expect(final.status).toBe("failed");
  expect(
    Object.values(final.steps).every((step) => step.status !== "running"),
  ).toBe(true);
});

test("does not retry deterministic context exhaustion", async () => {
  const source = `
return await agent("[context-fail] oversized", {
  id: "context",
  retries: 3,
})
`;
  const store = await createStore("context-run", source);
  const final = await new WorkflowEngine(store, source, () => {}).run();

  expect(final.status).toBe("failed");
  expect(final.error).toMatch(/Chunk the input or use a tree reduction/);
  expect(await invocationCount()).toBe(1);
  expect(final.steps["root/context"]?.threadId).toBe(
    "thread-context-failure",
  );
  expect(final.steps["root/context"]?.usage?.inputTokens).toBe(99);
});

test("cancellation kills descendants that ignore SIGTERM", async () => {
  const grandchildPath = path.join(temporaryDirectory, "grandchild.pid");
  const source = `
return await agent(
  "[grandchild-ignore=${grandchildPath}][delay=5000] process tree",
  { id: "tree" },
)
`;
  const store = await createStore("process-tree-run", source);
  const running = new WorkflowEngine(store, source, () => {}).run();
  await waitFor(() => store.snapshot().steps["root/tree"]?.workerPid !== undefined);
  await waitFor(() => isPidRunning(readPid(grandchildPath)));
  const grandchildPid = readPid(grandchildPath);

  await store.writeControl("cancel");
  const final = await running;
  expect(final.status, final.error).toBe("canceled");
  await waitFor(() => !isPidRunning(grandchildPid));
});

test("an abnormal worker exit removes surviving descendants", async () => {
  const grandchildPath = path.join(
    temporaryDirectory,
    "failed-worker-grandchild.pid",
  );
  const source = `
return await agent(
  "[grandchild-ignore=${grandchildPath}][fail] process tree",
  { id: "tree" },
)
`;
  const store = await createStore("failed-process-tree-run", source);
  const final = await new WorkflowEngine(store, source, () => {}).run();
  const grandchildPid = readPid(grandchildPath);

  await waitFor(() => !isPidRunning(grandchildPid));
  expect(final.status).toBe("failed");
  expect(final.steps["root/tree"]?.status).toBe("failed");
});

test("a worker cleanup timeout preserves durable ownership", async () => {
  const grandchildPath = path.join(
    temporaryDirectory,
    "cleanup-pending-grandchild.pid",
  );
  const source = `
return await agent(
  "[grandchild-ignore=${grandchildPath}][fail] process tree",
  { id: "tree" },
)
`;
  const store = await createStore("cleanup-pending-run", source);
  const originalKill = process.kill.bind(process);
  const killSpy = vi.spyOn(process, "kill").mockImplementation(
    (pid, signal) => {
      const workerPid = store.snapshot().steps["root/tree"]?.workerPid;
      if (
        workerPid !== undefined &&
        pid === -workerPid &&
        (signal === 0 || signal === "SIGKILL")
      ) {
        return true;
      }
      return originalKill(pid, signal);
    },
  );

  try {
    const final = await new WorkflowEngine(store, source, () => {}).run();
    const step = final.steps["root/tree"];
    expect(final.status, final.error).toBe("canceling");
    expect(final.completedAt).toBeUndefined();
    expect(step?.status).toBe("running");
    expect(step?.workerPid).toBeDefined();
    expect(step?.workerStartedAt).toBeDefined();
  } finally {
    killSpy.mockRestore();
    const workerPid = store.snapshot().steps["root/tree"]?.workerPid;
    if (workerPid !== undefined) {
      try {
        originalKill(
          process.platform === "win32" ? workerPid : -workerPid,
          "SIGKILL",
        );
      } catch {
        // Test cleanup is best effort after the owned process group exits.
      }
    }
  }
});

async function createStore(runId: string, source: string): Promise<StateStore> {
  const timestamp = nowIso();
  const state: RunState = {
    agentInvocations: 0,
    concurrency: 3,
    createdAt: timestamp,
    cwd: temporaryDirectory,
    runId,
    status: "starting",
    steps: {},
    updatedAt: timestamp,
    version: 1,
    workflowHash: sha256(source),
    workflowPath: path.join(temporaryDirectory, `${runId}.js`),
  };
  return await StateStore.create(state);
}

async function invocationCount(): Promise<number> {
  return (await readInvocations()).length;
}

async function readInvocations(): Promise<
  Array<{ args: string[]; prompt: string }>
> {
  try {
    const contents = await readFile(process.env.FAKE_CODEX_LOG as string, "utf8");
    return contents
      .trim()
      .split("\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line) as { args: string[]; prompt: string });
  } catch {
    return [];
  }
}

function restoreEnvironment(name: string, value: string | undefined): void {
  if (value === undefined) {
    delete process.env[name];
  } else {
    process.env[name] = value;
  }
}

function readPid(filePath: string): number {
  try {
    return Number(readFileSync(filePath, "utf8"));
  } catch {
    return -1;
  }
}
