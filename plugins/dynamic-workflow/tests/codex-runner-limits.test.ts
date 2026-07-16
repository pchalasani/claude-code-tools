import {
  access,
  chmod,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, expect, test } from "vitest";

import {
  CodexRunner,
  isDeadProcessState,
} from "../src/codex-runner.js";
import type { CodexRequest } from "../src/types.js";
import { isPidRunning } from "../src/utils.js";

let temporaryDirectory: string;
let originalCodexBin: string | undefined;

beforeEach(async () => {
  temporaryDirectory = await mkdtemp(path.join(tmpdir(), "codex-limits-"));
  originalCodexBin = process.env.CODEX_WORKFLOW_CODEX_BIN;
});

afterEach(async () => {
  if (originalCodexBin === undefined) {
    delete process.env.CODEX_WORKFLOW_CODEX_BIN;
  } else {
    process.env.CODEX_WORKFLOW_CODEX_BIN = originalCodexBin;
  }
  await rm(temporaryDirectory, { force: true, recursive: true });
});

test("terminates a worker tree with hostile newline-free stdout", async () => {
  const worker = await createWorker(`
await startGrandchild();
process.stdout.write(Buffer.alloc(9 * 1024 * 1024, 0x78));
setInterval(() => {}, 1000);
`);
  process.env.CODEX_WORKFLOW_CODEX_BIN = worker;
  let workerPid: number | undefined;
  let persistedEvents = 0;
  const runner = new CodexRunner(
    async () => {
      persistedEvents += 1;
    },
    async (_stepId, pid) => {
      workerPid = pid;
    },
  );

  await expect(runner.run(request())).rejects.toMatchObject({
    message: expect.stringMatching(/NDJSON record.*8388608-byte limit/),
    retryable: false,
  });

  const grandchildPid = await readPid("grandchild.pid");
  expect(persistedEvents).toBe(0);
  expect(isPidRunning(workerPid)).toBe(false);
  expect(isPidRunning(grandchildPid)).toBe(false);
});

test("rejects an oversized newline-terminated event before persistence", async () => {
  const worker = await createWorker(`
await startGrandchild();
const payload = "x".repeat(9 * 1024 * 1024);
process.stdout.write(JSON.stringify({ type: "progress", payload }) + "\\n");
setInterval(() => {}, 1000);
`);
  process.env.CODEX_WORKFLOW_CODEX_BIN = worker;
  let persistedEvents = 0;
  const runner = new CodexRunner(async () => {
    persistedEvents += 1;
  });

  await expect(runner.run(request())).rejects.toMatchObject({
    message: expect.stringMatching(/NDJSON record.*8388608-byte limit/),
    retryable: false,
  });

  const grandchildPid = await readPid("grandchild.pid");
  expect(persistedEvents).toBe(0);
  expect(isPidRunning(grandchildPid)).toBe(false);
});

test("bounds final agent text before persisting its event", async () => {
  const worker = await createWorker(`
await startGrandchild();
const text = "x".repeat(1_000_001);
process.stdout.write(JSON.stringify({
  type: "item.completed",
  item: { type: "agent_message", text },
}) + "\\n");
setInterval(() => {}, 1000);
`);
  process.env.CODEX_WORKFLOW_CODEX_BIN = worker;
  let persistedEvents = 0;
  const runner = new CodexRunner(async () => {
    persistedEvents += 1;
  });

  await expect(runner.run(request())).rejects.toMatchObject({
    message: expect.stringMatching(/result is 1000001 bytes; maximum is/),
    retryable: false,
  });

  const grandchildPid = await readPid("grandchild.pid");
  expect(persistedEvents).toBe(0);
  expect(isPidRunning(grandchildPid)).toBe(false);
});

test("bounds extracted worker errors before persisting their events", async () => {
  const worker = await createWorker(`
await startGrandchild();
const message = "x".repeat(32_001);
process.stdout.write(JSON.stringify({
  type: "turn.failed",
  error: { message },
}) + "\\n");
setInterval(() => {}, 1000);
`);
  process.env.CODEX_WORKFLOW_CODEX_BIN = worker;
  let persistedEvents = 0;
  const runner = new CodexRunner(async () => {
    persistedEvents += 1;
  });

  await expect(runner.run(request())).rejects.toMatchObject({
    message: expect.stringMatching(/event error is 32001 bytes; maximum is/),
    retryable: false,
  });

  const grandchildPid = await readPid("grandchild.pid");
  expect(persistedEvents).toBe(0);
  expect(isPidRunning(grandchildPid)).toBe(false);
});

test("rejects compact NDJSON with too many nodes before persistence", async () => {
  const worker = await createWorker(`
await startGrandchild();
const payload = Array.from({ length: 250_001 }, () => 0);
process.stdout.write(JSON.stringify({ type: "progress", payload }) + "\\n");
setInterval(() => {}, 1000);
`);
  process.env.CODEX_WORKFLOW_CODEX_BIN = worker;
  let persistedEvents = 0;
  const runner = new CodexRunner(async () => {
    persistedEvents += 1;
  });

  await expect(runner.run(request())).rejects.toMatchObject({
    message: expect.stringMatching(/NDJSON event.*node count of 250000/),
    retryable: false,
  });

  const grandchildPid = await readPid("grandchild.pid");
  expect(persistedEvents).toBe(0);
  expect(isPidRunning(grandchildPid)).toBe(false);
});

