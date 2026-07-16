import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import type {
  CodexExecution,
  CodexRequest,
  JsonValue,
  TokenUsage,
} from "./types.js";
import {
  assertBoundedJsonStructure,
  errorMessage,
  JsonStructureLimitError,
  processGroupIsRunning,
  processStartIdentity,
  safeIdentifier,
  sha256,
} from "./utils.js";

interface CodexEvent {
  error?: { message?: string };
  item?: { text?: string; type?: string };
  message?: string;
  thread_id?: string;
  type?: string;
  usage?: {
    cached_input_tokens?: number;
    input_tokens?: number;
    output_tokens?: number;
  };
}

const MAX_PROMPT_BYTES = 1_000_000;
const MAX_RESULT_BYTES = 1_000_000;
const MAX_EVENT_ERROR_BYTES = 32_000;
const MAX_NDJSON_RECORD_BYTES = 8 * 1024 * 1024;
const MAX_WORKER_EVENT_BYTES = 16 * 1024 * 1024;
const MAX_WORKER_EVENT_DEPTH = 128;
const MAX_WORKER_EVENT_NODES = 250_000;
const MAX_PERSISTED_EVENT_BYTES = 16 * 1024 * 1024;
const MAX_STANDARD_ERROR_BYTES = 32_000;
const PERSISTENCE_SETTLEMENT_MS = 1_000;
const TERMINATION_GRACE_MS = 2_000;

export class CodexProcessError extends Error {
  readonly retryable: boolean;
  readonly threadId: string | undefined;
  readonly usage: TokenUsage | undefined;

  constructor(
    message: string,
    retryable = true,
    metadata: {
      threadId?: string | undefined;
      usage?: TokenUsage | undefined;
    } = {},
  ) {
    super(message);
    this.name = "CodexProcessError";
    this.retryable = retryable;
    this.threadId = metadata.threadId;
    this.usage = metadata.usage;
  }
}

export class CodexCleanupPendingError extends CodexProcessError {
  readonly cleanupPending = true;
  readonly workerPid: number;
  readonly workerStartedAt: string | undefined;

  constructor(
    message: string,
    workerPid: number,
    workerStartedAt: string | undefined,
    metadata: {
      threadId?: string | undefined;
      usage?: TokenUsage | undefined;
    } = {},
  ) {
    super(message, false, metadata);
    this.name = "CodexCleanupPendingError";
    this.workerPid = workerPid;
    this.workerStartedAt = workerStartedAt;
  }
}

export function isCodexCleanupPendingError(
  error: unknown,
): error is CodexCleanupPendingError {
  return (
    typeof error === "object" &&
    error !== null &&
    "cleanupPending" in error &&
    error.cleanupPending === true
  );
}

export class CodexRunner {
  constructor(
    private readonly onEvent?: (
      stepId: string,
      event: unknown,
    ) => Promise<void>,
    private readonly onSpawn?: (
      stepId: string,
      workerPid: number,
      workerStartedAt: string,
    ) => Promise<void>,
  ) {}

