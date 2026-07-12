import { spawn } from "node:child_process";
import { open, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { compileWorkflow, WorkflowEngine } from "./engine.js";
import { StateStore } from "./state-store.js";
import type {
  JsonValue,
  RunAuthorization,
  RunState,
  RunStatus,
} from "./types.js";
import {
  createRunId,
  errorMessage,
  isPidRunning,
  nowIso,
  processIdentityMatches,
  processStartIdentity,
  sha256,
  sleep,
} from "./utils.js";

interface ParsedArguments {
  flags: Set<string>;
  positionals: string[];
  values: Map<string, string>;
}

const BOOLEAN_OPTIONS = new Set([
  "allow-danger-full-access",
  "allow-workspace-write",
  "detach",
  "foreground",
  "help",
  "json",
]);
const TERMINAL_STATUSES = new Set<RunStatus>([
  "canceled",
  "completed",
  "failed",
]);
const DEFAULT_AGENT_TIMEOUT_MS = 1_800_000;
const DEFAULT_MAX_AGENT_INVOCATIONS = 100;
const DEFAULT_MAX_RUNTIME_MS = 14_400_000;
const FORCE_STOP_GRACE_MS = 2_000;

class CleanupPendingError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CleanupPendingError";
  }
}

function printHelp(): void {
  console.log(`codex-workflow - durable JavaScript workflows for Codex

Usage:
  codex-workflow run <file> [--input JSON|@FILE] [--cwd DIR]
                     [--concurrency N] [--detach] [--json]
                     [--max-agents N] [--max-runtime-ms N]
                     [--agent-timeout-ms N]
                     [--allow-workspace-write]
                     [--allow-danger-full-access]
  codex-workflow validate <file>
  codex-workflow status <run-id> [--json]
  codex-workflow list [--json]
  codex-workflow logs <run-id>
  codex-workflow wait <run-id> [--json]
  codex-workflow pause <run-id>
  codex-workflow resume <run-id> [--foreground] [--json]
                        [--allow-workspace-write]
                        [--allow-danger-full-access]
  codex-workflow cancel <run-id>

Environment:
  CODEX_WORKFLOW_HOME       State root (default: ~/.codex/workflows)
  CODEX_WORKFLOW_CODEX_BIN  Codex executable (default: codex)

Workflow scripts receive agent(), pipeline(), parallel(), checkpoint(), log(),
args, and workflow.runId. Workers default to the read-only Codex sandbox.`);
}

function parseArguments(args: string[]): ParsedArguments {
  const parsed: ParsedArguments = {
    flags: new Set(),
    positionals: [],
    values: new Map(),
  };
  for (let index = 0; index < args.length; index += 1) {
    const value = args[index] as string;
    if (!value.startsWith("--")) {
      parsed.positionals.push(value);
      continue;
    }
    const name = value.slice(2);
    if (BOOLEAN_OPTIONS.has(name)) {
      parsed.flags.add(name);
      continue;
    }
    const optionValue = args[index + 1];
    if (optionValue === undefined || optionValue.startsWith("--")) {
      throw new Error(`--${name} requires a value`);
    }
    parsed.values.set(name, optionValue);
    index += 1;
  }
  return parsed;
}

function assertOptions(
  parsed: ParsedArguments,
  allowedValues: string[],
  allowedFlags: string[] = [],
): void {
  for (const name of parsed.values.keys()) {
    if (!allowedValues.includes(name)) {
      throw new Error(`Unknown option: --${name}`);
    }
  }
  for (const name of parsed.flags) {
    if (!allowedFlags.includes(name) && name !== "help") {
      throw new Error(`Unknown flag: --${name}`);
    }
  }
}

function requirePositional(
  parsed: ParsedArguments,
  index: number,
  description: string,
): string {
  const value = parsed.positionals[index];
  if (value === undefined) {
    throw new Error(`Missing ${description}`);
  }
  return value;
}

async function parseInput(value: string | undefined): Promise<JsonValue | undefined> {
  if (value === undefined) {
    return undefined;
  }
  const source = value.startsWith("@")
    ? await readFile(path.resolve(value.slice(1)), "utf8")
    : value;
  return JSON.parse(source) as JsonValue;
}

