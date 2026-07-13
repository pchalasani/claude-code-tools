import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import readline from "node:readline";

import type {
  CodexExecution,
  CodexRequest,
  JsonValue,
  TokenUsage,
} from "./types.js";
import {
  errorMessage,
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
      let standardError = "";
      let eventError = "";
      let timedOut = false;
      let settled = false;
      let terminationStarted = false;
      let terminationDeadline = 0;
      let killTimer: NodeJS.Timeout | undefined;
      let persistenceError: unknown;
      let persistenceQueue = Promise.resolve();

      const persist = (operation: () => Promise<void>): void => {
        persistenceQueue = persistenceQueue
          .then(operation)
          .catch((error: unknown) => {
            persistenceError ??= error;
            terminate();
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
      }, timeout);
      timeoutTimer.unref();

      if (child.pid !== undefined && this.onSpawn) {
        const spawnedWorkerPid = child.pid;
        const spawnedWorkerStartedAt = processStartIdentity(spawnedWorkerPid);
        workerStartedAt = spawnedWorkerStartedAt;
        if (spawnedWorkerStartedAt === undefined) {
          persistenceError = new Error(
            `Could not identify Codex worker PID ${spawnedWorkerPid}`,
          );
          terminate();
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

      const abort = (): void => terminate();
      request.signal.addEventListener("abort", abort, { once: true });

      const lines = readline.createInterface({ input: child.stdout });
      lines.on("line", (line) => {
        const trimmed = line.trim();
        if (trimmed === "") {
          return;
        }
        try {
          const event = JSON.parse(trimmed) as CodexEvent;
          if (this.onEvent) {
            persist(async () => await this.onEvent?.(request.stepId, event));
          }
          if (event.type === "thread.started") {
            threadId = event.thread_id;
          } else if (
            event.type === "item.completed" &&
            event.item?.type === "agent_message" &&
            event.item.text
          ) {
            finalText = event.item.text;
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
          } else if (event.type === "turn.failed" || event.type === "error") {
            eventError = event.error?.message ?? event.message ?? trimmed;
          }
        } catch {
          standardError += `${trimmed}\n`;
          standardError = standardError.slice(-32_000);
        }
      });

      child.stderr.on("data", (chunk: Buffer) => {
        standardError += chunk.toString("utf8");
        if (standardError.length > 32_000) {
          standardError = standardError.slice(-32_000);
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
            false,
          ),
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
          await persistenceQueue;
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
          const details = eventError || standardError.trim();
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
          const resultBytes = Buffer.byteLength(finalText, "utf8");
          if (resultBytes > MAX_RESULT_BYTES) {
            reject(
              new CodexProcessError(
                `Codex worker result is ${resultBytes} bytes; maximum is ` +
                  `${MAX_RESULT_BYTES}. Return compact structured output.`,
                false,
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
        })();
      });

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

function processGroupIsRunning(pid: number): boolean {
  try {
    process.kill(process.platform === "win32" ? pid : -pid, 0);
    return true;
  } catch {
    return false;
  }
}

function signalProcessGroupByPid(pid: number, signal: NodeJS.Signals): void {
  try {
    process.kill(process.platform === "win32" ? pid : -pid, signal);
  } catch {
    // The process group may already be gone.
  }
}