  async run(request: CodexRequest): Promise<CodexExecution> {
    if (request.signal.aborted) {
      throw request.signal.reason;
    }
    const promptBytes = Buffer.byteLength(request.prompt, "utf8");
    if (promptBytes > MAX_PROMPT_BYTES) {
      throw new CodexProcessError(
        `Codex worker prompt is ${promptBytes} bytes; maximum is ` +
          `${MAX_PROMPT_BYTES}. Chunk inputs or use a tree reduction.`,
        false,
      );
    }
    const command = process.env.CODEX_WORKFLOW_CODEX_BIN ?? "codex";
    const args = await this.buildArgs(request);
    if (request.signal.aborted) {
      throw request.signal.reason;
    }
    const cwd = path.resolve(
      request.workflowCwd,
      request.options.cwd ?? ".",
    );

    return await new Promise<CodexExecution>((resolve, reject) => {
      const child = spawn(command, args, {
        cwd,
        detached: process.platform !== "win32",
        env: process.env,
        stdio: ["pipe", "pipe", "pipe"],
      });
      let finalText = "";
      let workerStartedAt: string | undefined;
      let threadId: string | undefined;
      let usage: TokenUsage | undefined;
      let standardError: Buffer = Buffer.alloc(0);
      let eventError = "";
      let workerEventBytes = 0;
      let persistedEventBytes = 0;
      let recordBytes = 0;
      let recordChunks: Buffer[] = [];
      let timedOut = false;
      let settled = false;
      let finalizationStarted = false;
      let terminationStarted = false;
      let terminationDeadline = 0;
      let killTimer: NodeJS.Timeout | undefined;
      let persistenceError: unknown;
      let outputError: CodexProcessError | undefined;
      let persistenceQueue = Promise.resolve();

      const persist = (operation: () => Promise<void>): void => {
        persistenceQueue = persistenceQueue
          .then(operation)
          .catch((error: unknown) => {
            persistenceError ??= error;
            terminate();
            void finalize(null, null);
          });
      };

      const terminate = (): void => {
        if (terminationStarted) {
          return;
        }
        terminationStarted = true;
        terminationDeadline = Date.now() + TERMINATION_GRACE_MS;
        signalProcessGroup(child, "SIGTERM");
        killTimer = setTimeout(
          () => signalProcessGroup(child, "SIGKILL"),
          TERMINATION_GRACE_MS,
        );
        killTimer.unref();
      };

      const timeout = request.options.timeoutMs ?? request.defaultTimeoutMs;
      const timeoutTimer = setTimeout(() => {
        timedOut = true;
        terminate();
        void finalize(null, null);
      }, timeout);
      timeoutTimer.unref();

      const abort = (): void => {
        terminate();
        void finalize(null, null);
      };
      request.signal.addEventListener("abort", abort, { once: true });
      if (request.signal.aborted) {
        abort();
      }

      if (!terminationStarted && child.pid !== undefined && this.onSpawn) {
        const spawnedWorkerPid = child.pid;
        const spawnedWorkerStartedAt = processStartIdentity(spawnedWorkerPid);
        workerStartedAt = spawnedWorkerStartedAt;
        if (spawnedWorkerStartedAt === undefined) {
          persistenceError = new Error(
            `Could not identify Codex worker PID ${spawnedWorkerPid}`,
          );
          terminate();
          void finalize(null, null);
        } else {
          persist(
            async () =>
              await this.onSpawn?.(
                request.stepId,
                spawnedWorkerPid,
                spawnedWorkerStartedAt,
              ),
          );
        }
      }

      const failOutput = (message: string): void => {
        if (outputError !== undefined) {
          return;
        }
        outputError = new CodexProcessError(message, false, {
          threadId,
          usage,
        });
        recordChunks = [];
        recordBytes = 0;
        child.stdout.destroy();
        terminate();
      };

      const processRecord = (record: Buffer, delimiterBytes: number): void => {
        if (outputError !== undefined) {
          return;
        }
        const nextWorkerEventBytes =
          workerEventBytes + record.length + delimiterBytes;
        if (nextWorkerEventBytes > MAX_WORKER_EVENT_BYTES) {
          failOutput(
            `Codex worker emitted more than ${MAX_WORKER_EVENT_BYTES} bytes ` +
              "of NDJSON events",
          );
          return;
        }
        workerEventBytes = nextWorkerEventBytes;
        const trimmed = record.toString("utf8").trim();
        if (trimmed === "") {
          return;
        }
        let event: CodexEvent;
        try {
          event = JSON.parse(trimmed) as CodexEvent;
        } catch {
          const delimiter =
            delimiterBytes === 0 ? Buffer.alloc(0) : Buffer.from("\n");
          standardError = appendBoundedBytes(
            standardError,
            Buffer.concat([record, delimiter]),
            MAX_STANDARD_ERROR_BYTES,
          );
          return;
        }
        try {
          assertBoundedJsonStructure(
            event,
            MAX_WORKER_EVENT_DEPTH,
            MAX_WORKER_EVENT_NODES,
          );
        } catch (error) {
          if (error instanceof JsonStructureLimitError) {
            failOutput(`Codex worker NDJSON event ${error.message}`);
            return;
          }
          throw error;
        }
        try {
          if (
            event.type === "item.completed" &&
            event.item?.type === "agent_message" &&
            typeof event.item.text === "string"
          ) {
            const resultBytes = Buffer.byteLength(event.item.text, "utf8");
            if (resultBytes > MAX_RESULT_BYTES) {
              failOutput(
                `Codex worker result is ${resultBytes} bytes; maximum is ` +
                  `${MAX_RESULT_BYTES}. Return compact structured output.`,
              );
              return;
            }
            finalText = event.item.text;
          } else if (event.type === "turn.failed" || event.type === "error") {
            const extractedError =
              typeof event.error?.message === "string"
                ? event.error.message
                : typeof event.message === "string"
                  ? event.message
                  : trimmed;
            const errorBytes = Buffer.byteLength(extractedError, "utf8");
            if (errorBytes > MAX_EVENT_ERROR_BYTES) {
              failOutput(
                `Codex worker event error is ${errorBytes} bytes; maximum is ` +
                  `${MAX_EVENT_ERROR_BYTES}.`,
              );
              return;
            }
            eventError = extractedError;
          }

          if (this.onEvent) {
            const eventBytes =
              Buffer.byteLength(
                JSON.stringify({ event, stepId: request.stepId }),
                "utf8",
              ) + 128;
            if (
              eventBytes > MAX_PERSISTED_EVENT_BYTES - persistedEventBytes
            ) {
              failOutput(
                `Codex worker events exceed the ` +
                  `${MAX_PERSISTED_EVENT_BYTES}-byte persistence limit`,
              );
              return;
            }
            persistedEventBytes += eventBytes;
            persist(async () => await this.onEvent?.(request.stepId, event));
          }

          if (event.type === "thread.started") {
            threadId = event.thread_id;
          } else if (event.type === "turn.completed" && event.usage) {
            usage = {
              ...(event.usage.cached_input_tokens === undefined
                ? {}
                : { cachedInputTokens: event.usage.cached_input_tokens }),
              ...(event.usage.input_tokens === undefined
                ? {}
                : { inputTokens: event.usage.input_tokens }),
              ...(event.usage.output_tokens === undefined
                ? {}
                : { outputTokens: event.usage.output_tokens }),
            };
          }
        } catch (error) {
          failOutput(
            `Could not process Codex worker event: ${errorMessage(error)}`,
          );
        }
      };

      child.stdout.on("data", (chunk: Buffer) => {
        if (outputError !== undefined) {
          return;
        }
        let offset = 0;
        while (offset < chunk.length && outputError === undefined) {
          const newline = chunk.indexOf(0x0a, offset);
          const end = newline === -1 ? chunk.length : newline;
          const segment = chunk.subarray(offset, end);
          const nextRecordBytes = recordBytes + segment.length;
          if (nextRecordBytes > MAX_NDJSON_RECORD_BYTES) {
            failOutput(
              `Codex worker NDJSON record exceeds the ` +
                `${MAX_NDJSON_RECORD_BYTES}-byte limit`,
            );
            return;
          }
          if (segment.length > 0) {
            recordChunks.push(segment);
            recordBytes = nextRecordBytes;
          }
          if (newline === -1) {
            return;
          }
          const record =
            recordChunks.length === 0
              ? Buffer.alloc(0)
              : recordChunks.length === 1
                ? recordChunks[0] as Buffer
                : Buffer.concat(recordChunks, recordBytes);
          recordChunks = [];
          recordBytes = 0;
          processRecord(record, 1);
          offset = newline + 1;
        }
      });

      child.stdout.on("end", () => {
        if (outputError !== undefined || recordBytes === 0) {
          return;
        }
        const record =
          recordChunks.length === 1
            ? recordChunks[0] as Buffer
            : Buffer.concat(recordChunks, recordBytes);
        recordChunks = [];
        recordBytes = 0;
        processRecord(record, 0);
      });

      child.stderr.on("data", (chunk: Buffer) => {
        standardError = appendBoundedBytes(
          standardError,
          chunk,
          MAX_STANDARD_ERROR_BYTES,
        );
      });

      child.on("error", (error) => {
        if (settled || finalizationStarted) {
          return;
        }
        finalizationStarted = true;
        settled = true;
        clearTimeout(timeoutTimer);
        clearTimeout(killTimer);
        request.signal.removeEventListener("abort", abort);
        reject(
          new CodexProcessError(
            `Could not start ${command}: ${errorMessage(error)}`,
            false,
          ),
        );
      });

      child.on("close", (code, signal) => {
        void finalize(code, signal);
      });

      async function finalize(
        code: number | null,
        signal: NodeJS.Signals | null,
      ): Promise<void> {
        if (settled || finalizationStarted) {
          return;
        }
        finalizationStarted = true;
        clearTimeout(timeoutTimer);
        request.signal.removeEventListener("abort", abort);
        try {
          if (
            child.pid !== undefined &&
            !(await finishProcessGroupTermination(
              child.pid,
              terminationStarted ? terminationDeadline : Date.now(),
            ))
          ) {
            reject(
              new CodexCleanupPendingError(
                `Codex worker process group ${child.pid} did not terminate`,
                child.pid,
                workerStartedAt,
                { threadId, usage },
              ),
            );
            return;
          }
          clearTimeout(killTimer);
          if (
            !(await promiseSettledWithin(
              persistenceQueue,
              PERSISTENCE_SETTLEMENT_MS,
            ))
          ) {
            reject(
              new CodexProcessError(
                `Codex worker persistence did not settle within ` +
                  `${PERSISTENCE_SETTLEMENT_MS} ms`,
                false,
                { threadId, usage },
              ),
            );
            return;
          }
          if (persistenceError) {
            reject(
              new CodexProcessError(
                `Could not persist Codex worker events: ` +
                  errorMessage(persistenceError),
                false,
              ),
            );
            return;
          }
          if (outputError) {
            reject(outputError);
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
                { threadId, usage },
              ),
            );
            return;
          }
          const details = eventError || standardError.toString("utf8").trim();
          if (code !== 0) {
            reject(
              processFailure(
                `Codex worker exited ${String(code)}${
                  signal ? ` (${signal})` : ""
                }: ${details || "no error details"}`,
                threadId,
                usage,
              ),
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
                "Codex worker completed without an agent message",
              ),
            );
            return;
          }
          let data: JsonValue | undefined;
          if (request.options.schema) {
            try {
              data = JSON.parse(finalText) as JsonValue;
            } catch (error) {
              reject(
                new CodexProcessError(
                  `Structured Codex result was not JSON: ${errorMessage(error)}`,
                  false,
                ),
              );
              return;
            }
          }
          resolve({
            ...(data === undefined ? {} : { data }),
            text: finalText,
            ...(threadId === undefined ? {} : { threadId }),
            ...(usage === undefined ? {} : { usage }),
          });
        } finally {
          settled = true;
          clearTimeout(killTimer);
        }
      }