function parseConcurrency(value: string | undefined): number {
  const concurrency = value === undefined ? 6 : Number(value);
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 64) {
    throw new Error("--concurrency must be an integer from 1 to 64");
  }
  return concurrency;
}

function parseIntegerOption(
  name: string,
  value: string | undefined,
  defaultValue: number,
  maximum: number,
): number {
  const parsed = value === undefined ? defaultValue : Number(value);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > maximum) {
    throw new Error(`--${name} must be an integer from 1 to ${maximum}`);
  }
  return parsed;
}

function authorizationFromFlags(
  parsed: ParsedArguments,
  workflowHash: string,
  current?: RunAuthorization,
): RunAuthorization {
  const changed =
    parsed.flags.has("allow-danger-full-access") ||
    parsed.flags.has("allow-workspace-write");
  if (!changed && current) {
    return current;
  }
  const dangerFullAccess = parsed.flags.has("allow-danger-full-access");
  return {
    dangerFullAccess,
    workflowHash,
    workspaceWrite:
      dangerFullAccess || parsed.flags.has("allow-workspace-write"),
  };
}

function summary(state: RunState): string {
  const completed = Object.values(state.steps).filter(
    (step) => step.status === "completed",
  ).length;
  const total = Object.keys(state.steps).length;
  return `${state.runId}  ${state.status}  ${completed}/${total} agents`;
}

function outputState(state: RunState, json: boolean): void {
  if (json) {
    console.log(JSON.stringify(state, null, 2));
    return;
  }
  console.log(summary(state));
  if (state.error) {
    console.log(`Error: ${state.error}`);
  }
  if (state.status === "completed" && state.result !== undefined) {
    console.log(JSON.stringify(state.result, null, 2));
  }
}

async function executeRun(runId: string, json: boolean): Promise<RunState> {
  const store = await StateStore.load(runId);
  return await executeClaimedRun(store, json);
}

async function executeClaimedRun(
  store: StateStore,
  json: boolean,
  requestedToken?: string,
): Promise<RunState> {
  let runnerToken: string;
  try {
    runnerToken = await store.claimRunner(process.pid, requestedToken);
  } catch (error) {
    if (requestedToken) {
      const latest = await StateStore.load(store.runId);
      const state = latest.snapshot();
      if (state.pid === process.pid && !TERMINAL_STATUSES.has(state.status)) {
        return await recordBootstrapFailure(latest, error, json);
      }
    }
    throw error;
  }
  try {
    const requestCancel = (): void => {
      void store.writeControl("cancel");
    };
    process.once("SIGINT", requestCancel);
    process.once("SIGTERM", requestCancel);
    try {
      const pidStartedAt = processStartIdentity(process.pid);
      if (pidStartedAt === undefined) {
        throw new Error(`Could not identify runner PID ${process.pid}`);
      }
      await store.update((state) => {
        state.pid = process.pid;
        state.pidStartedAt = pidStartedAt;
        state.runnerStartedAt = nowIso();
      });
      const state = await superviseEngine(store);
      if (json) {
        outputState(state, true);
      }
      return state;
    } finally {
      process.removeListener("SIGINT", requestCancel);
      process.removeListener("SIGTERM", requestCancel);
    }
  } catch (error) {
    if (error instanceof CleanupPendingError) {
      return await recordCleanupPending(store, error, json);
    }
    return await recordBootstrapFailure(store, error, json);
  } finally {
    await store.releaseRunner(runnerToken);
  }
}

async function executeEngine(store: StateStore): Promise<RunState> {
  try {
    const current = store.snapshot();
    const source = await readFile(current.workflowPath, "utf8");
    compileWorkflow(source, current.workflowPath);
    const currentHash = sha256(source);
    await store.snapshotWorkflow(source, currentHash);
    if (currentHash !== current.workflowHash) {
      await store.appendEvent("workflow.changed", {
        from: current.workflowHash,
        to: currentHash,
      });
      await store.update((state) => {
        state.workflowHash = currentHash;
      });
    }
    const engine = new WorkflowEngine(store, source, (message) => {
      console.error(`[${store.runId}] ${message}`);
    });
    return await engine.run();
  } catch (error) {
    return await recordBootstrapFailure(store, error, false);
  }
}