test("rejects deeply nested NDJSON before persistence", async () => {
  const worker = await createWorker(`
await startGrandchild();
const payload = "[".repeat(129) + "0" + "]".repeat(129);
process.stdout.write('{"type":"progress","payload":' + payload + "}\\n");
setInterval(() => {}, 1000);
`);
  process.env.CODEX_WORKFLOW_CODEX_BIN = worker;
  let persistedEvents = 0;
  const runner = new CodexRunner(async () => {
    persistedEvents += 1;
  });

  await expect(runner.run(request())).rejects.toMatchObject({
    message: expect.stringMatching(/NDJSON event.*maximum depth of 128/),
    retryable: false,
  });

  const grandchildPid = await readPid("grandchild.pid");
  expect(persistedEvents).toBe(0);
  expect(isPidRunning(grandchildPid)).toBe(false);
});

test("does not spawn an already-aborted worker", async () => {
  const markerPath = path.join(temporaryDirectory, "worker-ran");
  const worker = await createWorker(`
await writeFile(${JSON.stringify(markerPath)}, "ran", "utf8");
`);
  process.env.CODEX_WORKFLOW_CODEX_BIN = worker;
  const controller = new AbortController();
  const reason = new Error("canceled before launch");
  controller.abort(reason);

  await expect(new CodexRunner().run(request(controller.signal))).rejects.toBe(
    reason,
  );
  await expect(access(markerPath)).rejects.toBeDefined();
});

test("preserves malformed NDJSON record boundaries", async () => {
  const worker = await createWorker(`
process.stdout.write("bad-one\\nbad-two\\n");
process.exit(17);
`);
  process.env.CODEX_WORKFLOW_CODEX_BIN = worker;

  await expect(new CodexRunner().run(request())).rejects.toMatchObject({
    message: expect.stringContaining("bad-one\nbad-two"),
  });
});

test("bounds settlement when worker registration never resolves", async () => {
  const worker = await createWorker("setInterval(() => {}, 1000);");
  process.env.CODEX_WORKFLOW_CODEX_BIN = worker;
  const runner = new CodexRunner(
    undefined,
    async () => await new Promise<void>(() => {}),
  );
  const startedAt = Date.now();

  await expect(runner.run(request(undefined, 25))).rejects.toThrow(
    /persistence did not settle/,
  );
  expect(Date.now() - startedAt).toBeLessThan(4_500);
});

test("bounds settlement after an event persistence callback stalls", async () => {
  const worker = await createWorker(`
process.stdout.write(JSON.stringify({
  type: "item.completed",
  item: { type: "agent_message", text: "finished" },
}) + "\\n");
`);
  process.env.CODEX_WORKFLOW_CODEX_BIN = worker;
  const runner = new CodexRunner(
    async () => await new Promise<void>(() => {}),
  );
  const startedAt = Date.now();

  await expect(runner.run(request())).rejects.toThrow(
    /persistence did not settle/,
  );
  expect(Date.now() - startedAt).toBeLessThan(2_000);
});

test.each(["Z", "X", "x"])(
  "classifies Linux dead process state %s as non-running",
  (state) => {
    expect(isDeadProcessState(state)).toBe(true);
    expect(isDeadProcessState(`${state}+`)).toBe(true);
  },
);

test.each(["R", "S", "D", "T", "t", "I"])(
  "classifies live process state %s as running",
  (state) => {
    expect(isDeadProcessState(state)).toBe(false);
  },
);

function request(
  signal = new AbortController().signal,
  timeoutMs = 10_000,
): CodexRequest {
  return {
    defaultTimeoutMs: timeoutMs,
    options: {},
    prompt: "bounded output test",
    runDirectory: temporaryDirectory,
    signal,
    stepId: "root/limits",
    workflowCwd: temporaryDirectory,
  };
}

async function createWorker(body: string): Promise<string> {
  const workerPath = path.join(temporaryDirectory, "worker.mjs");
  const grandchildPath = path.join(temporaryDirectory, "grandchild.pid");
  const source = `#!/usr/bin/env node
import { spawn } from "node:child_process";
import { writeFile } from "node:fs/promises";

process.stdout.on("error", () => {});
async function startGrandchild() {
  const grandchild = spawn(
    process.execPath,
    ["-e", "setInterval(() => {}, 1000)"],
    { stdio: "ignore" },
  );
  await writeFile(${JSON.stringify(grandchildPath)}, String(grandchild.pid));
}

${body}
`;
  await writeFile(workerPath, source, "utf8");
  await chmod(workerPath, 0o755);
  return workerPath;
}

async function readPid(name: string): Promise<number> {
  return Number(await readFile(path.join(temporaryDirectory, name), "utf8"));
}
