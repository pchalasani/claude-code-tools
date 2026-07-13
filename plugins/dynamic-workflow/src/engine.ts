import { AsyncLocalStorage } from "node:async_hooks";
import vm from "node:vm";

import {
  CodexCleanupPendingError,
  CodexProcessError,
  CodexRunner,
  isCodexCleanupPendingError,
} from "./codex-runner.js";
import { StateStore } from "./state-store.js";
import type {
  AgentOptions,
  AgentStep,
  ParallelFunction,
  PipelineFunction,
  PipelineOptions,
  RunState,
  RunAuthorization,
  WorkflowApi,
} from "./types.js";
import {
  errorMessage,
  nowIso,
  processIdentityMatches,
  safeIdentifier,
  sha256,
  sleep,
  stableStringify,
  toJsonValue,
} from "./utils.js";

interface Scope {
  counters: Map<string, number>;
  name: string;
}

const DEFAULT_MAX_AGENT_INVOCATIONS = 100;
const DEFAULT_MAX_PIPELINE_ITEMS = 100;
const MAX_AGENT_RETRIES = 5;
const MAX_AGENT_TIMEOUT_MS = 86_400_000;

class CanceledError extends Error {
  constructor() {
    super("Workflow canceled");
    this.name = "CanceledError";
  }
}

class Semaphore {
  private active = 0;
  private readonly waiting: Array<() => void> = [];

  constructor(private readonly limit: number) {}

  async acquire(signal: AbortSignal): Promise<() => void> {
    if (signal.aborted) {
      throw signal.reason;
    }
    if (this.active >= this.limit) {
      await new Promise<void>((resolve, reject) => {
        const grant = (): void => {
          signal.removeEventListener("abort", abort);
          resolve();
        };
        const abort = (): void => {
          const index = this.waiting.indexOf(grant);
          if (index >= 0) {
            this.waiting.splice(index, 1);
          }
          reject(signal.reason);
        };
        signal.addEventListener("abort", abort, { once: true });
        this.waiting.push(grant);
      });
    }
    this.active += 1;
    let released = false;
    return () => {
      if (released) {
        return;
      }
      released = true;
      this.active -= 1;
      this.waiting.shift()?.();
    };
  }
}

export function compileWorkflow(source: string, filename: string): vm.Script {
  const metaPattern = /(^|\n)([\t ]*)export[\t ]+const[\t ]+meta[\t ]*=/m;
  const transformed = source.replace(metaPattern, "$1$2const meta =");
  return new vm.Script(`(async () => {\n${transformed}\n})()`, {
    filename,
  });
}

export class WorkflowEngine {
  private readonly abortController = new AbortController();
  private readonly agentCalls = new Set<Promise<unknown>>();
  private readonly codex: CodexRunner;
  private readonly scope = new AsyncLocalStorage<Scope>();
  private readonly semaphore: Semaphore;
  private activeAgents = 0;
  private readonly activeStepIds = new Set<string>();
  private monitoring = true;

  constructor(
    private readonly store: StateStore,
    private readonly source: string,
    private readonly emit: (message: string) => void,
  ) {
    this.semaphore = new Semaphore(store.snapshot().concurrency);
    this.codex = new CodexRunner(
      async (stepId, event) => {
        await this.store.appendEvent("codex", { stepId, event });
      },
      async (stepId, workerPid, workerStartedAt) => {
        await this.store.update((state) => {
          const step = state.steps[stepId];
          if (step?.status === "running") {
            step.workerPid = workerPid;
            step.workerStartedAt = workerStartedAt;
          }
        });
      },
    );
  }