async function superviseEngine(store: StateStore): Promise<RunState> {
  const entry = fileURLToPath(import.meta.url);
  const child = spawn(process.execPath, [entry, "_engine", store.runId], {
    detached: process.platform !== "win32",
    env: process.env,
    stdio: ["ignore", "inherit", "inherit"],
  });
  if (child.pid === undefined) {
    throw new Error("Workflow engine did not receive a PID");
  }
  const enginePid = child.pid;
  const engineStartedAt = processStartIdentity(enginePid);
  if (engineStartedAt === undefined) {
    signalProcessTree(enginePid, "SIGKILL");
    throw new Error(`Could not identify workflow engine PID ${enginePid}`);
  }

  let childError: unknown;
  let closed = false;
  let exitCode: number | null = null;
  let exitSignal: NodeJS.Signals | null = null;
  const completion = new Promise<void>((resolve) => {
    child.once("error", (error) => {
      childError = error;
      closed = true;
      resolve();
    });
    child.once("close", (code, signal) => {
      exitCode = code;
      exitSignal = signal;
      closed = true;
      resolve();
    });
  });
  await store.update((state) => {
    state.enginePid = enginePid;
    state.engineStartedAt = engineStartedAt;
  });

  const maximumRuntime =
    store.snapshot().maxRuntimeMs ?? DEFAULT_MAX_RUNTIME_MS;
  const deadline = Date.now() + maximumRuntime;
  let forcedAt: number | undefined;
  let forcedReason: "cancel" | "timeout" | undefined;

  while (!closed) {
    const control = await store.readControl();
    if (Date.now() >= deadline && forcedReason === undefined) {
      forcedReason = "timeout";
      forcedAt = Date.now() + FORCE_STOP_GRACE_MS;
      await store.appendEvent("runner.deadline_exceeded", {
        maxRuntimeMs: maximumRuntime,
      });
      signalProcessTree(child.pid, "SIGTERM");
      await signalRecordedWorkers(store, "SIGKILL", false);
    } else if (control.command === "cancel" && forcedReason === undefined) {
      forcedReason = "cancel";
      forcedAt = Date.now() + FORCE_STOP_GRACE_MS;
    }
    if (forcedAt !== undefined && Date.now() >= forcedAt) {
      signalProcessTree(child.pid, "SIGKILL");
      await signalRecordedWorkers(store, "SIGKILL", false);
    }
    await Promise.race([completion, sleep(100)]);
  }
  await completion;

  await signalRecordedWorkers(store, "SIGKILL", false, true);

  let latestStore = await StateStore.load(store.runId);
  let state = latestStore.snapshot();
  if (
    forcedReason === "timeout" &&
    !TERMINAL_STATUSES.has(state.status)
  ) {
    state = await terminalizeForcedRun(
      latestStore,
      "failed",
      `Workflow exceeded its ${maximumRuntime} ms runtime limit`,
    );
  } else if (!TERMINAL_STATUSES.has(state.status)) {
    const canceled = forcedReason === "cancel";
    const detail = childError
      ? errorMessage(childError)
      : `exit ${String(exitCode)}${exitSignal ? ` (${exitSignal})` : ""}`;
    const failureMessage = state.error ?? `Workflow engine stopped: ${detail}`;
    state = await terminalizeForcedRun(
      latestStore,
      canceled ? "canceled" : "failed",
      canceled ? "Workflow canceled" : failureMessage,
    );
  }
  latestStore = await StateStore.load(store.runId);
  return await latestStore.update((current) => {
    delete current.enginePid;
    delete current.engineStartedAt;
    delete current.pid;
    delete current.pidStartedAt;
  });
}

async function terminalizeForcedRun(
  store: StateStore,
  status: "canceled" | "failed",
  message: string,
): Promise<RunState> {
  return await store.update((state) => {
    state.status = status;
    state.error = message;
    state.completedAt = nowIso();
    delete state.cleanupPending;
    delete state.enginePid;
    delete state.engineStartedAt;
    delete state.pid;
    delete state.pidStartedAt;
    for (const step of Object.values(state.steps)) {
      if (step.status === "running") {
        step.status = status === "canceled" ? "canceled" : "failed";
        step.error = message;
        step.completedAt = nowIso();
        delete step.workerPid;
        delete step.workerStartedAt;
      }
    }
  });
}

