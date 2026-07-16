import { randomUUID } from "node:crypto";
import {
  appendFile,
  mkdir,
  readdir,
  readFile,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import path from "node:path";

import type {
  AgentStep,
  RunAuthorization,
  RunControl,
  RunState,
} from "./types.js";
import {
  atomicWriteJson,
  boundedJsonStringify,
  errorMessage,
  fileExists,
  isPidRunning,
  nowIso,
  processIdentityMatches,
  processStartIdentity,
  readJson,
  sha256,
  sleep,
  workflowHome,
} from "./utils.js";

const MAX_EVENT_HISTORY_BYTES = 16 * 1024 * 1024;
const MAX_STATE_BYTES = 16 * 1024 * 1024;

export class StateStore {
  readonly directory: string;
  readonly eventsPath: string;
  readonly logPath: string;
  readonly runId: string;

  private state: RunState;
  private eventQueue: Promise<void> = Promise.resolve();
  private eventsSaturated = false;
  private queue: Promise<void> = Promise.resolve();

  private constructor(state: RunState) {
    this.state = state;
    this.runId = state.runId;
    this.directory = StateStore.runDirectory(state.runId);
    this.eventsPath = path.join(this.directory, "events.jsonl");
    this.logPath = path.join(this.directory, "workflow.log");
  }

  static runDirectory(runId: string): string {
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(runId)) {
      throw new Error(`Invalid workflow run ID: ${runId}`);
    }
    const runsDirectory = path.join(workflowHome(), "runs");
    const directory = path.resolve(runsDirectory, runId);
    if (path.dirname(directory) !== path.resolve(runsDirectory)) {
      throw new Error(`Workflow run ID escapes the state directory: ${runId}`);
    }
    return directory;
  }

  static statePath(runId: string): string {
    return path.join(StateStore.runDirectory(runId), "state.json");
  }

  static controlPath(runId: string): string {
    return path.join(StateStore.runDirectory(runId), "control.json");
  }

  static runnerLockDirectory(runId: string): string {
    return path.join(StateStore.runDirectory(runId), "runner.lock");
  }

  static runnerSnapshotPath(runId: string): string {
    return path.join(
      StateStore.runDirectory(runId),
      "runtime",
      "workflow.mjs",
    );
  }

  static async create(
    state: RunState,
    runnerSource?: string,
  ): Promise<StateStore> {
    synchronizeTerminalFingerprint(state);
    const store = new StateStore(state);
    await mkdir(store.directory, { mode: 0o700, recursive: true });
    if (runnerSource !== undefined) {
      const runnerHash = sha256(runnerSource);
      if (state.runnerHash !== undefined && state.runnerHash !== runnerHash) {
        throw new Error("Runner source does not match its durable hash");
      }
      state.runnerHash = runnerHash;
      await store.writeRunnerSnapshot(runnerSource, runnerHash);
    }
    await atomicWriteJson(
      StateStore.statePath(state.runId),
      state,
      MAX_STATE_BYTES,
    );
    await store.writeControl("run", state.authorization);
    return store;
  }

  static async load(runId: string): Promise<StateStore> {
    const state = await readJson<RunState>(StateStore.statePath(runId));
    return new StateStore(state);
  }

  static async list(): Promise<RunState[]> {
    const runsDirectory = path.join(workflowHome(), "runs");
    if (!(await fileExists(runsDirectory))) {
      return [];
    }
    const entries = await readdir(runsDirectory, { withFileTypes: true });
    const states = await Promise.all(
      entries
        .filter((entry) => entry.isDirectory())
        .map(async (entry) => {
          try {
            return await readJson<RunState>(
              StateStore.statePath(entry.name),
            );
          } catch {
            return undefined;
          }
        }),
    );
    return states
      .filter((state): state is RunState => state !== undefined)
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt));
  }

  snapshot(): RunState {
    return structuredClone(this.state);
  }

  async update(mutator: (state: RunState) => void): Promise<RunState> {
    let result = this.snapshot();
    const operation = this.queue.then(async () => {
      const nextState = structuredClone(this.state);
      mutator(nextState);
      synchronizeTerminalFingerprint(nextState, this.state);
      nextState.updatedAt = nowIso();
      await atomicWriteJson(
        StateStore.statePath(this.runId),
        nextState,
        MAX_STATE_BYTES,
      );
      this.state = nextState;
      result = structuredClone(nextState);
    });
    this.queue = operation.catch(() => {});
    await operation;
    return result;
  }

  async updateStep(step: AgentStep): Promise<void> {
    await this.update((state) => {
      state.steps[step.id] = structuredClone(step);
    });
  }

  async readControl(): Promise<RunControl> {
    try {
      return await readJson<RunControl>(StateStore.controlPath(this.runId));
    } catch {
      return { command: "run", updatedAt: nowIso() };
    }
  }

  async writeControl(
    command: RunControl["command"],
    authorization?: RunAuthorization,
  ): Promise<void> {
    const current = await this.readControl();
    const effectiveAuthorization = authorization ?? current.authorization;
    await atomicWriteJson(StateStore.controlPath(this.runId), {
      ...(effectiveAuthorization === undefined
        ? {}
        : { authorization: effectiveAuthorization }),
      command,
      updatedAt: nowIso(),
    } satisfies RunControl);
  }

  async claimRunner(pid: number, requestedToken?: string): Promise<string> {
    if (requestedToken) {
      return await this.acceptRunnerHandoff(pid, requestedToken);
    }
    const lockDirectory = StateStore.runnerLockDirectory(this.runId);
    const ownerPath = path.join(lockDirectory, "owner.json");
    const token = randomUUID();
    const pidStartedAt = processStartIdentity(pid);
    if (pidStartedAt === undefined) {
      throw new Error(`Could not identify runner PID ${pid}`);
    }
    const candidateDirectory = `${lockDirectory}.candidate-${token}`;
    await mkdir(candidateDirectory);
    await atomicWriteJson(path.join(candidateDirectory, "owner.json"), {
      pid,
      pidStartedAt,
      token,
      updatedAt: nowIso(),
    });

    try {
      for (let attempt = 0; attempt < 20; attempt += 1) {
        try {
          await rename(candidateDirectory, lockDirectory);
          return token;
        } catch (error) {
          if (!isLockContention(error)) {
            throw error;
          }
        }

        let owner: {
          pid: number;
          pidStartedAt?: string;
          token: string;
        };
        try {
          owner = await readJson<{
            pid: number;
            pidStartedAt?: string;
            token: string;
          }>(ownerPath);
        } catch {
          // A legacy empty lock can be replaced by the prepared directory.
          try {
            await rename(candidateDirectory, lockDirectory);
            return token;
          } catch (error) {
            if (!isLockContention(error)) {
              throw error;
            }
          }
          await sleep(25);
          continue;
        }
        const ownerAlive = owner.pidStartedAt
          ? processIdentityMatches(owner.pid, owner.pidStartedAt)
          : isPidRunning(owner.pid);
        if (ownerAlive) {
          throw new Error(
            `Run ${this.runId} is already claimed by PID ${owner.pid}`,
          );
        }

        const quarantine = `${lockDirectory}.stale-${sha256(owner.token).slice(
          0,
          16,
        )}`;
        try {
          await rename(lockDirectory, quarantine);
        } catch (error) {
          if (!isLockContention(error) && !isMissing(error)) {
            throw error;
          }
        }
        await sleep(10);
      }
      throw new Error(`Could not claim runner lock for ${this.runId}`);
    } finally {
      try {
        if (await fileExists(candidateDirectory)) {
          await rm(candidateDirectory, { force: true, recursive: true });
        }
      } catch {
        // Candidate cleanup is best effort after a successful atomic publish.
      }
    }
  }

  async transferRunner(token: string, pid: number): Promise<void> {
    const ownerPath = path.join(
      StateStore.runnerLockDirectory(this.runId),
      "owner.json",
    );
    const owner = await readJson<{ token: string }>(ownerPath);
    if (owner.token !== token) {
      throw new Error(`Runner lock for ${this.runId} changed during launch`);
    }
    const pidStartedAt = processStartIdentity(pid);
    if (pidStartedAt === undefined) {
      throw new Error(`Could not identify runner PID ${pid}`);
    }
    await atomicWriteJson(ownerPath, {
      pid,
      pidStartedAt,
      token,
      updatedAt: nowIso(),
    });
  }

  async releaseRunner(token: string): Promise<void> {
    const lockDirectory = StateStore.runnerLockDirectory(this.runId);
    try {
      const owner = await readJson<{ token: string }>(
        path.join(lockDirectory, "owner.json"),
      );
      if (owner.token === token) {
        await rm(lockDirectory, { force: true, recursive: true });
      }
    } catch {
      // A missing or replaced lock is not owned by this runner.
    }
  }

  async appendEvent(type: string, data: unknown = {}): Promise<void> {
    const operation = this.eventQueue.then(async () => {
      if (this.eventsSaturated) {
        return;
      }
      const event = boundedJsonStringify(
        { at: nowIso(), type, data },
        MAX_EVENT_HISTORY_BYTES - 1,
      );
      const record = `${event}\n`;
      const recordBytes = Buffer.byteLength(record, "utf8");
      const currentBytes = await this.eventFileBytes();
      if (recordBytes <= MAX_EVENT_HISTORY_BYTES - currentBytes) {
        await appendFile(this.eventsPath, record, "utf8");
        return;
      }

      const marker = `${boundedJsonStringify({
        at: nowIso(),
        type: "events.truncated",
        data: { maximumBytes: MAX_EVENT_HISTORY_BYTES },
      }, MAX_EVENT_HISTORY_BYTES - 1)}\n`;
      if (
        Buffer.byteLength(marker, "utf8") <=
        MAX_EVENT_HISTORY_BYTES - currentBytes
      ) {
        await appendFile(this.eventsPath, marker, "utf8");
      }
      this.eventsSaturated = true;
    });
    this.eventQueue = operation.catch(() => {});
    await operation;
  }

  private async eventFileBytes(): Promise<number> {
    try {
      return (await stat(this.eventsPath)).size;
    } catch (error) {
      if (errorCode(error) === "ENOENT") {
        return 0;
      }
      throw error;
    }
  }

  async appendLog(message: string): Promise<void> {
    await appendFile(this.logPath, `[${nowIso()}] ${message}\n`, "utf8");
  }

  async readLog(): Promise<string> {
    try {
      return await readFile(this.logPath, "utf8");
    } catch {
      return "";
    }
  }

  async ensureRunnerSnapshot(currentEntryPath: string): Promise<string> {
    const snapshotPath = StateStore.runnerSnapshotPath(this.runId);
    try {
      const source = await readFile(snapshotPath, "utf8");
      const hash = sha256(source);
      if (this.state.runnerHash !== undefined && this.state.runnerHash !== hash) {
        throw new Error(
          `Runner snapshot for ${this.runId} failed its integrity check`,
        );
      }
      if (this.state.runnerHash === undefined) {
        await this.update((state) => {
          state.runnerHash = hash;
        });
      }
      return snapshotPath;
    } catch (error) {
      if (errorCode(error) !== "ENOENT") {
        throw error;
      }
    }

    const source = await readFile(currentEntryPath, "utf8");
    const hash = sha256(source);
    if (this.state.runnerHash !== undefined && this.state.runnerHash !== hash) {
      throw new Error(
        `Runner snapshot for ${this.runId} is missing and the installed ` +
          "runner no longer matches it",
      );
    }
    await this.writeRunnerSnapshot(source, hash);
    if (this.state.runnerHash === undefined) {
      await this.update((state) => {
        state.runnerHash = hash;
      });
    }
    return snapshotPath;
  }

  private async writeRunnerSnapshot(
    source: string,
    expectedHash: string,
  ): Promise<void> {
    const directory = path.join(this.directory, "runtime");
    const snapshotPath = StateStore.runnerSnapshotPath(this.runId);
    await mkdir(directory, { mode: 0o700, recursive: true });
    try {
      await writeFile(snapshotPath, source, {
        encoding: "utf8",
        flag: "wx",
        mode: 0o600,
      });
    } catch (error) {
      if (errorCode(error) !== "EEXIST") {
        throw error;
      }
    }
    const persisted = await readFile(snapshotPath, "utf8");
    if (sha256(persisted) !== expectedHash) {
      throw new Error(
        `Runner snapshot for ${this.runId} does not match its source`,
      );
    }
  }

  async snapshotWorkflow(source: string, hash: string): Promise<void> {
    const directory = path.join(this.directory, "workflow-snapshots");
    const snapshotPath = path.join(directory, `${hash}.js`);
    await mkdir(directory, { recursive: true });
    try {
      await writeFile(snapshotPath, source, { encoding: "utf8", flag: "wx" });
    } catch (error) {
      if (errorCode(error) !== "EEXIST") {
        throw error;
      }
    }
  }

  private async acceptRunnerHandoff(
    pid: number,
    token: string,
  ): Promise<string> {
    const ownerPath = path.join(
      StateStore.runnerLockDirectory(this.runId),
      "owner.json",
    );
    for (let attempt = 0; attempt < 80; attempt += 1) {
      try {
        const owner = await readJson<{
          pid: number;
          pidStartedAt?: string;
          token: string;
        }>(ownerPath);
        if (owner.token !== token) {
          throw new Error(`Runner handoff token for ${this.runId} is invalid`);
        }
        if (
          owner.pid === pid &&
          (owner.pidStartedAt === undefined ||
            processIdentityMatches(pid, owner.pidStartedAt))
        ) {
          return token;
        }
      } catch (error) {
        if (attempt === 79) {
          throw new Error(
            `Runner handoff for ${this.runId} failed: ${errorMessage(error)}`,
          );
        }
      }
      await sleep(25);
    }
    throw new Error(`Runner handoff for ${this.runId} timed out`);
  }
}