      child.stdin.on("error", () => {
        // The child may exit before consuming stdin; close handling reports why.
      });
      const workerRegistration = persistenceQueue;
      void (async () => {
        await workerRegistration;
        if (!settled && !terminationStarted && persistenceError === undefined) {
          child.stdin.end(request.prompt);
        }
      })();
    });
  }

  private async buildArgs(request: CodexRequest): Promise<string[]> {
    const options = request.options;
    const common: string[] = ["--json"];

    if (options.model) {
      common.push("--model", options.model);
    }
    if (options.reasoningEffort) {
      common.push(
        "--config",
        `model_reasoning_effort=${JSON.stringify(options.reasoningEffort)}`,
      );
    }
    common.push("--config", 'approval_policy="never"');
    if (options.ignoreUserConfig) {
      common.push("--ignore-user-config");
    }
    common.push("--skip-git-repo-check");

    if (options.schema) {
      const schemaDirectory = path.join(request.runDirectory, "schemas");
      await mkdir(schemaDirectory, { recursive: true });
      const readableId = safeIdentifier(request.stepId)
        .replaceAll("/", "-")
        .slice(-80);
      const schemaPath = path.join(
        schemaDirectory,
        `${readableId}-${sha256(request.stepId).slice(0, 12)}.json`,
      );
      await writeFile(
        schemaPath,
        `${JSON.stringify(options.schema, null, 2)}\n`,
        "utf8",
      );
      common.push("--output-schema", schemaPath);
    }

    if (options.resumeThreadId) {
      return [
        "exec",
        "resume",
        ...common,
        options.resumeThreadId,
        "-",
      ];
    }

    const cwd = path.resolve(request.workflowCwd, options.cwd ?? ".");
    const args = [
      "exec",
      ...common,
      "--sandbox",
      options.sandbox ?? "read-only",
      "--cd",
      cwd,
    ];
    for (const directory of options.addDirs ?? []) {
      args.push("--add-dir", path.resolve(cwd, directory));
    }
    args.push("-");
    return args;
  }
}