  async run(): Promise<RunState> {
    const monitor = this.monitorControl();
    try {
      const control = await this.store.readControl();
      await this.store.update((state) => {
        if (control.authorization) {
          state.authorization = control.authorization;
        }
        state.status = "running";
        state.startedAt ??= nowIso();
        delete state.completedAt;
        delete state.error;
      });
      await this.log(`run ${this.store.runId} started`);
      const script = compileWorkflow(
        this.source,
        this.store.snapshot().workflowPath,
      );
      const result = await this.executeScript(script);
      if (this.agentCalls.size > 0) {
        const error = new Error(
          "Workflow returned while agent calls were still running; " +
            "await every agent(), pipeline(), and parallel() call",
        );
        this.abortController.abort(error);
        await this.drainAgentCalls();
        throw error;
      }
      await this.store.update((state) => {
        state.status = "completed";
        state.result = toJsonValue(result);
        state.completedAt = nowIso();
        delete state.cleanupPending;
        delete state.error;
      });
      await this.log(`run ${this.store.runId} completed`);
    } catch (error) {
      const cleanupError =
        isCodexCleanupPendingError(error)
          ? error
          : isCodexCleanupPendingError(this.abortController.signal.reason)
            ? this.abortController.signal.reason
            : undefined;
      const cleanupPending =
        cleanupError !== undefined ||
        this.store.snapshot().cleanupPending === true;
      const canceled =
        error instanceof CanceledError ||
        this.abortController.signal.reason instanceof CanceledError;
      if (!this.abortController.signal.aborted) {
        this.abortController.abort(canceled ? new CanceledError() : error);
      }
      await this.drainAgentCalls();
      await this.store.update((state) => {
        if (cleanupPending) {
          state.status = "canceling";
          state.cleanupPending = true;
          state.error = cleanupError?.message ?? errorMessage(error);
          delete state.completedAt;
        } else {
          state.status = canceled ? "canceled" : "failed";
          state.error = canceled ? "Workflow canceled" : errorMessage(error);
          state.completedAt = nowIso();
          delete state.cleanupPending;
        }
      });
      await this.log(
        cleanupPending
          ? `run ${this.store.runId} awaiting process cleanup: ` +
              (cleanupError?.message ?? errorMessage(error))
          : canceled
          ? `run ${this.store.runId} canceled`
          : `run ${this.store.runId} failed: ${errorMessage(error)}`,
      );
    } finally {
      this.monitoring = false;
      if (!this.abortController.signal.aborted) {
        this.abortController.abort(new CanceledError());
      }
      await this.drainAgentCalls();
      await monitor;
    }
    return this.store.snapshot();
  }

  private async executeScript(script: vm.Script): Promise<unknown> {
    const state = this.store.snapshot();
    const api: WorkflowApi = {
      agent: <T = string>(prompt: string, options: AgentOptions = {}) =>
        this.trackAgentCall(this.agent(prompt, options)) as Promise<T>,
      args: state.args,
      checkpoint: async () => await this.checkpoint(),
      log: async (...values: unknown[]) => {
        const message = values
          .map((value) =>
            typeof value === "string" ? value : stableStringify(value),
          )
          .join(" ");
        await this.log(message);
      },
      parallel: <T>(
        tasks: ReadonlyArray<() => Promise<T>>,
        options: { concurrency?: number; label?: string } = {},
      ) => this.trackAgentCall(this.parallel(tasks, options)),
      pipeline: <T, R>(
        items: readonly T[],
        worker: (item: T, index: number) => Promise<R>,
        options: PipelineOptions<T> = {},
      ) => this.trackAgentCall(this.pipeline(items, worker, options)),
      runId: state.runId,
    };
    const context = vm.createContext(
      {
        __workflowArgsJson:
          api.args === undefined ? undefined : JSON.stringify(api.args),
        __workflowBridge: {
          agent: api.agent,
          checkpoint: api.checkpoint,
          log: api.log,
          parallel: api.parallel,
          pipeline: api.pipeline,
        },
        __workflowRunId: api.runId,
      },
      {
        codeGeneration: { strings: false, wasm: false },
        name: `workflow-${state.runId}`,
      },
    );
    installWorkflowGlobals(context);
    return await this.scope.run(
      { counters: new Map(), name: "root" },
      async () => await (script.runInContext(context) as Promise<unknown>),
    );
  }

  private readonly pipeline: PipelineFunction = async <T, R>(
    items: readonly T[],
    worker: (item: T, index: number) => Promise<R>,
    options: PipelineOptions<T> = {},
  ): Promise<R[]> => {
    await this.checkpoint();
    const parent = this.currentScope();
    const pipelineNumber = this.nextCounter(parent, "pipeline");
    const pipelineName = safeIdentifier(
      options.label
        ? `pipeline-${pipelineNumber}-${options.label}`
        : `pipeline-${pipelineNumber}`,
    );
    const concurrency = this.validateConcurrency(
      options.concurrency ?? this.store.snapshot().concurrency,
    );
    const maxItems = options.maxItems ?? DEFAULT_MAX_PIPELINE_ITEMS;
    if (!Number.isInteger(maxItems) || maxItems < 0 || maxItems > 1_000) {
      throw new RangeError(
        "pipeline maxItems must be an integer from 0 to 1000",
      );
    }
    if (items.length > maxItems) {
      throw new RangeError(
        `Pipeline received ${items.length} items; maximum is ${maxItems}`,
      );
    }
    const results = new Array<R>(items.length);
    let cursor = 0;
    const keys = new Set<string>();

    const runNext = async (): Promise<void> => {
      while (cursor < items.length) {
        const index = cursor;
        cursor += 1;
        const item = items[index] as T;
        const key = safeIdentifier(options.key?.(item, index) ?? String(index));
        if (keys.has(key)) {
          throw new Error(`Duplicate pipeline key: ${key}`);
        }
        keys.add(key);
        await this.checkpoint();
        results[index] = await this.scope.run(
          {
            counters: new Map(),
            name: `${parent.name}/${pipelineName}/${key}`,
          },
          async () => await worker(item, index),
        );
      }
    };

    await Promise.all(
      Array.from(
        { length: Math.min(concurrency, items.length) },
        async () => await runNext(),
      ),
    );
    return results;
  };

