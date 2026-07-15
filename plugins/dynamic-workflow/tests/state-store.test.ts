import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, expect, test } from "vitest";

import { StateStore } from "../src/state-store.js";
import type { RunState } from "../src/types.js";
import {
  boundedJsonStringify,
  isPidRunning,
  nowIso,
  toJsonValue,
} from "../src/utils.js";

let temporaryDirectory: string;
let originalHome: string | undefined;

beforeEach(async () => {
  temporaryDirectory = await mkdtemp(path.join(tmpdir(), "workflow-store-"));
  originalHome = process.env.CODEX_WORKFLOW_HOME;
  process.env.CODEX_WORKFLOW_HOME = temporaryDirectory;
});

afterEach(async () => {
  if (originalHome === undefined) {
    delete process.env.CODEX_WORKFLOW_HOME;
  } else {
    process.env.CODEX_WORKFLOW_HOME = originalHome;
  }
  await rm(temporaryDirectory, { force: true, recursive: true });
});

test("allows only one runner claim and supports a detached handoff", async () => {
  const first = await createStore("claim-run");
  const second = await StateStore.load("claim-run");
  const claims = await Promise.allSettled([
    first.claimRunner(process.pid),
    second.claimRunner(process.pid),
  ]);
  const winners = claims.filter((result) => result.status === "fulfilled");
  const losers = claims.filter((result) => result.status === "rejected");
  expect(winners).toHaveLength(1);
  expect(losers).toHaveLength(1);

  const token = (winners[0] as PromiseFulfilledResult<string>).value;
  await first.transferRunner(token, process.pid);
  await expect(second.claimRunner(process.pid, token)).resolves.toBe(token);
  await second.releaseRunner(token);
});

test("recovers a stale owner without allowing split-brain claims", async () => {
  const initial = await createStore("stale-claim-run");
  const staleOwner = spawn(
    process.execPath,
    ["-e", "setInterval(() => {}, 1000)"],
    { stdio: "ignore" },
  );
  if (staleOwner.pid === undefined) {
    throw new Error("Stale owner test process did not receive a PID");
  }
  await initial.claimRunner(staleOwner.pid);
  staleOwner.kill("SIGKILL");
  await new Promise<void>((resolve) => staleOwner.once("exit", () => resolve()));
  expect(isPidRunning(staleOwner.pid)).toBe(false);
  const first = await StateStore.load("stale-claim-run");
  const second = await StateStore.load("stale-claim-run");

  const claims = await Promise.allSettled([
    first.claimRunner(process.pid),
    second.claimRunner(process.pid),
  ]);
  const winners = claims.filter((result) => result.status === "fulfilled");
  const losers = claims.filter((result) => result.status === "rejected");
  expect(winners).toHaveLength(1);
  expect(losers).toHaveLength(1);

  const token = (winners[0] as PromiseFulfilledResult<string>).value;
  await first.releaseRunner(token);
});

test("persists and verifies an immutable runner snapshot", async () => {
  const runnerSource = "#!/usr/bin/env node\nconsole.log('runner')\n";
  const store = await createStore("runner-snapshot", runnerSource);
  const snapshotPath = StateStore.runnerSnapshotPath(store.runId);
  expect(await readFile(snapshotPath, "utf8")).toBe(runnerSource);

  await writeFile(snapshotPath, "tampered\n", "utf8");
  await expect(
    store.ensureRunnerSnapshot(path.join(temporaryDirectory, "missing.mjs")),
  ).rejects.toThrow(/integrity check/);
});

test("rejects deeply nested results before pretty-state amplification", () => {
  let value: unknown = "leaf";
  for (let depth = 0; depth < 5_000; depth += 1) {
    value = [value];
  }

  expect(() => toJsonValue(value)).toThrow(/maximum depth of 128/);
});

test("bounds the actual representation returned by inherited toJSON", () => {
  const value = Object.create({
    toJSON: () => ({ payload: "x".repeat(200) }),
  }) as unknown;

  expect(() => boundedJsonStringify(value, 100)).toThrow(
    /100-byte durable limit/,
  );
});

test("bounds depth in the actual representation returned by toJSON", () => {
  let replacement: unknown = "leaf";
  for (let depth = 0; depth < 129; depth += 1) {
    replacement = [replacement];
  }
  const value = Object.create({
    toJSON: () => replacement,
  }) as unknown;

  expect(() => boundedJsonStringify(value, 1_000_000)).toThrow(
    /maximum depth of 128/,
  );
});

test("bounds nodes in compact output returned by toJSON", () => {
  const value = Object.create({
    toJSON: () => Array.from({ length: 250_001 }, () => 0),
  }) as unknown;

  expect(() => boundedJsonStringify(value, 1_000_000)).toThrow(
    /maximum node count of 250000/,
  );
});

test("reads changing getters only during the bounded serialization", () => {
  let reads = 0;
  const value = {
    get payload(): string {
      reads += 1;
      return reads === 1 ? "ok" : "x".repeat(200);
    },
  };

  const serialized = boundedJsonStringify(value, 100);

  expect(serialized).toBe('{"payload":"ok"}');
  expect(Buffer.byteLength(serialized, "utf8")).toBeLessThanOrEqual(100);
  expect(reads).toBe(1);
});

test("does not retain a state mutation when bounded persistence fails", async () => {
  const store = await createStore("transactional-state");
  const oversized = "x".repeat(17 * 1024 * 1024);

  await expect(
    store.update((state) => {
      state.error = oversized;
      state.status = "failed";
    }),
  ).rejects.toThrow(/durable limit/);
  expect(store.snapshot()).toMatchObject({
    status: "starting",
  });
  expect(store.snapshot().error).toBeUndefined();

  await store.update((state) => {
    state.error = "bounded diagnostic";
    state.status = "failed";
  });
  expect(store.snapshot().error).toBe("bounded diagnostic");
});

test("caps aggregate durable event history across workers and cache hits", async () => {
  const store = await createStore("bounded-events");
  const payload = "x".repeat(1024 * 1024);
  for (let index = 0; index < 20; index += 1) {
    await store.appendEvent(index % 2 === 0 ? "codex" : "cache.hit", {
      index,
      payload,
    });
  }

  expect((await stat(store.eventsPath)).size).toBeLessThanOrEqual(
    16 * 1024 * 1024,
  );
  expect(await readFile(store.eventsPath, "utf8")).toContain(
    '"type":"events.truncated"',
  );
});

async function createStore(
  runId: string,
  runnerSource?: string,
): Promise<StateStore> {
  const timestamp = nowIso();
  const state: RunState = {
    agentInvocations: 0,
    concurrency: 1,
    createdAt: timestamp,
    cwd: temporaryDirectory,
    runId,
    status: "starting",
    steps: {},
    updatedAt: timestamp,
    version: 1,
    workflowHash: "test",
    workflowPath: path.join(temporaryDirectory, "workflow.js"),
  };
  return await StateStore.create(state, runnerSource);
}
