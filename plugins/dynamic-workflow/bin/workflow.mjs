#!/usr/bin/env node

// src/cli.ts
import { spawn as spawn2 } from "node:child_process";
import { open, readFile as readFile3 } from "node:fs/promises";
import path4 from "node:path";
import { fileURLToPath } from "node:url";

// src/engine.ts
import { AsyncLocalStorage } from "node:async_hooks";
import vm from "node:vm";

// src/codex-runner.ts
import { spawn } from "node:child_process";
import { mkdir as mkdir2, writeFile as writeFile2 } from "node:fs/promises";
import path2 from "node:path";
import readline from "node:readline";

// src/utils.ts
import { createHash, randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { constants } from "node:fs";
import {
  access,
  mkdir,
  readFile,
  rename,
  writeFile
} from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
function nowIso() {
  return (/* @__PURE__ */ new Date()).toISOString();
}
function createRunId() {
  const stamp = (/* @__PURE__ */ new Date()).toISOString().replaceAll(/[-:.TZ]/g, "");
  return `${stamp}-${randomUUID().slice(0, 8)}`;
}
function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}
function stableStringify(value) {
  const seen = /* @__PURE__ */ new WeakSet();
  function normalize(item) {
    if (item === null || typeof item !== "object") {
      return item;
    }
    if (seen.has(item)) {
      throw new TypeError("Cannot serialize a circular value");
    }
    seen.add(item);
    if (Array.isArray(item)) {
      const normalized2 = item.map(normalize);
      seen.delete(item);
      return normalized2;
    }
    const normalized = Object.fromEntries(
      Object.entries(item).sort(([left], [right]) => left.localeCompare(right)).map(([key, child]) => [key, normalize(child)])
    );
    seen.delete(item);
    return normalized;
  }
  return JSON.stringify(normalize(value));
}
function toJsonValue(value) {
  if (value === void 0) {
    return null;
  }
  return JSON.parse(JSON.stringify(value));
}
function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}
async function sleep(milliseconds, signal) {
  if (signal?.aborted) {
    throw signal.reason;
  }
  await new Promise((resolve, reject) => {
    const finish = () => {
      signal?.removeEventListener("abort", abort);
      resolve();
    };
    const timer = setTimeout(finish, milliseconds);
    const abort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    signal?.addEventListener("abort", abort, { once: true });
  });
}
async function atomicWriteJson(filePath, value) {
  await mkdir(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}
`, "utf8");
  await rename(temporary, filePath);
}
async function readJson(filePath) {
  return JSON.parse(await readFile(filePath, "utf8"));
}
async function fileExists(filePath) {
  try {
    await access(filePath, constants.F_OK);
    return true;
  } catch {
    return false;
  }
}
function workflowHome() {
  const configured = process.env.CODEX_WORKFLOW_HOME;
  return path.resolve(configured ?? path.join(homedir(), ".codex", "workflows"));
}
function isPidRunning(pid) {
  if (pid === void 0) {
    return false;
  }
  try {
    process.kill(pid, 0);
    if (process.platform !== "win32") {
      const state = spawnSync("ps", ["-o", "stat=", "-p", String(pid)], {
        encoding: "utf8"
      });
      const processState = state.stdout.trim();
      if (state.status !== 0 || processState === "" || processState.startsWith("Z")) {
        return false;
      }
    }
    return true;
  } catch {
    return false;
  }
}
function processStartIdentity(pid) {
  if (!isPidRunning(pid)) {
    return void 0;
  }
  const result = process.platform === "win32" ? spawnSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      `(Get-Process -Id ${pid}).StartTime.ToUniversalTime().Ticks`
    ],
    { encoding: "utf8", windowsHide: true }
  ) : spawnSync("ps", ["-o", "lstart=", "-p", String(pid)], {
    encoding: "utf8"
  });
  if (result.status !== 0) {
    return void 0;
  }
  const identity = result.stdout.trim();
  return identity === "" ? void 0 : identity;
}
function processIdentityMatches(pid, expectedStartedAt) {
  return pid !== void 0 && expectedStartedAt !== void 0 && processStartIdentity(pid) === expectedStartedAt;
}
function safeIdentifier(value) {
  const cleaned = value.replaceAll(/[^A-Za-z0-9_.\/-]+/g, "-");
  return cleaned.replaceAll(/^[-/.]+|[-/.]+$/g, "") || "step";
}

// src/codex-runner.ts
var MAX_PROMPT_BYTES = 1e6;
var MAX_RESULT_BYTES = 1e6;
var TERMINATION_GRACE_MS = 2e3;
var CodexProcessError = class extends Error {
  retryable;
  threadId;
  usage;
  constructor(message, retryable = true, metadata = {}) {
    super(message);
    this.name = "CodexProcessError";
    this.retryable = retryable;
    this.threadId = metadata.threadId;
    this.usage = metadata.usage;
  }
};
var CodexCleanupPendingError = class extends CodexProcessError {
  cleanupPending = true;
  workerPid;
  workerStartedAt;
  constructor(message, workerPid, workerStartedAt, metadata = {}) {
    super(message, false, metadata);
    this.name = "CodexCleanupPendingError";
    this.workerPid = workerPid;
    this.workerStartedAt = workerStartedAt;
  }
};
function isCodexCleanupPendingError(error) {
  return typeof error === "object" && error !== null && "cleanupPending" in error && error.cleanupPending === true;
}
var CodexRunner = class {
  constructor(onEvent, onSpawn) {
    this.onEvent = onEvent;
    this.onSpawn = onSpawn;
  }
  async run(request) {
    const promptBytes = Buffer.byteLength(request.prompt, "utf8");
    if (promptBytes > MAX_PROMPT_BYTES) {
      throw new CodexProcessError(
        `Codex worker prompt is ${promptBytes} bytes; maximum is ${MAX_PROMPT_BYTES}. Chunk inputs or use a tree reduction.`,
        false
      );
    }
    const command = process.env.CODEX_WORKFLOW_CODEX_BIN ?? "codex";
    const args = await this.buildArgs(request);
    const cwd = path2.resolve(
      request.workflowCwd,
      request.options.cwd ?? "."
    );
    return await new Promise((resolve, reject) => {
      const child = spawn(command, args, {
        cwd,
        detached: process.platform !== "win32",
        env: process.env,
        stdio: ["pipe", "pipe", "pipe"]
      });
      let finalText = "";
      let workerStartedAt;
      let threadId;
      let usage;
      let standardError = "";
      let eventError = "";
      let timedOut = false;
      let settled = false;
      let terminationStarted = false;
      let terminationDeadline = 0;
      let killTimer;
      let persistenceError;
      let persistenceQueue = Promise.resolve();
      const persist = (operation) => {
        persistenceQueue = persistenceQueue.then(operation).catch((error) => {
          persistenceError ??= error;
          terminate();
        });
      };
      const terminate = () => {
        if (terminationStarted) {
          return;
        }
        terminationStarted = true;
        terminationDeadline = Date.now() + TERMINATION_GRACE_MS;
        signalProcessGroup(child, "SIGTERM");
        killTimer = setTimeout(
          () => signalProcessGroup(child, "SIGKILL"),
          TERMINATION_GRACE_MS
        );
        killTimer.unref();
      };
      const timeout = request.options.timeoutMs ?? request.defaultTimeoutMs;
      const timeoutTimer = setTimeout(() => {
        timedOut = true;
        terminate();
      }, timeout);
      timeoutTimer.unref();
      if (child.pid !== void 0 && this.onSpawn) {
        const spawnedWorkerPid = child.pid;
        const spawnedWorkerStartedAt = processStartIdentity(spawnedWorkerPid);
        workerStartedAt = spawnedWorkerStartedAt;
        if (spawnedWorkerStartedAt === void 0) {
          persistenceError = new Error(
            `Could not identify Codex worker PID ${spawnedWorkerPid}`
          );
          terminate();
        } else {
          persist(
            async () => await this.onSpawn?.(
              request.stepId,
              spawnedWorkerPid,
              spawnedWorkerStartedAt
            )
          );
        }
      }
      const abort = () => terminate();
      request.signal.addEventListener("abort", abort, { once: true });
      const lines = readline.createInterface({ input: child.stdout });
      lines.on("line", (line) => {
        const trimmed = line.trim();
        if (trimmed === "") {
          return;
        }
        try {
          const event = JSON.parse(trimmed);
          if (this.onEvent) {
            persist(async () => await this.onEvent?.(request.stepId, event));
          }
          if (event.type === "thread.started") {
            threadId = event.thread_id;
          } else if (event.type === "item.completed" && event.item?.type === "agent_message" && event.item.text) {
            finalText = event.item.text;
          } else if (event.type === "turn.completed" && event.usage) {
            usage = {
              ...event.usage.cached_input_tokens === void 0 ? {} : { cachedInputTokens: event.usage.cached_input_tokens },
              ...event.usage.input_tokens === void 0 ? {} : { inputTokens: event.usage.input_tokens },
              ...event.usage.output_tokens === void 0 ? {} : { outputTokens: event.usage.output_tokens }
            };
          } else if (event.type === "turn.failed" || event.type === "error") {
            eventError = event.error?.message ?? event.message ?? trimmed;
          }
        } catch {
          standardError += `${trimmed}
`;
          standardError = standardError.slice(-32e3);
        }
      });
      child.stderr.on("data", (chunk) => {
        standardError += chunk.toString("utf8");
        if (standardError.length > 32e3) {
          standardError = standardError.slice(-32e3);
        }
      });
      child.on("error", (error) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timeoutTimer);
        clearTimeout(killTimer);
        request.signal.removeEventListener("abort", abort);
        reject(
          new CodexProcessError(
            `Could not start ${command}: ${errorMessage(error)}`,
            false
          )
        );
      });
      child.on("close", (code, signal) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timeoutTimer);
        if (!terminationStarted) {
          clearTimeout(killTimer);
        }
        request.signal.removeEventListener("abort", abort);
        void (async () => {
          if (child.pid !== void 0 && !await finishProcessGroupTermination(
            child.pid,
            terminationStarted ? terminationDeadline : Date.now()
          )) {
            reject(
              new CodexCleanupPendingError(
                `Codex worker process group ${child.pid} did not terminate`,
                child.pid,
                workerStartedAt,
                { threadId, usage }
              )
            );
            return;
          }
          clearTimeout(killTimer);
          await persistenceQueue;
          if (persistenceError) {
            reject(
              new CodexProcessError(
                `Could not persist Codex worker events: ` + errorMessage(persistenceError),
                false
              )
            );
            return;
          }
          if (request.signal.aborted) {
            reject(request.signal.reason);
            return;
          }
          if (timedOut) {
            reject(
              new CodexProcessError(
                `Codex worker timed out after ${timeout} ms`,
                true,
                { threadId, usage }
              )
            );
            return;
          }
          const details = eventError || standardError.trim();
          if (code !== 0) {
            reject(
              processFailure(
                `Codex worker exited ${String(code)}${signal ? ` (${signal})` : ""}: ${details || "no error details"}`,
                threadId,
                usage
              )
            );
            return;
          }
          if (eventError) {
            reject(processFailure(eventError, threadId, usage));
            return;
          }
          if (finalText === "") {
            reject(
              new CodexProcessError(
                "Codex worker completed without an agent message"
              )
            );
            return;
          }
          const resultBytes = Buffer.byteLength(finalText, "utf8");
          if (resultBytes > MAX_RESULT_BYTES) {
            reject(
              new CodexProcessError(
                `Codex worker result is ${resultBytes} bytes; maximum is ${MAX_RESULT_BYTES}. Return compact structured output.`,
                false
              )
            );
            return;
          }
          let data;
          if (request.options.schema) {
            try {
              data = JSON.parse(finalText);
            } catch (error) {
              reject(
                new CodexProcessError(
                  `Structured Codex result was not JSON: ${errorMessage(error)}`,
                  false
                )
              );
              return;
            }
          }
          resolve({
            ...data === void 0 ? {} : { data },
            text: finalText,
            ...threadId === void 0 ? {} : { threadId },
            ...usage === void 0 ? {} : { usage }
          });
        })();
      });
      child.stdin.on("error", () => {
      });
      const workerRegistration = persistenceQueue;
      void (async () => {
        await workerRegistration;
        if (!settled && !terminationStarted && persistenceError === void 0) {
          child.stdin.end(request.prompt);
        }
      })();
    });
  }
  async buildArgs(request) {
    const options = request.options;
    const common = ["--json"];
    if (options.model) {
      common.push("--model", options.model);
    }
    if (options.reasoningEffort) {
      common.push(
        "--config",
        `model_reasoning_effort=${JSON.stringify(options.reasoningEffort)}`
      );
    }
    common.push("--config", 'approval_policy="never"');
    if (options.ignoreUserConfig) {
      common.push("--ignore-user-config");
    }
    common.push("--skip-git-repo-check");
    if (options.schema) {
      const schemaDirectory = path2.join(request.runDirectory, "schemas");
      await mkdir2(schemaDirectory, { recursive: true });
      const readableId = safeIdentifier(request.stepId).replaceAll("/", "-").slice(-80);
      const schemaPath = path2.join(
        schemaDirectory,
        `${readableId}-${sha256(request.stepId).slice(0, 12)}.json`
      );
      await writeFile2(
        schemaPath,
        `${JSON.stringify(options.schema, null, 2)}
`,
        "utf8"
      );
      common.push("--output-schema", schemaPath);
    }
    if (options.resumeThreadId) {
      return [
        "exec",
        "resume",
        ...common,
        options.resumeThreadId,
        "-"
      ];
    }
    const cwd = path2.resolve(request.workflowCwd, options.cwd ?? ".");
    const args = [
      "exec",
      ...common,
      "--sandbox",
      options.sandbox ?? "read-only",
      "--cd",
      cwd
    ];
    for (const directory of options.addDirs ?? []) {
      args.push("--add-dir", path2.resolve(cwd, directory));
    }
    args.push("-");
    return args;
  }
};
function signalProcessGroup(child, signal) {
  try {
    if (process.platform !== "win32" && child.pid !== void 0) {
      process.kill(-child.pid, signal);
    } else {
      child.kill(signal);
    }
  } catch {
  }
}
function processFailure(message, threadId, usage) {
  if (/context[_ ]length[_ ]exceeded|context (window|length)|maximum context|too many tokens|prompt.{0,20}too long/i.test(message)) {
    return new CodexProcessError(
      `Codex worker exceeded its context capacity. Chunk the input or use a tree reduction, then resume the workflow. Details: ${message}`,
      false,
      { threadId, usage }
    );
  }
  return new CodexProcessError(message, true, { threadId, usage });
}
async function finishProcessGroupTermination(pid, deadline) {
  while (processGroupIsRunning(pid) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  if (processGroupIsRunning(pid)) {
    signalProcessGroupByPid(pid, "SIGKILL");
  }
  const killDeadline = Date.now() + 1e3;
  while (processGroupIsRunning(pid) && Date.now() < killDeadline) {
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  return !processGroupIsRunning(pid);
}
function processGroupIsRunning(pid) {
  try {
    process.kill(process.platform === "win32" ? pid : -pid, 0);
    return true;
  } catch {
    return false;
  }
}
function signalProcessGroupByPid(pid, signal) {
  try {
    process.kill(process.platform === "win32" ? pid : -pid, signal);
  } catch {
  }
}

// src/engine.ts
var DEFAULT_MAX_AGENT_INVOCATIONS = 100;
var DEFAULT_MAX_PIPELINE_ITEMS = 100;
var MAX_AGENT_RETRIES = 5;
var MAX_AGENT_TIMEOUT_MS = 864e5;
var CanceledError = class extends Error {
  constructor() {
    super("Workflow canceled");
    this.name = "CanceledError";
  }
};
var Semaphore = class {
  constructor(limit) {
    this.limit = limit;
  }
  active = 0;
  waiting = [];
  async acquire(signal) {
    if (signal.aborted) {
      throw signal.reason;
    }
    if (this.active >= this.limit) {
      await new Promise((resolve, reject) => {
        const grant = () => {
          signal.removeEventListener("abort", abort);
          resolve();
        };
        const abort = () => {
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
};
function compileWorkflow(source, filename) {
  const metaPattern = /(^|\n)([\t ]*)export[\t ]+const[\t ]+meta[\t ]*=/m;
  const transformed = source.replace(metaPattern, "$1$2const meta =");
  return new vm.Script(`(async () => {
${transformed}
})()`, {
    filename
  });
}
var WorkflowEngine = class {
  constructor(store, source, emit) {
    this.store = store;
    this.source = source;
    this.emit = emit;
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
      }
    );
  }
  abortController = new AbortController();
  agentCalls = /* @__PURE__ */ new Set();
  codex;
  scope = new AsyncLocalStorage();
  semaphore;
  activeAgents = 0;
  activeStepIds = /* @__PURE__ */ new Set();
  monitoring = true;
  async run() {
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
        this.store.snapshot().workflowPath
      );
      const result = await this.executeScript(script);
      if (this.agentCalls.size > 0) {
        const error = new Error(
          "Workflow returned while agent calls were still running; await every agent(), pipeline(), and parallel() call"
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
      const cleanupError = isCodexCleanupPendingError(error) ? error : isCodexCleanupPendingError(this.abortController.signal.reason) ? this.abortController.signal.reason : void 0;
      const cleanupPending = cleanupError !== void 0 || this.store.snapshot().cleanupPending === true;
      const canceled = error instanceof CanceledError || this.abortController.signal.reason instanceof CanceledError;
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
        cleanupPending ? `run ${this.store.runId} awaiting process cleanup: ` + (cleanupError?.message ?? errorMessage(error)) : canceled ? `run ${this.store.runId} canceled` : `run ${this.store.runId} failed: ${errorMessage(error)}`
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
  async executeScript(script) {
    const state = this.store.snapshot();
    const api = {
      agent: (prompt, options = {}) => this.trackAgentCall(this.agent(prompt, options)),
      args: state.args,
      checkpoint: async () => await this.checkpoint(),
      log: async (...values) => {
        const message = values.map(
          (value) => typeof value === "string" ? value : stableStringify(value)
        ).join(" ");
        await this.log(message);
      },
      parallel: (tasks, options = {}) => this.trackAgentCall(this.parallel(tasks, options)),
      pipeline: (items, worker, options = {}) => this.trackAgentCall(this.pipeline(items, worker, options)),
      runId: state.runId
    };
    const context = vm.createContext(
      {
        __workflowArgsJson: api.args === void 0 ? void 0 : JSON.stringify(api.args),
        __workflowBridge: {
          agent: api.agent,
          checkpoint: api.checkpoint,
          log: api.log,
          parallel: api.parallel,
          pipeline: api.pipeline
        },
        __workflowRunId: api.runId
      },
      {
        codeGeneration: { strings: false, wasm: false },
        name: `workflow-${state.runId}`
      }
    );
    installWorkflowGlobals(context);
    return await this.scope.run(
      { counters: /* @__PURE__ */ new Map(), name: "root" },
      async () => await script.runInContext(context)
    );
  }
  pipeline = async (items, worker, options = {}) => {
    await this.checkpoint();
    const parent = this.currentScope();
    const pipelineNumber = this.nextCounter(parent, "pipeline");
    const pipelineName = safeIdentifier(
      options.label ? `pipeline-${pipelineNumber}-${options.label}` : `pipeline-${pipelineNumber}`
    );
    const concurrency = this.validateConcurrency(
      options.concurrency ?? this.store.snapshot().concurrency
    );
    const maxItems = options.maxItems ?? DEFAULT_MAX_PIPELINE_ITEMS;
    if (!Number.isInteger(maxItems) || maxItems < 0 || maxItems > 1e3) {
      throw new RangeError(
        "pipeline maxItems must be an integer from 0 to 1000"
      );
    }
    if (items.length > maxItems) {
      throw new RangeError(
        `Pipeline received ${items.length} items; maximum is ${maxItems}`
      );
    }
    const results = new Array(items.length);
    let cursor = 0;
    const keys = /* @__PURE__ */ new Set();
    const runNext = async () => {
      while (cursor < items.length) {
        const index = cursor;
        cursor += 1;
        const item = items[index];
        const key = safeIdentifier(options.key?.(item, index) ?? String(index));
        if (keys.has(key)) {
          throw new Error(`Duplicate pipeline key: ${key}`);
        }
        keys.add(key);
        await this.checkpoint();
        results[index] = await this.scope.run(
          {
            counters: /* @__PURE__ */ new Map(),
            name: `${parent.name}/${pipelineName}/${key}`
          },
          async () => await worker(item, index)
        );
      }
    };
    await Promise.all(
      Array.from(
        { length: Math.min(concurrency, items.length) },
        async () => await runNext()
      )
    );
    return results;
  };
  parallel = async (tasks, options = {}) => {
    return await this.pipeline(
      tasks,
      async (task) => await task(),
      {
        ...options.concurrency === void 0 ? {} : { concurrency: options.concurrency },
        label: options.label ?? "parallel"
      }
    );
  };
  async agent(prompt, options) {
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
        ignoreUserConfig: options.ignoreUserConfig ?? false
      })
    );
    const existing = this.store.snapshot().steps[stepId];
    if (existing?.status === "completed" && existing.fingerprint === fingerprint) {
      await this.log(`cache hit: ${label}`);
      await this.store.appendEvent("cache.hit", { stepId });
      return existing.result;
    }
    if (this.activeStepIds.has(stepId)) {
      throw new Error(`Concurrent agent calls share the step ID: ${stepId}`);
    }
    this.activeStepIds.add(stepId);
    let release;
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
        const step = {
          attempt,
          fingerprint,
          id: stepId,
          label,
          startedAt: nowIso(),
          status: "running"
        };
        await this.store.updateStep(step);
        await this.store.appendEvent("agent.started", {
          attempt,
          label,
          stepId
        });
        await this.log(`agent started: ${label} (attempt ${attempt})`);
        try {
          const execution = await this.codex.run({
            defaultTimeoutMs: this.store.snapshot().defaultAgentTimeoutMs ?? 18e5,
            options,
            prompt,
            runDirectory: this.store.directory,
            signal: this.abortController.signal,
            stepId,
            workflowCwd: this.store.snapshot().cwd
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
                if (error.workerStartedAt !== void 0) {
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
              workerPid: error.workerPid
            });
            throw error;
          }
          const abortReason = this.abortController.signal.reason;
          const canceled = abortReason instanceof CanceledError;
          step.status = canceled ? "canceled" : "failed";
          step.error = canceled ? "Workflow canceled" : errorMessage(
            this.abortController.signal.aborted ? abortReason : error
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
            stepId
          });
          if (this.abortController.signal.aborted) {
            throw abortReason;
          }
          const canRetry = retry < retries && (!(error instanceof CodexProcessError) || error.retryable);
          if (!canRetry) {
            throw error;
          }
          const delay = Math.min(3e4, 1e3 * 2 ** retry);
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
  async checkpoint() {
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
  async monitorControl() {
    while (this.monitoring) {
      const supervisorPid = this.store.snapshot().pid;
      const supervisorStartedAt = this.store.snapshot().pidStartedAt;
      if (supervisorPid !== void 0 && !processIdentityMatches(supervisorPid, supervisorStartedAt)) {
        if (!this.abortController.signal.aborted) {
          this.abortController.abort(
            new Error(`Workflow supervisor PID ${supervisorPid} exited`)
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
  currentScope() {
    return this.scope.getStore() ?? { counters: /* @__PURE__ */ new Map(), name: "root" };
  }
  nextCounter(scope, kind) {
    const next = (scope.counters.get(kind) ?? 0) + 1;
    scope.counters.set(kind, next);
    return next;
  }
  validateConcurrency(value) {
    if (!Number.isInteger(value) || value < 1 || value > 64) {
      throw new RangeError("Concurrency must be an integer from 1 to 64");
    }
    return value;
  }
  async reserveAgentInvocation() {
    await this.store.update((state) => {
      const count = state.agentInvocations ?? 0;
      const maximum = state.maxAgentInvocations ?? DEFAULT_MAX_AGENT_INVOCATIONS;
      if (count >= maximum) {
        throw new Error(
          `Workflow exceeded its ${maximum}-agent safety limit`
        );
      }
      state.agentInvocations = count + 1;
    });
  }
  validateAgentLimits(options) {
    if (options.retries !== void 0 && (!Number.isInteger(options.retries) || options.retries < 0 || options.retries > MAX_AGENT_RETRIES)) {
      throw new RangeError(
        `agent retries must be an integer from 0 to ${MAX_AGENT_RETRIES}`
      );
    }
    if (options.timeoutMs !== void 0 && (!Number.isInteger(options.timeoutMs) || options.timeoutMs < 1 || options.timeoutMs > MAX_AGENT_TIMEOUT_MS)) {
      throw new RangeError(
        "agent timeoutMs must be an integer from 1 to 86400000"
      );
    }
  }
  trackAgentCall(promise) {
    let tracked;
    tracked = promise.finally(() => {
      this.agentCalls.delete(tracked);
    });
    this.agentCalls.add(tracked);
    return tracked;
  }
  async drainAgentCalls() {
    while (this.agentCalls.size > 0) {
      await Promise.allSettled([...this.agentCalls]);
    }
  }
  async assertSandboxAuthorized(options) {
    const control = await this.store.readControl();
    const storedAuthorization = this.store.snapshot().authorization;
    const authorization = control.authorization ?? storedAuthorization ?? defaultAuthorization();
    if (control.authorization && (storedAuthorization?.dangerFullAccess !== control.authorization.dangerFullAccess || storedAuthorization?.workspaceWrite !== control.authorization.workspaceWrite || storedAuthorization?.workflowHash !== control.authorization.workflowHash)) {
      await this.store.update((state) => {
        state.authorization = control.authorization;
      });
    }
    const sandbox = options.sandbox ?? "read-only";
    if (options.resumeThreadId && !authorization.dangerFullAccess) {
      throw new Error(
        "resumeThreadId requires --allow-danger-full-access because the existing thread sandbox cannot be verified"
      );
    }
    if (sandbox === "danger-full-access" && !authorization.dangerFullAccess) {
      throw new Error(
        "danger-full-access requires --allow-danger-full-access at launch"
      );
    }
    if (sandbox === "workspace-write" && !authorization.workspaceWrite) {
      throw new Error(
        "workspace-write requires --allow-workspace-write at launch"
      );
    }
    if (sandbox !== "read-only" && authorization.workflowHash !== this.store.snapshot().workflowHash) {
      throw new Error(
        "Write authorization does not match the current workflow source; launch again with the required --allow-* flag"
      );
    }
  }
  async log(message) {
    await this.store.appendLog(message);
    this.emit(message);
  }
};
function isTerminal(status) {
  return status === "canceled" || status === "completed" || status === "failed";
}
function defaultAuthorization() {
  return {
    dangerFullAccess: false,
    workflowHash: "",
    workspaceWrite: false
  };
}
function installWorkflowGlobals(context) {
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

// src/state-store.ts
import { randomUUID as randomUUID2 } from "node:crypto";
import {
  appendFile,
  mkdir as mkdir3,
  readdir,
  readFile as readFile2,
  rename as rename2,
  rm,
  writeFile as writeFile3
} from "node:fs/promises";
import path3 from "node:path";
var StateStore = class _StateStore {
  directory;
  eventsPath;
  logPath;
  runId;
  state;
  queue = Promise.resolve();
  constructor(state) {
    this.state = state;
    this.runId = state.runId;
    this.directory = _StateStore.runDirectory(state.runId);
    this.eventsPath = path3.join(this.directory, "events.jsonl");
    this.logPath = path3.join(this.directory, "workflow.log");
  }
  static runDirectory(runId) {
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(runId)) {
      throw new Error(`Invalid workflow run ID: ${runId}`);
    }
    const runsDirectory = path3.join(workflowHome(), "runs");
    const directory = path3.resolve(runsDirectory, runId);
    if (path3.dirname(directory) !== path3.resolve(runsDirectory)) {
      throw new Error(`Workflow run ID escapes the state directory: ${runId}`);
    }
    return directory;
  }
  static statePath(runId) {
    return path3.join(_StateStore.runDirectory(runId), "state.json");
  }
  static controlPath(runId) {
    return path3.join(_StateStore.runDirectory(runId), "control.json");
  }
  static runnerLockDirectory(runId) {
    return path3.join(_StateStore.runDirectory(runId), "runner.lock");
  }
  static async create(state) {
    const store = new _StateStore(state);
    await mkdir3(store.directory, { recursive: true });
    await atomicWriteJson(_StateStore.statePath(state.runId), state);
    await store.writeControl("run", state.authorization);
    return store;
  }
  static async load(runId) {
    const state = await readJson(_StateStore.statePath(runId));
    return new _StateStore(state);
  }
  static async list() {
    const runsDirectory = path3.join(workflowHome(), "runs");
    if (!await fileExists(runsDirectory)) {
      return [];
    }
    const entries = await readdir(runsDirectory, { withFileTypes: true });
    const states = await Promise.all(
      entries.filter((entry) => entry.isDirectory()).map(async (entry) => {
        try {
          return await readJson(
            _StateStore.statePath(entry.name)
          );
        } catch {
          return void 0;
        }
      })
    );
    return states.filter((state) => state !== void 0).sort((left, right) => right.createdAt.localeCompare(left.createdAt));
  }
  snapshot() {
    return structuredClone(this.state);
  }
  async update(mutator) {
    let result = this.snapshot();
    const operation = this.queue.then(async () => {
      mutator(this.state);
      this.state.updatedAt = nowIso();
      await atomicWriteJson(_StateStore.statePath(this.runId), this.state);
      result = this.snapshot();
    });
    this.queue = operation.catch(() => {
    });
    await operation;
    return result;
  }
  async updateStep(step) {
    await this.update((state) => {
      state.steps[step.id] = structuredClone(step);
    });
  }
  async readControl() {
    try {
      return await readJson(_StateStore.controlPath(this.runId));
    } catch {
      return { command: "run", updatedAt: nowIso() };
    }
  }
  async writeControl(command, authorization) {
    const current = await this.readControl();
    const effectiveAuthorization = authorization ?? current.authorization;
    await atomicWriteJson(_StateStore.controlPath(this.runId), {
      ...effectiveAuthorization === void 0 ? {} : { authorization: effectiveAuthorization },
      command,
      updatedAt: nowIso()
    });
  }
  async claimRunner(pid, requestedToken) {
    if (requestedToken) {
      return await this.acceptRunnerHandoff(pid, requestedToken);
    }
    const lockDirectory = _StateStore.runnerLockDirectory(this.runId);
    const ownerPath = path3.join(lockDirectory, "owner.json");
    const token = randomUUID2();
    const pidStartedAt = processStartIdentity(pid);
    if (pidStartedAt === void 0) {
      throw new Error(`Could not identify runner PID ${pid}`);
    }
    const candidateDirectory = `${lockDirectory}.candidate-${token}`;
    await mkdir3(candidateDirectory);
    await atomicWriteJson(path3.join(candidateDirectory, "owner.json"), {
      pid,
      pidStartedAt,
      token,
      updatedAt: nowIso()
    });
    try {
      for (let attempt = 0; attempt < 20; attempt += 1) {
        try {
          await rename2(candidateDirectory, lockDirectory);
          return token;
        } catch (error) {
          if (!isLockContention(error)) {
            throw error;
          }
        }
        let owner;
        try {
          owner = await readJson(ownerPath);
        } catch {
          try {
            await rename2(candidateDirectory, lockDirectory);
            return token;
          } catch (error) {
            if (!isLockContention(error)) {
              throw error;
            }
          }
          await sleep(25);
          continue;
        }
        const ownerAlive = owner.pidStartedAt ? processIdentityMatches(owner.pid, owner.pidStartedAt) : isPidRunning(owner.pid);
        if (ownerAlive) {
          throw new Error(
            `Run ${this.runId} is already claimed by PID ${owner.pid}`
          );
        }
        const quarantine = `${lockDirectory}.stale-${sha256(owner.token).slice(
          0,
          16
        )}`;
        try {
          await rename2(lockDirectory, quarantine);
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
      }
    }
  }
  async transferRunner(token, pid) {
    const ownerPath = path3.join(
      _StateStore.runnerLockDirectory(this.runId),
      "owner.json"
    );
    const owner = await readJson(ownerPath);
    if (owner.token !== token) {
      throw new Error(`Runner lock for ${this.runId} changed during launch`);
    }
    const pidStartedAt = processStartIdentity(pid);
    if (pidStartedAt === void 0) {
      throw new Error(`Could not identify runner PID ${pid}`);
    }
    await atomicWriteJson(ownerPath, {
      pid,
      pidStartedAt,
      token,
      updatedAt: nowIso()
    });
  }
  async releaseRunner(token) {
    const lockDirectory = _StateStore.runnerLockDirectory(this.runId);
    try {
      const owner = await readJson(
        path3.join(lockDirectory, "owner.json")
      );
      if (owner.token === token) {
        await rm(lockDirectory, { force: true, recursive: true });
      }
    } catch {
    }
  }
  async appendEvent(type, data = {}) {
    await appendFile(
      this.eventsPath,
      `${JSON.stringify({ at: nowIso(), type, data })}
`,
      "utf8"
    );
  }
  async appendLog(message) {
    await appendFile(this.logPath, `[${nowIso()}] ${message}
`, "utf8");
  }
  async readLog() {
    try {
      return await readFile2(this.logPath, "utf8");
    } catch {
      return "";
    }
  }
  async snapshotWorkflow(source, hash) {
    const directory = path3.join(this.directory, "workflow-snapshots");
    const snapshotPath = path3.join(directory, `${hash}.js`);
    await mkdir3(directory, { recursive: true });
    try {
      await writeFile3(snapshotPath, source, { encoding: "utf8", flag: "wx" });
    } catch (error) {
      if (errorCode(error) !== "EEXIST") {
        throw error;
      }
    }
  }
  async acceptRunnerHandoff(pid, token) {
    const ownerPath = path3.join(
      _StateStore.runnerLockDirectory(this.runId),
      "owner.json"
    );
    for (let attempt = 0; attempt < 80; attempt += 1) {
      try {
        const owner = await readJson(ownerPath);
        if (owner.token !== token) {
          throw new Error(`Runner handoff token for ${this.runId} is invalid`);
        }
        if (owner.pid === pid && (owner.pidStartedAt === void 0 || processIdentityMatches(pid, owner.pidStartedAt))) {
          return token;
        }
      } catch (error) {
        if (attempt === 79) {
          throw new Error(
            `Runner handoff for ${this.runId} failed: ${errorMessage(error)}`
          );
        }
      }
      await sleep(25);
    }
    throw new Error(`Runner handoff for ${this.runId} timed out`);
  }
};
function errorCode(error) {
  if (error instanceof Error && "code" in error) {
    return error.code;
  }
  return void 0;
}
function isLockContention(error) {
  return errorCode(error) === "EEXIST" || errorCode(error) === "ENOTEMPTY";
}
function isMissing(error) {
  return errorCode(error) === "ENOENT";
}

// src/cli.ts
var BOOLEAN_OPTIONS = /* @__PURE__ */ new Set([
  "allow-danger-full-access",
  "allow-workspace-write",
  "detach",
  "foreground",
  "help",
  "json"
]);
var TERMINAL_STATUSES = /* @__PURE__ */ new Set([
  "canceled",
  "completed",
  "failed"
]);
var DEFAULT_AGENT_TIMEOUT_MS = 18e5;
var DEFAULT_MAX_AGENT_INVOCATIONS2 = 100;
var DEFAULT_MAX_RUNTIME_MS = 144e5;
var FORCE_STOP_GRACE_MS = 2e3;
var CleanupPendingError = class extends Error {
  constructor(message) {
    super(message);
    this.name = "CleanupPendingError";
  }
};
function printHelp() {
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
function parseArguments(args) {
  const parsed = {
    flags: /* @__PURE__ */ new Set(),
    positionals: [],
    values: /* @__PURE__ */ new Map()
  };
  for (let index = 0; index < args.length; index += 1) {
    const value = args[index];
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
    if (optionValue === void 0 || optionValue.startsWith("--")) {
      throw new Error(`--${name} requires a value`);
    }
    parsed.values.set(name, optionValue);
    index += 1;
  }
  return parsed;
}
function assertOptions(parsed, allowedValues, allowedFlags = []) {
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
function requirePositional(parsed, index, description) {
  const value = parsed.positionals[index];
  if (value === void 0) {
    throw new Error(`Missing ${description}`);
  }
  return value;
}
async function parseInput(value) {
  if (value === void 0) {
    return void 0;
  }
  const source = value.startsWith("@") ? await readFile3(path4.resolve(value.slice(1)), "utf8") : value;
  return JSON.parse(source);
}
function parseConcurrency(value) {
  const concurrency = value === void 0 ? 6 : Number(value);
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 64) {
    throw new Error("--concurrency must be an integer from 1 to 64");
  }
  return concurrency;
}
function parseIntegerOption(name, value, defaultValue, maximum) {
  const parsed = value === void 0 ? defaultValue : Number(value);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > maximum) {
    throw new Error(`--${name} must be an integer from 1 to ${maximum}`);
  }
  return parsed;
}
function authorizationFromFlags(parsed, workflowHash, current) {
  const changed = parsed.flags.has("allow-danger-full-access") || parsed.flags.has("allow-workspace-write");
  if (!changed && current) {
    return current;
  }
  const dangerFullAccess = parsed.flags.has("allow-danger-full-access");
  return {
    dangerFullAccess,
    workflowHash,
    workspaceWrite: dangerFullAccess || parsed.flags.has("allow-workspace-write")
  };
}
function summary(state) {
  const completed = Object.values(state.steps).filter(
    (step) => step.status === "completed"
  ).length;
  const total = Object.keys(state.steps).length;
  return `${state.runId}  ${state.status}  ${completed}/${total} agents`;
}
function outputState(state, json) {
  if (json) {
    console.log(JSON.stringify(state, null, 2));
    return;
  }
  console.log(summary(state));
  if (state.error) {
    console.log(`Error: ${state.error}`);
  }
  if (state.status === "completed" && state.result !== void 0) {
    console.log(JSON.stringify(state.result, null, 2));
  }
}
async function executeRun(runId, json) {
  const store = await StateStore.load(runId);
  return await executeClaimedRun(store, json);
}
async function executeClaimedRun(store, json, requestedToken) {
  let runnerToken;
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
    const requestCancel = () => {
      void store.writeControl("cancel");
    };
    process.once("SIGINT", requestCancel);
    process.once("SIGTERM", requestCancel);
    try {
      const pidStartedAt = processStartIdentity(process.pid);
      if (pidStartedAt === void 0) {
        throw new Error(`Could not identify runner PID ${process.pid}`);
      }
      await store.update((state2) => {
        state2.pid = process.pid;
        state2.pidStartedAt = pidStartedAt;
        state2.runnerStartedAt = nowIso();
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
async function executeEngine(store) {
  try {
    const current = store.snapshot();
    const source = await readFile3(current.workflowPath, "utf8");
    compileWorkflow(source, current.workflowPath);
    const currentHash = sha256(source);
    await store.snapshotWorkflow(source, currentHash);
    if (currentHash !== current.workflowHash) {
      await store.appendEvent("workflow.changed", {
        from: current.workflowHash,
        to: currentHash
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
async function superviseEngine(store) {
  const entry = fileURLToPath(import.meta.url);
  const child = spawn2(process.execPath, [entry, "_engine", store.runId], {
    detached: process.platform !== "win32",
    env: process.env,
    stdio: ["ignore", "inherit", "inherit"]
  });
  if (child.pid === void 0) {
    throw new Error("Workflow engine did not receive a PID");
  }
  const enginePid = child.pid;
  const engineStartedAt = processStartIdentity(enginePid);
  if (engineStartedAt === void 0) {
    signalProcessTree(enginePid, "SIGKILL");
    throw new Error(`Could not identify workflow engine PID ${enginePid}`);
  }
  let childError;
  let closed = false;
  let exitCode = null;
  let exitSignal = null;
  const completion = new Promise((resolve) => {
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
  await store.update((state2) => {
    state2.enginePid = enginePid;
    state2.engineStartedAt = engineStartedAt;
  });
  const maximumRuntime = store.snapshot().maxRuntimeMs ?? DEFAULT_MAX_RUNTIME_MS;
  const deadline = Date.now() + maximumRuntime;
  let forcedAt;
  let forcedReason;
  while (!closed) {
    const control = await store.readControl();
    if (Date.now() >= deadline && forcedReason === void 0) {
      forcedReason = "timeout";
      forcedAt = Date.now() + FORCE_STOP_GRACE_MS;
      await store.appendEvent("runner.deadline_exceeded", {
        maxRuntimeMs: maximumRuntime
      });
      signalProcessTree(child.pid, "SIGTERM");
      await signalRecordedWorkers(store, "SIGKILL", false);
    } else if (control.command === "cancel" && forcedReason === void 0) {
      forcedReason = "cancel";
      forcedAt = Date.now() + FORCE_STOP_GRACE_MS;
    }
    if (forcedAt !== void 0 && Date.now() >= forcedAt) {
      signalProcessTree(child.pid, "SIGKILL");
      await signalRecordedWorkers(store, "SIGKILL", false);
    }
    await Promise.race([completion, sleep(100)]);
  }
  await completion;
  await signalRecordedWorkers(store, "SIGKILL", false, true);
  let latestStore = await StateStore.load(store.runId);
  let state = latestStore.snapshot();
  if (forcedReason === "timeout" && !TERMINAL_STATUSES.has(state.status)) {
    state = await terminalizeForcedRun(
      latestStore,
      "failed",
      `Workflow exceeded its ${maximumRuntime} ms runtime limit`
    );
  } else if (!TERMINAL_STATUSES.has(state.status)) {
    const canceled = forcedReason === "cancel";
    const detail = childError ? errorMessage(childError) : `exit ${String(exitCode)}${exitSignal ? ` (${exitSignal})` : ""}`;
    const failureMessage = state.error ?? `Workflow engine stopped: ${detail}`;
    state = await terminalizeForcedRun(
      latestStore,
      canceled ? "canceled" : "failed",
      canceled ? "Workflow canceled" : failureMessage
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
async function terminalizeForcedRun(store, status, message) {
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
function signalProcessTree(pid, signal) {
  try {
    process.kill(process.platform === "win32" ? pid : -pid, signal);
  } catch {
  }
}
async function signalRecordedWorkers(store, signal, strictIdentity = true, waitForExit = false) {
  const state = (await StateStore.load(store.runId)).snapshot();
  const signaledPids = [];
  for (const step of Object.values(state.steps)) {
    if (step.workerPid === void 0) {
      continue;
    }
    const signaled = signalOwnedProcessTree(
      step.workerPid,
      step.workerStartedAt,
      signal,
      `worker ${step.id}`,
      strictIdentity
    );
    if (signaled) {
      signaledPids.push(step.workerPid);
    }
  }
  if (!waitForExit) {
    return;
  }
  for (const pid of signaledPids) {
    if (!await waitForProcessTreeExit(pid)) {
      throw new CleanupPendingError(
        `Worker process group ${pid} did not terminate`
      );
    }
  }
}
async function terminateOrphanedExecution(store, message) {
  let state = (await StateStore.load(store.runId)).snapshot();
  let engineSignaled = false;
  if (state.enginePid !== void 0) {
    engineSignaled = signalOwnedProcessTree(
      state.enginePid,
      state.engineStartedAt,
      "SIGKILL",
      "workflow engine"
    );
  }
  await signalRecordedWorkers(store, "SIGKILL", true, true);
  if (engineSignaled && state.enginePid !== void 0 && !await waitForProcessTreeExit(state.enginePid)) {
    throw new CleanupPendingError(
      `Workflow engine process group ${state.enginePid} did not terminate`
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
function signalOwnedProcessTree(pid, expectedStartedAt, signal, label, strictIdentity = true) {
  if (!isPidRunning(pid)) {
    if (processTreeIsRunning(pid)) {
      throw new CleanupPendingError(
        `${label} PID ${pid} exited while its process group remains`
      );
    }
    return false;
  }
  const actualStartedAt = processStartIdentity(pid);
  if (expectedStartedAt === void 0 || actualStartedAt === void 0) {
    if (processTreeIsRunning(pid)) {
      throw new CleanupPendingError(
        `Could not verify ${label} PID ${pid} before process-group cleanup`
      );
    }
    return false;
  }
  if (actualStartedAt !== expectedStartedAt) {
    if (strictIdentity) {
      throw new Error(
        `Refusing to signal ${label} PID ${pid}: process identity changed`
      );
    }
    return false;
  }
  signalProcessTree(pid, signal);
  return true;
}
async function waitForProcessTreeExit(pid, timeoutMs = 2e3) {
  const deadline = Date.now() + timeoutMs;
  while (processTreeIsRunning(pid) && Date.now() < deadline) {
    await sleep(25);
  }
  return !processTreeIsRunning(pid);
}
function processTreeIsRunning(pid) {
  try {
    process.kill(process.platform === "win32" ? pid : -pid, 0);
    return true;
  } catch {
    return false;
  }
}
async function waitForEngineRegistration(runId) {
  const deadline = Date.now() + 5e3;
  while (Date.now() < deadline) {
    const store = await StateStore.load(runId);
    const state = store.snapshot();
    if (state.enginePid === process.pid && processIdentityMatches(process.pid, state.engineStartedAt)) {
      return store;
    }
    await sleep(25);
  }
  throw new Error(`Workflow engine ${process.pid} was not registered`);
}
async function spawnDetached(store) {
  const entry = fileURLToPath(import.meta.url);
  const runnerToken = await store.claimRunner(process.pid);
  let handedOff = false;
  try {
    const runnerLog = await open(path4.join(store.directory, "runner.log"), "a");
    try {
      const child = spawn2(
        process.execPath,
        [entry, "_execute", store.runId, runnerToken],
        {
          detached: true,
          env: process.env,
          stdio: ["ignore", runnerLog.fd, runnerLog.fd]
        }
      );
      if (child.pid === void 0) {
        throw new Error("Detached runner did not receive a PID");
      }
      const pid = child.pid;
      const pidStartedAt = processStartIdentity(pid);
      if (pidStartedAt === void 0) {
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
async function recordBootstrapFailure(store, error, json) {
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
async function recordCleanupPending(store, error, json) {
  const message = `Process cleanup is incomplete: ${error.message}. Retry cancel or resume to continue cleanup.`;
  const state = await store.update((current) => {
    current.status = "canceling";
    current.cleanupPending = true;
    current.error = message;
    delete current.completedAt;
    delete current.pid;
    delete current.pidStartedAt;
    if (!processIdentityMatches(
      current.enginePid,
      current.engineStartedAt
    )) {
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
async function runCommand(parsed) {
  assertOptions(
    parsed,
    [
      "agent-timeout-ms",
      "concurrency",
      "cwd",
      "input",
      "max-agents",
      "max-runtime-ms"
    ],
    [
      "allow-danger-full-access",
      "allow-workspace-write",
      "detach",
      "json"
    ]
  );
  const workflowPath = path4.resolve(
    requirePositional(parsed, 0, "workflow file")
  );
  if (parsed.positionals.length > 1) {
    throw new Error("run accepts exactly one workflow file");
  }
  const source = await readFile3(workflowPath, "utf8");
  compileWorkflow(source, workflowPath);
  const cwd = path4.resolve(parsed.values.get("cwd") ?? process.cwd());
  const input = await parseInput(parsed.values.get("input"));
  const timestamp = nowIso();
  const workflowHash = sha256(source);
  const state = {
    ...input === void 0 ? {} : { args: input },
    agentInvocations: 0,
    authorization: authorizationFromFlags(parsed, workflowHash),
    concurrency: parseConcurrency(parsed.values.get("concurrency")),
    createdAt: timestamp,
    cwd,
    defaultAgentTimeoutMs: parseIntegerOption(
      "agent-timeout-ms",
      parsed.values.get("agent-timeout-ms"),
      DEFAULT_AGENT_TIMEOUT_MS,
      864e5
    ),
    maxAgentInvocations: parseIntegerOption(
      "max-agents",
      parsed.values.get("max-agents"),
      DEFAULT_MAX_AGENT_INVOCATIONS2,
      1e3
    ),
    maxRuntimeMs: parseIntegerOption(
      "max-runtime-ms",
      parsed.values.get("max-runtime-ms"),
      DEFAULT_MAX_RUNTIME_MS,
      6048e5
    ),
    runId: createRunId(),
    status: "starting",
    steps: {},
    updatedAt: timestamp,
    version: 1,
    workflowHash,
    workflowPath
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
async function validateCommand(parsed) {
  assertOptions(parsed, []);
  const workflowPath = path4.resolve(
    requirePositional(parsed, 0, "workflow file")
  );
  if (parsed.positionals.length > 1) {
    throw new Error("validate accepts exactly one workflow file");
  }
  compileWorkflow(await readFile3(workflowPath, "utf8"), workflowPath);
  console.log(`${workflowPath}: valid`);
  return 0;
}
async function statusCommand(parsed) {
  assertOptions(parsed, [], ["json"]);
  const runId = requirePositional(parsed, 0, "run ID");
  outputState(
    (await StateStore.load(runId)).snapshot(),
    parsed.flags.has("json")
  );
  return 0;
}
async function listCommand(parsed) {
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
async function logsCommand(parsed) {
  assertOptions(parsed, []);
  const store = await StateStore.load(
    requirePositional(parsed, 0, "run ID")
  );
  process.stdout.write(await store.readLog());
  return 0;
}
async function controlCommand(parsed, command) {
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
        "Interrupted after the workflow supervisor exited"
      );
      await terminalizeForcedRun(cleaned, "canceled", "Workflow canceled");
    } else if (!processIdentityMatches(state.enginePid, state.engineStartedAt)) {
      await store.update((current) => {
        current.status = "paused";
      });
    }
  }
  console.log(`${command === "pause" ? "Pausing" : "Canceling"} ${runId}`);
  return 0;
}
async function resumeCommand(parsed) {
  assertOptions(parsed, [], [
    "allow-danger-full-access",
    "allow-workspace-write",
    "foreground",
    "json"
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
    state.pidStartedAt
  );
  if (!runnerAlive) {
    const cleaned = await terminateOrphanedExecution(
      store,
      "Interrupted after the workflow supervisor exited"
    );
    store = cleaned;
    state = cleaned.snapshot();
  }
  const changingAuthorization = parsed.flags.has("allow-danger-full-access") || parsed.flags.has("allow-workspace-write");
  const workflowHash = changingAuthorization && !runnerAlive ? sha256(await readFile3(state.workflowPath, "utf8")) : state.workflowHash;
  const currentControl = await store.readControl();
  const authorization = authorizationFromFlags(
    parsed,
    workflowHash,
    currentControl.authorization ?? state.authorization
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
async function waitCommand(parsed) {
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
        `Run ${runId} has no active runner; use resume to continue it`
      );
    }
    await sleep(500);
  }
}
async function main() {
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
main().then((code) => {
  process.exitCode = code;
}).catch((error) => {
  console.error(`codex-workflow: ${errorMessage(error)}`);
  process.exitCode = 1;
});