  private readonly parallel: ParallelFunction = async <T>(
    tasks: ReadonlyArray<() => Promise<T>>,
    options: { concurrency?: number; label?: string } = {},
  ): Promise<T[]> => {
    return await this.pipeline(
      tasks,
      async (task) => await task(),
      {
        ...(options.concurrency === undefined
          ? {}
          : { concurrency: options.concurrency }),
        label: options.label ?? "parallel",
      },
    );
  };

  private async agent(prompt: string, options: AgentOptions): Promise<unknown> {
    if (typeof prompt !== "string" || prompt.trim() === "") {
      throw new TypeError("agent() requires a non-empty prompt string");
    }
    await this.checkpoint();
    await this.assertSandboxAuthorized(options);
    this.validateAgentLimits(options);
    const scope = this.currentScope();
    const sequence = this.nextCounter(scope, "agent");
    const localId = options.id ?? `agent-${sequence}`;
    const stepId = safeIdentifier(`${scope.name}/${localId}`);
    const label = options.label ?? stepId;
    const fingerprint = sha256(
      stableStringify({
        prompt,
        cwd: options.cwd ?? this.store.snapshot().cwd,
        model: options.model,
        reasoningEffort: options.reasoningEffort,
        resumeThreadId: options.resumeThreadId,
        sandbox: options.sandbox ?? "read-only",
        schema: options.schema,
        addDirs: options.addDirs,
        cacheKey: options.cacheKey,
        ignoreUserConfig: options.ignoreUserConfig ?? false,
      }),
    );
    const existing = this.store.snapshot().steps[stepId];
    if (
      existing?.status === "completed" &&
      existing.fingerprint === fingerprint
    ) {
      await this.log(`cache hit: ${label}`);
      await this.store.appendEvent("cache.hit", { stepId });
      return existing.result;
    }
    if (this.activeStepIds.has(stepId)) {
      throw new Error(`Concurrent agent calls share the step ID: ${stepId}`);
    }

    this.activeStepIds.add(stepId);
    let release: () => void;
    try {
      release = await this.semaphore.acquire(this.abortController.signal);
    } catch (error) {
      this.activeStepIds.delete(stepId);
      throw error;
    }
    let countedActive = false;
    try {
      await this.checkpoint();
      this.activeAgents += 1;
      countedActive = true;
      const retries = Math.max(0, Math.floor(options.retries ?? 0));
      for (let retry = 0; retry <= retries; retry += 1) {
        await this.reserveAgentInvocation();
        const attempt = (existing?.attempt ?? 0) + retry + 1;
        const step: AgentStep = {
          attempt,
          fingerprint,
          id: stepId,
          label,
          startedAt: nowIso(),
          status: "running",
        };
        await this.store.updateStep(step);
        await this.store.appendEvent("agent.started", {
          attempt,
          label,
          stepId,
        });
        await this.log(`agent started: ${label} (attempt ${attempt})`);
        try {
          const execution = await this.codex.run({
            defaultTimeoutMs:
              this.store.snapshot().defaultAgentTimeoutMs ?? 1_800_000,
            options,
            prompt,
            runDirectory: this.store.directory,
            signal: this.abortController.signal,
            stepId,
            workflowCwd: this.store.snapshot().cwd,
          });
          const result = execution.data ?? execution.text;
          step.status = "completed";
          step.result = toJsonValue(result);
          step.completedAt = nowIso();
          if (execution.threadId) {
            step.threadId = execution.threadId;
          }
          if (execution.usage) {
            step.usage = execution.usage;
          }
          await this.store.updateStep(step);
          await this.store.appendEvent("agent.completed", { stepId });
          await this.log(`agent completed: ${label}`);
          return result;
        } catch (error) {
          if (error instanceof CodexCleanupPendingError) {
            await this.store.update((state) => {
              const persisted = state.steps[stepId];
              if (persisted) {
                persisted.error = error.message;
                persisted.workerPid = error.workerPid;
                if (error.workerStartedAt !== undefined) {
                  persisted.workerStartedAt = error.workerStartedAt;
                }
              }
              state.cleanupPending = true;
              state.status = "canceling";
              state.error = error.message;
              delete state.completedAt;
            });
            await this.store.appendEvent("agent.cleanup_pending", {
              error: error.message,
              stepId,
              workerPid: error.workerPid,
            });
            throw error;
          }
          const abortReason = this.abortController.signal.reason;
          const canceled = abortReason instanceof CanceledError;
          step.status = canceled ? "canceled" : "failed";
          step.error = canceled
            ? "Workflow canceled"
            : errorMessage(
                this.abortController.signal.aborted ? abortReason : error,
              );
          step.completedAt = nowIso();
          if (error instanceof CodexProcessError) {
            if (error.threadId) {
              step.threadId = error.threadId;
            }
            if (error.usage) {
              step.usage = error.usage;
            }
          }
          await this.store.updateStep(step);
          await this.store.appendEvent("agent.failed", {
            error: step.error,
            stepId,
          });
          if (this.abortController.signal.aborted) {
            throw abortReason;
          }
          const canRetry =
            retry < retries &&
            (!(error instanceof CodexProcessError) || error.retryable);
          if (!canRetry) {
            throw error;
          }
          const delay = Math.min(30_000, 1_000 * 2 ** retry);
          await this.log(`retrying ${label} in ${delay} ms`);
          await sleep(delay, this.abortController.signal);
        }
      }
      throw new Error(`Agent failed without an error: ${label}`);
    } finally {
      if (countedActive) {
        this.activeAgents -= 1;
      }
      release();
      this.activeStepIds.delete(stepId);
    }
  }