function signalProcessGroup(
  child: ReturnType<typeof spawn>,
  signal: NodeJS.Signals,
): void {
  try {
    if (process.platform !== "win32" && child.pid !== undefined) {
      process.kill(-child.pid, signal);
    } else {
      child.kill(signal);
    }
  } catch {
    // The process may have exited between the liveness check and the signal.
  }
}

function processFailure(
  message: string,
  threadId?: string,
  usage?: TokenUsage,
): CodexProcessError {
  if (
    /context[_ ]length[_ ]exceeded|context (window|length)|maximum context|too many tokens|prompt.{0,20}too long/i
      .test(message)
  ) {
    return new CodexProcessError(
      `Codex worker exceeded its context capacity. Chunk the input or use a ` +
        `tree reduction, then resume the workflow. Details: ${message}`,
      false,
      { threadId, usage },
    );
  }
  return new CodexProcessError(message, true, { threadId, usage });
}

async function finishProcessGroupTermination(
  pid: number,
  deadline: number,
): Promise<boolean> {
  while (processGroupIsRunning(pid) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  if (processGroupIsRunning(pid)) {
    signalProcessGroupByPid(pid, "SIGKILL");
  }
  const killDeadline = Date.now() + 1_000;
  while (processGroupIsRunning(pid) && Date.now() < killDeadline) {
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  return !processGroupIsRunning(pid);
}

export function isDeadProcessState(state: string): boolean {
  return /^[ZXx]/.test(state);
}

function signalProcessGroupByPid(pid: number, signal: NodeJS.Signals): void {
  try {
    process.kill(process.platform === "win32" ? pid : -pid, signal);
  } catch {
    // The process group may already be gone.
  }
}

function appendBoundedBytes(
  current: Buffer,
  incoming: Buffer,
  maximumBytes: number,
): Buffer {
  if (incoming.length >= maximumBytes) {
    return Buffer.from(incoming.subarray(incoming.length - maximumBytes));
  }
  const retainedBytes = Math.min(
    current.length,
    maximumBytes - incoming.length,
  );
  return Buffer.concat(
    [current.subarray(current.length - retainedBytes), incoming],
    retainedBytes + incoming.length,
  );
}

async function promiseSettledWithin(
  promise: Promise<unknown>,
  timeoutMs: number,
): Promise<boolean> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      promise.then(() => true),
      new Promise<false>((resolve) => {
        timer = setTimeout(() => resolve(false), timeoutMs);
        timer.unref();
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