export function terminalStateFingerprint(state: RunState): string {
  const terminalPayload = JSON.stringify([
    state.status,
    state.completedAt,
    state.error ?? null,
    state.result ?? null,
  ]);
  return sha256(terminalPayload);
}

function synchronizeTerminalFingerprint(
  state: RunState,
  previous?: RunState,
): void {
  if (
    (state.status === "canceled" ||
      state.status === "completed" ||
      state.status === "failed") &&
    state.completedAt !== undefined
  ) {
    const previousWasTerminal =
      previous !== undefined &&
      (previous.status === "canceled" ||
        previous.status === "completed" ||
        previous.status === "failed") &&
      previous.completedAt !== undefined;
    state.terminalFingerprint =
      previousWasTerminal && previous.terminalFingerprint !== undefined
        ? previous.terminalFingerprint
        : sha256(randomUUID());
    return;
  }
  delete state.terminalFingerprint;
}

function errorCode(error: unknown): string | undefined {
  if (error instanceof Error && "code" in error) {
    return (error as NodeJS.ErrnoException).code;
  }
  return undefined;
}

function isLockContention(error: unknown): boolean {
  return errorCode(error) === "EEXIST" || errorCode(error) === "ENOTEMPTY";
}

function isMissing(error: unknown): boolean {
  return errorCode(error) === "ENOENT";
}