  private async checkpoint(): Promise<void> {
    while (true) {
      const control = await this.store.readControl();
      if (control.command === "cancel" || this.abortController.signal.aborted) {
        this.abortController.abort(new CanceledError());
        throw new CanceledError();
      }
      if (control.command === "run") {
        const status = this.store.snapshot().status;
        if (status === "paused" || status === "pausing") {
          await this.store.update((state) => {
            state.status = "running";
          });
          await this.log("run resumed");
        }
        return;
      }
      const desired = this.activeAgents > 0 ? "pausing" : "paused";
      if (this.store.snapshot().status !== desired) {
        await this.store.update((state) => {
          state.status = desired;
        });
      }
      await sleep(250);
    }
  }

  private async monitorControl(): Promise<void> {
    while (this.monitoring) {
      const supervisorPid = this.store.snapshot().pid;
      const supervisorStartedAt = this.store.snapshot().pidStartedAt;
      if (
        supervisorPid !== undefined &&
        !processIdentityMatches(supervisorPid, supervisorStartedAt)
      ) {
        if (!this.abortController.signal.aborted) {
          this.abortController.abort(
            new Error(`Workflow supervisor PID ${supervisorPid} exited`),
          );
        }
        return;
      }
      const control = await this.store.readControl();
      if (control.command === "cancel") {
        if (isTerminal(this.store.snapshot().status)) {
          return;
        }
        if (!this.abortController.signal.aborted) {
          await this.store.update((state) => {
            if (!isTerminal(state.status)) {
              state.status = "canceling";
            }
          });
          this.abortController.abort(new CanceledError());
        }
        return;
      }
      if (control.command === "pause") {
        const desired = this.activeAgents > 0 ? "pausing" : "paused";
        const current = this.store.snapshot().status;
        if (!isTerminal(current) && current !== desired) {
          await this.store.update((state) => {
            if (!isTerminal(state.status)) {
              state.status = desired;
            }
          });
        }
      }
      await sleep(250);
    }
  }

  private currentScope(): Scope {
    return this.scope.getStore() ?? { counters: new Map(), name: "root" };
  }

  private nextCounter(scope: Scope, kind: string): number {
    const next = (scope.counters.get(kind) ?? 0) + 1;
    scope.counters.set(kind, next);
    return next;
  }

  private validateConcurrency(value: number): number {
    if (!Number.isInteger(value) || value < 1 || value > 64) {
      throw new RangeError("Concurrency must be an integer from 1 to 64");
    }
    return value;
  }

  private async reserveAgentInvocation(): Promise<void> {
    await this.store.update((state) => {
      const count = state.agentInvocations ?? 0;
      const maximum =
        state.maxAgentInvocations ?? DEFAULT_MAX_AGENT_INVOCATIONS;
      if (count >= maximum) {
        throw new Error(
          `Workflow exceeded its ${maximum}-agent safety limit`,
        );
      }
      state.agentInvocations = count + 1;
    });
  }

