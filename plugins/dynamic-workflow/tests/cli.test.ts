import { execFile, spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";

import { afterEach, beforeEach, expect, test } from "vitest";

import { StateStore } from "../src/state-store.js";
import type { RunState } from "../src/types.js";
import { isPidRunning, nowIso } from "../src/utils.js";
import { createFakeCodex } from "./helpers.js";

const execFileAsync = promisify(execFile);
const packageDirectory = path.resolve(import.meta.dirname, "..");
const cliPath = path.join(packageDirectory, "bin", "workflow.mjs");

let temporaryDirectory: string;
let environment: NodeJS.ProcessEnv;

beforeEach(async () => {
  temporaryDirectory = await mkdtemp(path.join(tmpdir(), "workflow-cli-"));
  const codex = await createFakeCodex(temporaryDirectory);
  environment = {
    ...process.env,
    CODEX_WORKFLOW_CODEX_BIN: codex,
    CODEX_WORKFLOW_HOME: path.join(temporaryDirectory, "state"),
    FAKE_CODEX_LOG: path.join(temporaryDirectory, "codex.jsonl"),
  };
  delete environment.CCTOOLS_CODEX_CALLBACK_ENDPOINT;
});

afterEach(async () => {
  await rm(temporaryDirectory, { force: true, recursive: true });
});

test("runs and inspects a workflow through the bundled CLI", async () => {
  const workflowPath = path.join(temporaryDirectory, "workflow.js");
  await writeFile(
    workflowPath,
    'return await agent("hello", { id: "hello" })\n',
    "utf8",
  );

  const run = await invoke([
    "run",
    workflowPath,
    "--cwd",
    temporaryDirectory,
    "--json",
  ]);
  expect(run.stdout, run.stderr).not.toBe("");
  const state = JSON.parse(run.stdout) as RunState;
  expect(state.status).toBe("completed");
  expect(state.result).toBe("result:hello");
  const snapshotPath = path.join(
    environment.CODEX_WORKFLOW_HOME as string,
    "runs",
    state.runId,
    "workflow-snapshots",
    `${state.workflowHash}.js`,
  );
  expect(await readFile(snapshotPath, "utf8")).toContain('agent("hello"');

  const status = await invoke(["status", state.runId, "--json"]);
  expect((JSON.parse(status.stdout) as RunState).status).toBe("completed");

  const listed = await invoke(["list", "--json"]);
  expect(JSON.parse(listed.stdout)).toHaveLength(1);

  const validated = await invoke(["validate", workflowPath]);
  expect(validated.stdout).toContain(": valid");
});

test("runs a detached workflow and waits for its durable result", async () => {
  const workflowPath = path.join(temporaryDirectory, "detached.js");
  await writeFile(
    workflowPath,
    'return await agent("[delay=100] detached", { id: "work" })\n',
    "utf8",
  );

  const launched = await invoke([
    "run",
    workflowPath,
    "--detach",
    "--json",
  ]);
  const { runId } = JSON.parse(launched.stdout) as { runId: string };
  const waited = await invoke(["wait", runId, "--json"]);
  expect(waited.stdout, waited.stderr).not.toBe("");
  const state = JSON.parse(waited.stdout) as RunState;
  expect(state.status).toBe("completed");
  expect(state.result).toBe("result:[delay=100] detached");
});

test("records a detached runner bootstrap failure as terminal state", async () => {
  const runId = "missing-workflow-run";
  const originalHome = process.env.CODEX_WORKFLOW_HOME;
  process.env.CODEX_WORKFLOW_HOME = environment.CODEX_WORKFLOW_HOME;
  try {
    const timestamp = nowIso();
    await StateStore.create({
      agentInvocations: 0,
      authorization: {
        dangerFullAccess: false,
        workflowHash: "missing",
        workspaceWrite: false,
      },
      concurrency: 1,
      createdAt: timestamp,
      cwd: temporaryDirectory,
      runId,
      status: "starting",
      steps: {},
      updatedAt: timestamp,
      version: 1,
      workflowHash: "missing",
      workflowPath: path.join(temporaryDirectory, "missing.js"),
    });
  } finally {
    if (originalHome === undefined) {
      delete process.env.CODEX_WORKFLOW_HOME;
    } else {
      process.env.CODEX_WORKFLOW_HOME = originalHome;
    }
  }

  await expect(invoke(["_execute", runId])).rejects.toMatchObject({ code: 1 });
  const status = await invoke(["status", runId, "--json"]);
  const state = JSON.parse(status.stdout) as RunState;
  expect(state.status).toBe("failed");
  expect(state.error).toMatch(/Runner bootstrap failed/);
});

test("preserves a pause requested during detached startup", async () => {
  const workflowPath = path.join(temporaryDirectory, "pause-startup.js");
  await writeFile(
    workflowPath,
    `
await agent("[delay=300] first", { id: "first" })
return await agent("second", { id: "second" })
`,
    "utf8",
  );
  const launched = await invoke([
    "run",
    workflowPath,
    "--detach",
    "--json",
  ]);
  const { runId } = JSON.parse(launched.stdout) as { runId: string };
  await invoke(["pause", runId]);
  const paused = await waitForRun(runId, (state) => state.status === "paused");
  expect(paused.steps["root/second"]).toBeUndefined();

  await invoke(["cancel", runId]);
  const canceled = await waitForRun(
    runId,
    (state) => state.status === "canceled",
  );
  expect(canceled.status).toBe("canceled");
});

test("binds explicit write authorization to the workflow source", async () => {
  const workflowPath = path.join(temporaryDirectory, "write.js");
  await writeFile(
    workflowPath,
    `
return await agent("edit", { id: "edit", sandbox: "workspace-write" })
`,
    "utf8",
  );
  const result = await invoke([
    "run",
    workflowPath,
    "--allow-workspace-write",
    "--json",
  ]);
  const state = JSON.parse(result.stdout) as RunState;
  expect(state.status).toBe("completed");
  expect(state.authorization?.workspaceWrite).toBe(true);
  expect(state.authorization?.workflowHash).toBe(state.workflowHash);
});

test("forcibly cancels runaway workflow JavaScript", async () => {
  const workflowPath = path.join(temporaryDirectory, "runaway.js");
  await writeFile(workflowPath, "await checkpoint(); while (true) {}\n", "utf8");
  const launched = await invoke([
    "run",
    workflowPath,
    "--detach",
    "--json",
  ]);
  const { runId } = JSON.parse(launched.stdout) as { runId: string };

  await invoke(["cancel", runId]);
  const state = await waitForRun(runId, (item) => item.status === "canceled");
  expect(state.error).toBe("Workflow canceled");
});

test("fails runaway JavaScript at the persisted runtime deadline", async () => {
  const workflowPath = path.join(temporaryDirectory, "deadline.js");
  await writeFile(workflowPath, "while (true) {}\n", "utf8");
  const launched = await invoke([
    "run",
    workflowPath,
    "--detach",
    "--json",
    "--max-runtime-ms",
    "300",
  ]);
  const { runId } = JSON.parse(launched.stdout) as { runId: string };

  const state = await waitForRun(runId, (item) => item.status === "failed");
  expect(state.error).toMatch(/300 ms runtime limit/);
});

test("a completed run wins a simultaneous supervisor deadline", async () => {
  const workflowPath = path.join(temporaryDirectory, "deadline-race.js");
  await writeFile(workflowPath, "return 42\n", "utf8");
  const result = await invoke([
    "run",
    workflowPath,
    "--json",
    "--max-runtime-ms",
    "1000",
  ]);
  const state = JSON.parse(result.stdout) as RunState;
  expect(state.status).toBe("completed");
  expect(state.result).toBe(42);
});

test("a runtime deadline removes active worker descendants", async () => {
  const workflowPath = path.join(temporaryDirectory, "tree-deadline.js");
  const grandchildPath = path.join(temporaryDirectory, "deadline-child.pid");
  await writeFile(
    workflowPath,
    `return await agent(
  "[grandchild=${grandchildPath}][delay=5000] tree",
  { id: "tree" },
)\n`,
    "utf8",
  );
  const launched = await invoke([
    "run",
    workflowPath,
    "--detach",
    "--json",
    "--max-runtime-ms",
    "2000",
  ]);
  const { runId } = JSON.parse(launched.stdout) as { runId: string };
  const state = await waitForRun(runId, (item) => item.status === "failed");
  const grandchildPid = Number(await readFile(grandchildPath, "utf8"));
  try {
    const deadline = Date.now() + 2_000;
    while (isPidRunning(grandchildPid) && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    expect(isPidRunning(grandchildPid)).toBe(false);
    expect(state.error).toMatch(/2000 ms runtime limit/);
  } finally {
    if (isPidRunning(grandchildPid)) {
      process.kill(grandchildPid, "SIGKILL");
    }
  }
});

test("rejects run IDs that could escape the state directory", async () => {
  await expect(invoke(["status", "../outside", "--json"]))
    .rejects.toMatchObject({ code: 1 });
});

test("resume replaces an engine orphaned by a supervisor crash", async () => {
  const workflowPath = path.join(temporaryDirectory, "orphan.js");
  await writeFile(workflowPath, "while (true) {}\n", "utf8");
  const launched = await invoke([
    "run",
    workflowPath,
    "--detach",
    "--json",
  ]);
  const launch = JSON.parse(launched.stdout) as { pid: number; runId: string };
  const initial = await waitForRun(
    launch.runId,
    (state) => state.enginePid !== undefined && state.status === "running",
  );
  const oldEnginePid = initial.enginePid as number;

  process.kill(launch.pid, "SIGKILL");
  await waitForProcessExit(launch.pid);
  try {
    await invoke(["resume", launch.runId, "--json"]);
    const resumed = await waitForRun(
      launch.runId,
      (state) =>
        state.enginePid !== undefined &&
        state.enginePid !== oldEnginePid &&
        state.status === "running",
    );
    expect(isPidRunning(oldEnginePid)).toBe(false);
    expect(resumed.status).toBe("running");

    await invoke(["cancel", launch.runId]);
    const canceled = await waitForRun(
      launch.runId,
      (state) => state.status === "canceled",
    );
    expect(canceled.enginePid).toBeUndefined();
    expect(canceled.pid).toBeUndefined();
  } finally {
    const state = await readRunState(launch.runId);
    killProcessGroup(state.enginePid);
    killProcessGroup(state.pid);
    killProcessGroup(oldEnginePid);
  }
});

test("an engine crash removes active worker descendants", async () => {
  const workflowPath = path.join(temporaryDirectory, "engine-crash.js");
  const grandchildPath = path.join(temporaryDirectory, "engine-child.pid");
  await writeFile(
    workflowPath,
    `return await agent(
  "[grandchild=${grandchildPath}][delay=5000] engine crash",
  { id: "worker" },
)\n`,
    "utf8",
  );
  const launched = await invoke([
    "run",
    workflowPath,
    "--detach",
    "--json",
  ]);
  const launch = JSON.parse(launched.stdout) as { pid: number; runId: string };
  const running = await waitForRun(
    launch.runId,
    (state) =>
      state.enginePid !== undefined &&
      state.steps["root/worker"]?.workerPid !== undefined,
  );
  const enginePid = running.enginePid as number;
  const workerStep = running.steps["root/worker"];
  if (workerStep?.workerPid === undefined) {
    throw new Error("Active worker did not persist its PID");
  }
  const workerPid = workerStep.workerPid;
  const grandchildPid = await waitForPidFile(grandchildPath);

  killProcessGroup(enginePid);
  try {
    const failed = await waitForRun(
      launch.runId,
      (state) => state.status === "failed",
    );
    expect(failed.error).toMatch(/Workflow engine stopped/);
    expect(failed.steps["root/worker"]?.status).toBe("failed");
    expect(isPidRunning(workerPid)).toBe(false);
    expect(isPidRunning(grandchildPid)).toBe(false);
  } finally {
    killProcessGroup(workerPid);
    killProcessGroup(grandchildPid);
    killProcessGroup(launch.pid);
  }
});

test("resume refuses to signal a reused process ID", async () => {
  const workflowPath = path.join(temporaryDirectory, "pid-reuse.js");
  await writeFile(workflowPath, "while (true) {}\n", "utf8");
  const launched = await invoke([
    "run",
    workflowPath,
    "--detach",
    "--json",
  ]);
  const launch = JSON.parse(launched.stdout) as { pid: number; runId: string };
  const initial = await waitForRun(
    launch.runId,
    (state) => state.enginePid !== undefined && state.status === "running",
  );
  const oldEnginePid = initial.enginePid as number;
  const unrelated = spawn(
    process.execPath,
    ["-e", "setInterval(() => {}, 1000)"],
    { detached: process.platform !== "win32", stdio: "ignore" },
  );
  const unrelatedPid = unrelated.pid;
  if (unrelatedPid === undefined) {
    throw new Error("Unrelated test process did not receive a PID");
  }
  unrelated.unref();

  process.kill(launch.pid, "SIGKILL");
  await waitForProcessExit(launch.pid);
  try {
    const statePath = path.join(
      environment.CODEX_WORKFLOW_HOME as string,
      "runs",
      launch.runId,
      "state.json",
    );
    const stale = JSON.parse(await readFile(statePath, "utf8")) as RunState;
    stale.enginePid = unrelatedPid;
    stale.engineStartedAt = "different-process-start";
    await writeFile(statePath, `${JSON.stringify(stale, null, 2)}\n`, "utf8");

    await expect(invoke(["resume", launch.runId, "--json"]))
      .rejects.toMatchObject({
        code: 1,
        stderr: expect.stringMatching(/process identity changed/),
      });
    expect(isPidRunning(unrelatedPid)).toBe(true);
  } finally {
    killProcessGroup(unrelatedPid);
    killProcessGroup(oldEnginePid);
    killProcessGroup(launch.pid);
  }
});

async function invoke(args: string[]): Promise<{ stderr: string; stdout: string }> {
  return await execFileAsync(process.execPath, [cliPath, ...args], {
    cwd: temporaryDirectory,
    encoding: "utf8",
    env: environment,
    timeout: 8_000,
  });
}

async function waitForRun(
  runId: string,
  predicate: (state: RunState) => boolean,
): Promise<RunState> {
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    const result = await invoke(["status", runId, "--json"]);
    const state = JSON.parse(result.stdout) as RunState;
    if (predicate(state)) {
      return state;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`Timed out waiting for run ${runId}`);
}

async function readRunState(runId: string): Promise<RunState> {
  const result = await invoke(["status", runId, "--json"]);
  return JSON.parse(result.stdout) as RunState;
}

async function waitForProcessExit(pid: number): Promise<void> {
  const deadline = Date.now() + 3_000;
  while (isPidRunning(pid) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  if (isPidRunning(pid)) {
    throw new Error(`PID ${pid} did not exit`);
  }
}

async function waitForPidFile(filePath: string): Promise<number> {
  const deadline = Date.now() + 3_000;
  while (Date.now() < deadline) {
    try {
      return Number(await readFile(filePath, "utf8"));
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  }
  throw new Error(`Timed out waiting for PID file ${filePath}`);
}

function killProcessGroup(pid: number | undefined): void {
  if (pid === undefined) {
    return;
  }
  try {
    process.kill(process.platform === "win32" ? pid : -pid, "SIGKILL");
  } catch {
    // Test cleanup is best effort after the owned process has exited.
  }
}