function signalProcessTree(pid: number, signal: NodeJS.Signals): void {
  try {
    process.kill(process.platform === "win32" ? pid : -pid, signal);
  } catch {
    // The owned process tree may already be gone.
  }
}

async function signalRecordedWorkers(
  store: StateStore,
  signal: NodeJS.Signals,
  strictIdentity = true,
  waitForExit = false,
): Promise<void> {
  const state = (await StateStore.load(store.runId)).snapshot();
  const signaledPids: number[] = [];
  for (const step of Object.values(state.steps)) {
    if (step.workerPid === undefined) {
      continue;
    }
    const signaled = signalOwnedProcessTree(
      step.workerPid,
      step.workerStartedAt,
      signal,
      `worker ${step.id}`,
      strictIdentity,
    );
    if (signaled) {
      signaledPids.push(step.workerPid);
    }
  }
  if (!waitForExit) {
    return;
  }
  for (const pid of signaledPids) {
    if (!(await waitForProcessTreeExit(pid))) {
      throw new CleanupPendingError(
        `Worker process group ${pid} did not terminate`,
      );
    }
  }
}

async function terminateOrphanedExecution(
  store: StateStore,
  message: string,
): Promise<StateStore> {
  let state = (await StateStore.load(store.runId)).snapshot();
  let engineSignaled = false;
  if (state.enginePid !== undefined) {
    engineSignaled = signalOwnedProcessTree(
      state.enginePid,
      state.engineStartedAt,
      "SIGKILL",
      "workflow engine",
    );
  }
  await signalRecordedWorkers(store, "SIGKILL", true, true);
  if (
    engineSignaled &&
    state.enginePid !== undefined &&
    !(await waitForProcessTreeExit(state.enginePid))
  ) {
    throw new CleanupPendingError(
      `Workflow engine process group ${state.enginePid} did not terminate`,
    );
  }

  const latest = await StateStore.load(store.runId);
  await latest.update((current) => {
    delete current.cleanupPending;
    delete current.enginePid;
    delete current.engineStartedAt;
    delete current.pid;
    delete current.pidStartedAt;
    for (const step of Object.values(current.steps)) {
      if (step.status === "running") {
        step.status = "failed";
        step.error = message;
        step.completedAt = nowIso();
      }
      delete step.workerPid;
      delete step.workerStartedAt;
    }
  });
  return await StateStore.load(store.runId);
}

function signalOwnedProcessTree(
  pid: number,
  expectedStartedAt: string | undefined,
  signal: NodeJS.Signals,
  label: string,
  strictIdentity = true,
): boolean {
  if (!isPidRunning(pid)) {
    if (processTreeIsRunning(pid)) {
      throw new CleanupPendingError(
        `${label} PID ${pid} exited while its process group remains`,
      );
    }
    return false;
  }
  const actualStartedAt = processStartIdentity(pid);
  if (
    expectedStartedAt === undefined || actualStartedAt === undefined
  ) {
    if (processTreeIsRunning(pid)) {
      throw new CleanupPendingError(
        `Could not verify ${label} PID ${pid} before process-group cleanup`,
      );
    }
    return false;
  }
  if (actualStartedAt !== expectedStartedAt) {
    if (strictIdentity) {
      throw new Error(
        `Refusing to signal ${label} PID ${pid}: process identity changed`,
      );
    }
    return false;
  }
  signalProcessTree(pid, signal);
  return true;
}

async function waitForProcessTreeExit(
  pid: number,
  timeoutMs = 2_000,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (processTreeIsRunning(pid) && Date.now() < deadline) {
    await sleep(25);
  }
  return !processTreeIsRunning(pid);
}