  private validateAgentLimits(options: AgentOptions): void {
    if (
      options.retries !== undefined &&
      (!Number.isInteger(options.retries) ||
        options.retries < 0 ||
        options.retries > MAX_AGENT_RETRIES)
    ) {
      throw new RangeError(
        `agent retries must be an integer from 0 to ${MAX_AGENT_RETRIES}`,
      );
    }
    if (
      options.timeoutMs !== undefined &&
      (!Number.isInteger(options.timeoutMs) ||
        options.timeoutMs < 1 ||
        options.timeoutMs > MAX_AGENT_TIMEOUT_MS)
    ) {
      throw new RangeError(
        "agent timeoutMs must be an integer from 1 to 86400000",
      );
    }
  }

  private trackAgentCall<T>(promise: Promise<T>): Promise<T> {
    let tracked: Promise<T>;
    tracked = promise.finally(() => {
      this.agentCalls.delete(tracked);
    });
    this.agentCalls.add(tracked);
    return tracked;
  }

  private async drainAgentCalls(): Promise<void> {
    while (this.agentCalls.size > 0) {
      await Promise.allSettled([...this.agentCalls]);
    }
  }

  private async assertSandboxAuthorized(options: AgentOptions): Promise<void> {
    const control = await this.store.readControl();
    const storedAuthorization = this.store.snapshot().authorization;
    const authorization =
      control.authorization ??
      storedAuthorization ??
      defaultAuthorization();
    if (
      control.authorization &&
      (storedAuthorization?.dangerFullAccess !==
        control.authorization.dangerFullAccess ||
        storedAuthorization?.workspaceWrite !==
          control.authorization.workspaceWrite ||
        storedAuthorization?.workflowHash !== control.authorization.workflowHash)
    ) {
      await this.store.update((state) => {
        state.authorization = control.authorization as RunAuthorization;
      });
    }
    const sandbox = options.sandbox ?? "read-only";
    if (options.resumeThreadId && !authorization.dangerFullAccess) {
      throw new Error(
        "resumeThreadId requires --allow-danger-full-access because the " +
          "existing thread sandbox cannot be verified",
      );
    }
    if (sandbox === "danger-full-access" && !authorization.dangerFullAccess) {
      throw new Error(
        "danger-full-access requires --allow-danger-full-access at launch",
      );
    }
    if (sandbox === "workspace-write" && !authorization.workspaceWrite) {
      throw new Error(
        "workspace-write requires --allow-workspace-write at launch",
      );
    }
    if (
      sandbox !== "read-only" &&
      authorization.workflowHash !== this.store.snapshot().workflowHash
    ) {
      throw new Error(
        "Write authorization does not match the current workflow source; " +
          "launch again with the required --allow-* flag",
      );
    }
  }

  private async log(message: string): Promise<void> {
    await this.store.appendLog(message);
    this.emit(message);
  }
}

function isTerminal(status: RunState["status"]): boolean {
  return status === "canceled" || status === "completed" || status === "failed";
}

function defaultAuthorization(): RunAuthorization {
  return {
    dangerFullAccess: false,
    workflowHash: "",
    workspaceWrite: false,
  };
}

function installWorkflowGlobals(context: vm.Context): void {
  const bootstrap = new vm.Script(`
    {
      const bridge = globalThis.__workflowBridge
      const clone = value => {
        if (value === undefined) return undefined
        return JSON.parse(JSON.stringify(value))
      }
      const call = async (fn, values) => {
        try {
          return clone(await fn(...values))
        } catch (error) {
          const message = error && error.message ? error.message : String(error)
          throw new Error(String(message))
        }
      }
      const start = (fn, values) => {
        const promise = call(fn, values)
        promise.catch(() => {})
        return promise
      }
      const define = (name, value) => Object.defineProperty(globalThis, name, {
        configurable: false,
        enumerable: true,
        value,
        writable: false,
      })
      define("agent", (...values) => start(bridge.agent, values))
      define("checkpoint", (...values) => start(bridge.checkpoint, values))
      define("log", (...values) => start(bridge.log, values))
      define("parallel", (...values) => start(bridge.parallel, values))
      define("pipeline", (...values) => start(bridge.pipeline, values))
      define(
        "args",
        globalThis.__workflowArgsJson === undefined
          ? undefined
          : JSON.parse(globalThis.__workflowArgsJson),
      )
      define("workflow", Object.freeze({ runId: globalThis.__workflowRunId }))
    }
    delete globalThis.__workflowArgsJson
    delete globalThis.__workflowBridge
    delete globalThis.__workflowRunId
  `);
  bootstrap.runInContext(context);
}
