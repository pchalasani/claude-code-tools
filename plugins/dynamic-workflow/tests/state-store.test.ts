import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, expect, test } from "vitest";

import { StateStore } from "../src/state-store.js";
import type { RunState } from "../src/types.js";
import { isPidRunning, nowIso } from "../src/utils.js";

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

async function createStore(runId: string): Promise<StateStore> {
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
  return await StateStore.create(state);
}