function processTreeIsRunning(pid: number): boolean {
  try {
    process.kill(process.platform === "win32" ? pid : -pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function waitForEngineRegistration(runId: string): Promise<StateStore> {
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    const store = await StateStore.load(runId);
    const state = store.snapshot();
    if (
      state.enginePid === process.pid &&
      processIdentityMatches(process.pid, state.engineStartedAt)
    ) {
      return store;
    }
    await sleep(25);
  }
  throw new Error(`Workflow engine ${process.pid} was not registered`);
}

async function spawnDetached(store: StateStore): Promise<number> {
  const entry = fileURLToPath(import.meta.url);
  const runnerToken = await store.claimRunner(process.pid);
  let handedOff = false;
  try {
    const runnerLog = await open(path.join(store.directory, "runner.log"), "a");
    try {
      const child = spawn(
        process.execPath,
        [entry, "_execute", store.runId, runnerToken],
        {
          detached: true,
          env: process.env,
          stdio: ["ignore", runnerLog.fd, runnerLog.fd],
        },
      );
      if (child.pid === undefined) {
        throw new Error("Detached runner did not receive a PID");
      }
      const pid = child.pid;
      const pidStartedAt = processStartIdentity(pid);
      if (pidStartedAt === undefined) {
        signalProcessTree(pid, "SIGKILL");
        throw new Error(`Could not identify detached runner PID ${pid}`);
      }
      child.unref();
      await store.update((state) => {
        state.pid = pid;
        state.pidStartedAt = pidStartedAt;
        state.status = "starting";
      });
      await store.transferRunner(runnerToken, pid);
      handedOff = true;
      return pid;
    } finally {
      await runnerLog.close();
    }
  } catch (error) {
    await recordBootstrapFailure(store, error, false);
    throw error;
  } finally {
    if (!handedOff) {
      await store.releaseRunner(runnerToken);
    }
  }
}

async function recordBootstrapFailure(
  store: StateStore,
  error: unknown,
  json: boolean,
): Promise<RunState> {
  const message = `Runner bootstrap failed: ${errorMessage(error)}`;
  const state = await store.update((current) => {
    if (!TERMINAL_STATUSES.has(current.status)) {
      current.status = "failed";
      current.error = message;
      current.completedAt = nowIso();
    }
  });
  await store.appendEvent("runner.bootstrap_failed", { error: message });
  await store.appendLog(message);
  if (json) {
    outputState(state, true);
  }
  return state;
}

async function recordCleanupPending(
  store: StateStore,
  error: CleanupPendingError,
  json: boolean,
): Promise<RunState> {
  const message =
    `Process cleanup is incomplete: ${error.message}. ` +
    "Retry cancel or resume to continue cleanup.";
  const state = await store.update((current) => {
    current.status = "canceling";
    current.cleanupPending = true;
    current.error = message;
    delete current.completedAt;
    delete current.pid;
    delete current.pidStartedAt;
    if (
      !processIdentityMatches(
        current.enginePid,
        current.engineStartedAt,
      )
    ) {
      delete current.enginePid;
      delete current.engineStartedAt;
    }
  });
  await store.appendEvent("runner.cleanup_pending", { error: message });
  await store.appendLog(message);
  if (json) {
    outputState(state, true);
  }
  return state;
}

async function runCommand(parsed: ParsedArguments): Promise<number> {
  assertOptions(
    parsed,
    [
      "agent-timeout-ms",
      "concurrency",
      "cwd",
      "input",
      "max-agents",
      "max-runtime-ms",
    ],
    [
      "allow-danger-full-access",
      "allow-workspace-write",
      "detach",
      "json",
    ],
  );
  const workflowPath = path.resolve(
    requirePositional(parsed, 0, "workflow file"),
  );
  if (parsed.positionals.length > 1) {
    throw new Error("run accepts exactly one workflow file");
  }
  const source = await readFile(workflowPath, "utf8");
  compileWorkflow(source, workflowPath);
  const cwd = path.resolve(parsed.values.get("cwd") ?? process.cwd());
  const input = await parseInput(parsed.values.get("input"));
  const timestamp = nowIso();
  const workflowHash = sha256(source);
  const state: RunState = {
    ...(input === undefined ? {} : { args: input }),
    agentInvocations: 0,
    authorization: authorizationFromFlags(parsed, workflowHash),
    concurrency: parseConcurrency(parsed.values.get("concurrency")),
    createdAt: timestamp,
    cwd,
    defaultAgentTimeoutMs: parseIntegerOption(
      "agent-timeout-ms",
      parsed.values.get("agent-timeout-ms"),
      DEFAULT_AGENT_TIMEOUT_MS,
      86_400_000,
    ),
    maxAgentInvocations: parseIntegerOption(
      "max-agents",
      parsed.values.get("max-agents"),
      DEFAULT_MAX_AGENT_INVOCATIONS,
      1_000,
    ),
    maxRuntimeMs: parseIntegerOption(
      "max-runtime-ms",
      parsed.values.get("max-runtime-ms"),
      DEFAULT_MAX_RUNTIME_MS,
      604_800_000,
    ),
    runId: createRunId(),
    status: "starting",
    steps: {},
    updatedAt: timestamp,
    version: 1,
    workflowHash,
    workflowPath,
  };
  const store = await StateStore.create(state);
  await store.appendEvent("run.created", { workflowPath });

  if (parsed.flags.has("detach")) {
    const pid = await spawnDetached(store);
    if (parsed.flags.has("json")) {
      console.log(JSON.stringify({ pid, runId: state.runId }));
    } else {
      console.log(`Started ${state.runId} as PID ${pid}`);
    }
    return 0;
  }

  const finalState = await executeRun(state.runId, parsed.flags.has("json"));
  if (!parsed.flags.has("json")) {
    outputState(finalState, false);
  }
  return finalState.status === "completed" ? 0 : 1;
}

async function validateCommand(parsed: ParsedArguments): Promise<number> {
  assertOptions(parsed, []);
  const workflowPath = path.resolve(
    requirePositional(parsed, 0, "workflow file"),
  );
  if (parsed.positionals.length > 1) {
    throw new Error("validate accepts exactly one workflow file");
  }
  compileWorkflow(await readFile(workflowPath, "utf8"), workflowPath);
  console.log(`${workflowPath}: valid`);
  return 0;
}

async function statusCommand(parsed: ParsedArguments): Promise<number> {
  assertOptions(parsed, [], ["json"]);
  const runId = requirePositional(parsed, 0, "run ID");
  outputState(
    (await StateStore.load(runId)).snapshot(),
    parsed.flags.has("json"),
  );
  return 0;
}

async function listCommand(parsed: ParsedArguments): Promise<number> {
  assertOptions(parsed, [], ["json"]);
  const states = await StateStore.list();
  if (parsed.flags.has("json")) {
    console.log(JSON.stringify(states, null, 2));
  } else if (states.length === 0) {
    console.log("No workflow runs found.");
  } else {
    for (const state of states) {
      console.log(summary(state));
    }
  }
  return 0;
}

async function logsCommand(parsed: ParsedArguments): Promise<number> {
  assertOptions(parsed, []);
  const store = await StateStore.load(
    requirePositional(parsed, 0, "run ID"),
  );
  process.stdout.write(await store.readLog());
  return 0;
}

async function controlCommand(
  parsed: ParsedArguments,
  command: "pause" | "cancel",
): Promise<number> {
  assertOptions(parsed, []);
  const runId = requirePositional(parsed, 0, "run ID");
  const store = await StateStore.load(runId);
  const state = store.snapshot();
  if (TERMINAL_STATUSES.has(state.status)) {
    throw new Error(`Run ${runId} is already ${state.status}`);
  }
  await store.writeControl(command);
  if (!processIdentityMatches(state.pid, state.pidStartedAt)) {
    if (command === "cancel") {
      const cleaned = await terminateOrphanedExecution(
        store,
        "Interrupted after the workflow supervisor exited",
      );
      await terminalizeForcedRun(cleaned, "canceled", "Workflow canceled");
    } else if (
      !processIdentityMatches(state.enginePid, state.engineStartedAt)
    ) {
      await store.update((current) => {
        current.status = "paused";
      });
    }
  }
  console.log(`${command === "pause" ? "Pausing" : "Canceling"} ${runId}`);
  return 0;
}

async function resumeCommand(parsed: ParsedArguments): Promise<number> {
  assertOptions(parsed, [], [
    "allow-danger-full-access",
    "allow-workspace-write",
    "foreground",
    "json",
  ]);
  const runId = requirePositional(parsed, 0, "run ID");
  let store = await StateStore.load(runId);
  let state = store.snapshot();
  if (state.status === "completed") {
    outputState(state, parsed.flags.has("json"));
    return 0;
  }
  const runnerAlive = processIdentityMatches(
    state.pid,
    state.pidStartedAt,
  );
  if (!runnerAlive) {
    const cleaned = await terminateOrphanedExecution(
      store,
      "Interrupted after the workflow supervisor exited",
    );
    store = cleaned;
    state = cleaned.snapshot();
  }
  const changingAuthorization =
    parsed.flags.has("allow-danger-full-access") ||
    parsed.flags.has("allow-workspace-write");
  const workflowHash =
    changingAuthorization && !runnerAlive
      ? sha256(await readFile(state.workflowPath, "utf8"))
      : state.workflowHash;
  const currentControl = await store.readControl();
  const authorization = authorizationFromFlags(
    parsed,
    workflowHash,
    currentControl.authorization ?? state.authorization,
  );
  await store.writeControl("run", authorization);
  if (runnerAlive) {
    if (parsed.flags.has("json")) {
      console.log(JSON.stringify({ pid: state.pid, resumed: true, runId }));
    } else {
      console.log(`Resumed ${runId} (PID ${state.pid})`);
    }
    return 0;
  }
  state = await store.update((current) => {
    current.authorization = authorization;
  });
  if (parsed.flags.has("foreground")) {
    const finalState = await executeRun(runId, parsed.flags.has("json"));
    if (!parsed.flags.has("json")) {
      outputState(finalState, false);
    }
    return finalState.status === "completed" ? 0 : 1;
  }
  const pid = await spawnDetached(store);
  if (parsed.flags.has("json")) {
    console.log(JSON.stringify({ pid, resumed: true, runId }));
  } else {
    console.log(`Resumed ${runId} as PID ${pid}`);
  }
  return 0;
}

async function waitCommand(parsed: ParsedArguments): Promise<number> {
  assertOptions(parsed, [], ["json"]);
  const runId = requirePositional(parsed, 0, "run ID");
  while (true) {
    const state = (await StateStore.load(runId)).snapshot();
    if (TERMINAL_STATUSES.has(state.status)) {
      outputState(state, parsed.flags.has("json"));
      return state.status === "completed" ? 0 : 1;
    }
    if (!processIdentityMatches(state.pid, state.pidStartedAt)) {
      throw new Error(
        `Run ${runId} has no active runner; use resume to continue it`,
      );
    }
    await sleep(500);
  }
}

async function main(): Promise<number> {
  const [command = "help", ...args] = process.argv.slice(2);
  if (command === "help" || command === "--help" || command === "-h") {
    printHelp();
    return 0;
  }
  if (command === "_execute") {
    const runId = args[0];
    if (!runId) {
      throw new Error("Internal runner requires a run ID");
    }
    const store = await StateStore.load(runId);
    const state = await executeClaimedRun(store, false, args[1]);
    return state.status === "completed" ? 0 : 1;
  }
  if (command === "_engine") {
    const runId = args[0];
    if (!runId) {
      throw new Error("Internal engine requires a run ID");
    }
    const state = await executeEngine(await waitForEngineRegistration(runId));
    return state.status === "completed" ? 0 : 1;
  }
  const parsed = parseArguments(args);
  if (parsed.flags.has("help")) {
    printHelp();
    return 0;
  }
  switch (command) {
    case "run":
      return await runCommand(parsed);
    case "validate":
      return await validateCommand(parsed);
    case "status":
      return await statusCommand(parsed);
    case "list":
      return await listCommand(parsed);
    case "logs":
      return await logsCommand(parsed);
    case "wait":
      return await waitCommand(parsed);
    case "pause":
      return await controlCommand(parsed, "pause");
    case "resume":
      return await resumeCommand(parsed);
    case "cancel":
      return await controlCommand(parsed, "cancel");
    default:
      throw new Error(`Unknown command: ${command}`);
  }
}

main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((error) => {
    console.error(`codex-workflow: ${errorMessage(error)}`);
    process.exitCode = 1;
  });
