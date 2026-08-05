import {
  AppServerClient,
  AppServerRpcError,
  canonicalAppServerEndpoint,
  notificationHasClientId,
  type AppServerClientInfo,
  type AppServerThread,
  type JsonRpcNotification,
  type ThreadResumeResult,
  valueContainsClientId,
} from "./app-server-client.js";
import {
  boundedJsonValueMatches,
  errorMessage,
  JsonStructureLimitError,
  sleep,
} from "./utils.js";

const DELIVERY_CONFIRMATION_TIMEOUT_MS = 10_000;
const MAX_DELIVERY_SUBMISSIONS = 5;
const MAX_DIAGNOSTIC_BYTES = 4 * 1024;
const RETRY_DELAY_MAX_MS = 30_000;
const RETRY_DELAY_MIN_MS = 250;
const RETRY_JITTER_RATIO = 0.2;
const THREAD_POLL_MIN_MS = 10_000;
const THREAD_POLL_MAX_MS = 300_000;

export interface ThreadMessageDeliveryOptions {
  allowNonSteerableFallbackWithinAttempt?: boolean;
  clientInfo?: AppServerClientInfo;
  clientUserMessageId: string;
  endpoint: string;
  initialAttempts?: number;
  initialSubmissionWasAmbiguous?: boolean;
  maximumAttempts?: number;
  onAttempt?: (attempts: number) => Promise<void>;
  onDiagnostic?: (diagnostic: string) => Promise<void>;
  text: string;
  threadId: string;
  timeoutMs: number;
}

export interface ThreadMessageDeliveryResult {
  attempts: number;
  error?: string;
  status: "delivered" | "failed" | "unknown";
  turnId?: string;
}

export async function verifyThreadMessageTarget(
  endpoint: string,
  threadId: string,
  clientInfo?: AppServerClientInfo,
): Promise<string> {
  const canonicalEndpoint = canonicalAppServerEndpoint(endpoint);
  const client = await AppServerClient.connect(
    canonicalEndpoint,
    undefined,
    clientInfo,
  );
  try {
    let response: { thread: AppServerThread };
    try {
      response = await client.request<{ thread: AppServerThread }>(
        "thread/read",
        { includeTurns: false, threadId },
      );
    } catch (error) {
      if (error instanceof AppServerRpcError && /not loaded/i.test(error.message)) {
        throw threadNotLoadedError();
      }
      throw error;
    }
    if (response.thread.status.type === "notLoaded") {
      throw threadNotLoadedError();
    }
    return canonicalEndpoint;
  } finally {
    client.close();
  }
}

export async function deliverThreadMessage(
  options: ThreadMessageDeliveryOptions,
): Promise<ThreadMessageDeliveryResult> {
  const deadline = Date.now() + options.timeoutMs;
  let attempts = options.initialAttempts ?? 0;
  const maximumAttempts = options.maximumAttempts ?? MAX_DELIVERY_SUBMISSIONS;
  if (
    !Number.isInteger(attempts) ||
    !Number.isInteger(maximumAttempts) ||
    attempts < 0 ||
    maximumAttempts < attempts ||
    maximumAttempts > MAX_DELIVERY_SUBMISSIONS
  ) {
    throw new Error("Invalid thread-message delivery attempt bounds");
  }
  let submissionWasAmbiguous =
    options.initialSubmissionWasAmbiguous ?? attempts > 0;
  let lastError = "Thread message was not accepted";
  let retryCount = 0;

  while (Date.now() < deadline) {
    let client: AppServerClient | undefined;
    let submissionAccepted = false;
    let submissionAttempted = false;
    try {
      client = await AppServerClient.connect(
        options.endpoint,
        remainingTimeout(deadline),
        options.clientInfo,
      );
      const resumed = await client.request<ThreadResumeResult>(
        "thread/resume",
        { threadId: options.threadId },
        remainingTimeout(deadline),
      );
      if (valueContainsClientId(resumed.thread.turns, options.clientUserMessageId)) {
        return { attempts, status: "delivered" };
      }
      let thread: AppServerThread;
      if (submissionWasAmbiguous) {
        const reconciled = await reconcileAmbiguousSubmission(
          client,
          resumed.thread,
          options.threadId,
          options.clientUserMessageId,
          deadline,
        );
        if (reconciled.delivered) {
          return { attempts, status: "delivered" };
        }
        thread = reconciled.thread;
        submissionWasAmbiguous = false;
      } else {
        thread = await waitForDeliverableThread(
          client,
          resumed.thread,
          options.threadId,
          deadline,
        );
      }
      if (attempts >= maximumAttempts) {
        return submissionLimitResult(
          attempts,
          maximumAttempts,
          submissionWasAmbiguous,
          lastError,
        );
      }
      const request = deliveryRequest(
        thread,
        options.threadId,
        options.clientUserMessageId,
        options.text,
      );
      attempts += 1;
      await options.onAttempt?.(attempts);
      submissionAttempted = true;
      const response = await client.request<{
        turnId?: string;
        turn?: { id: string };
      }>(request.method, request.params, remainingTimeout(deadline));
      submissionAccepted = true;
      const turnId = response.turnId ?? response.turn?.id;
      if (
        await confirmDelivery(
          client,
          options.threadId,
          options.clientUserMessageId,
          deadline,
        )
      ) {
        return { attempts, status: "delivered", ...(turnId ? { turnId } : {}) };
      }
      submissionWasAmbiguous = true;
      lastError =
        "App Server accepted the request but did not confirm the user message";
    } catch (error) {
      let deliveryError = error;
      let shouldWaitForIdle = false;
      if (client !== undefined && error instanceof AppServerRpcError) {
        try {
          shouldWaitForIdle = isActiveTurnNotSteerableRpcError(error);
        } catch (inspectionError) {
          deliveryError = inspectionError;
        }
      }
      if (
        options.allowNonSteerableFallbackWithinAttempt &&
        shouldWaitForIdle &&
        submissionAttempted &&
        !submissionAccepted
      ) {
        // A review/compaction steering refusal confirms that no message was
        // accepted. Return the reserved slot so the required idle fallback
        // can still submit within the same durable delivery attempt.
        attempts -= 1;
        await options.onAttempt?.(attempts);
      }
      if (error instanceof AppServerRpcError && !submissionAccepted) {
        submissionAttempted = false;
      }
      submissionWasAmbiguous ||= submissionAttempted;
      lastError = deliveryDiagnostic(deliveryError);
      await options.onDiagnostic?.(lastError);
      if (deliveryError instanceof JsonStructureLimitError) {
        return failureResult(attempts, submissionWasAmbiguous, lastError);
      }
      if (shouldWaitForIdle && client) {
        try {
          await waitForIdleThread(client, options.threadId, deadline);
        } catch (waitError) {
          lastError = deliveryDiagnostic(waitError);
          await options.onDiagnostic?.(lastError);
        }
      }
      if (error instanceof AppServerRpcError && !isRetryableRpcError(error)) {
        return failureResult(attempts, submissionWasAmbiguous, lastError);
      }
    } finally {
      client?.close();
    }
    if (attempts >= maximumAttempts) {
      return submissionLimitResult(
        attempts,
        maximumAttempts,
        submissionWasAmbiguous,
        lastError,
      );
    }
    const delay = threadMessageRetryDelayMs(retryCount);
    retryCount += 1;
    await sleep(Math.min(delay, Math.max(1, deadline - Date.now())));
  }
  return failureResult(
    attempts,
    submissionWasAmbiguous,
    deliveryDiagnostic(`${lastError}; notification deadline expired`),
  );
}

export function threadMessageRetryDelayMs(
  retryCount: number,
  random: () => number = Math.random,
): number {
  const boundedRetryCount = Math.min(
    7,
    Math.max(0, Number.isFinite(retryCount) ? Math.floor(retryCount) : 0),
  );
  const exponentialDelay = Math.min(
    RETRY_DELAY_MAX_MS,
    RETRY_DELAY_MIN_MS * 2 ** boundedRetryCount,
  );
  const sample = random();
  const randomValue = Number.isFinite(sample)
    ? Math.min(1, Math.max(0, sample))
    : 0.5;
  const jitter =
    1 - RETRY_JITTER_RATIO + 2 * RETRY_JITTER_RATIO * randomValue;
  return Math.max(
    1,
    Math.min(RETRY_DELAY_MAX_MS, Math.round(exponentialDelay * jitter)),
  );
}

function failureResult(
  attempts: number,
  ambiguous: boolean,
  error: string,
): ThreadMessageDeliveryResult {
  return {
    attempts,
    error,
    status: ambiguous ? "unknown" : "failed",
  };
}

function submissionLimitResult(
  attempts: number,
  maximumAttempts: number,
  ambiguous: boolean,
  lastError: string,
): ThreadMessageDeliveryResult {
  return failureResult(
    attempts,
    ambiguous,
    deliveryDiagnostic(
      `Callback submission limit of ${maximumAttempts} ` +
        `reached: ${lastError}`,
    ),
  );
}

function deliveryRequest(
  thread: AppServerThread,
  threadId: string,
  clientId: string,
  text: string,
): { method: "turn/start" | "turn/steer"; params: Record<string, unknown> } {
  const input = [{ text, type: "text" }];
  if (thread.status.type === "active") {
    const activeTurn = [...(thread.turns ?? [])]
      .reverse()
      .find((turn) => turn.status === "inProgress");
    if (!activeTurn) {
      throw new Error("Active thread did not expose an in-progress turn");
    }
    return {
      method: "turn/steer",
      params: {
        clientUserMessageId: clientId,
        expectedTurnId: activeTurn.id,
        input,
        threadId,
      },
    };
  }
  return {
    method: "turn/start",
    params: { clientUserMessageId: clientId, input, threadId },
  };
}

async function confirmDelivery(
  client: AppServerClient,
  threadId: string,
  clientId: string,
  deadline: number,
): Promise<boolean> {
  const timeout = Math.min(
    DELIVERY_CONFIRMATION_TIMEOUT_MS,
    Math.max(1, deadline - Date.now()),
  );
  try {
    await client.waitForNotification(
      (notification) => notificationHasClientId(notification, clientId),
      timeout,
    );
    return true;
  } catch {
    const thread = await readThread(client, threadId, true, deadline);
    return valueContainsClientId(thread.turns, clientId);
  }
}

async function waitForDeliverableThread(
  client: AppServerClient,
  initial: AppServerThread,
  threadId: string,
  deadline: number,
): Promise<AppServerThread> {
  let thread = initial;
  let pollInterval = THREAD_POLL_MIN_MS;
  while (Date.now() < deadline) {
    if (thread.status.type === "idle") {
      return thread;
    }
    if (
      thread.status.type === "active" &&
      thread.turns?.some((turn) => turn.status === "inProgress")
    ) {
      return thread;
    }
    assertUsableThread(thread);
    await waitForThreadChange(client, threadId, pollInterval, deadline);
    thread = await readThread(client, threadId, true, deadline);
    pollInterval = nextPollInterval(pollInterval);
  }
  throw new Error("Timed out waiting for the target Codex thread");
}

async function reconcileAmbiguousSubmission(
  client: AppServerClient,
  initial: AppServerThread,
  threadId: string,
  clientId: string,
  deadline: number,
): Promise<{ delivered: boolean; thread: AppServerThread }> {
  let thread = initial;
  let pollInterval = THREAD_POLL_MIN_MS;
  while (Date.now() < deadline) {
    if (valueContainsClientId(thread.turns, clientId)) {
      return { delivered: true, thread };
    }
    if (thread.status.type === "idle") {
      return { delivered: false, thread };
    }
    assertUsableThread(thread);
    const notification = await waitForMessageOrThreadChange(
      client,
      threadId,
      clientId,
      pollInterval,
      deadline,
    );
    if (notification && notificationHasClientId(notification, clientId)) {
      return { delivered: true, thread };
    }
    thread = await readThread(client, threadId, true, deadline);
    pollInterval = nextPollInterval(pollInterval);
  }
  throw new Error("Timed out reconciling an ambiguous callback submission");
}

async function waitForIdleThread(
  client: AppServerClient,
  threadId: string,
  deadline: number,
): Promise<void> {
  let interval = THREAD_POLL_MIN_MS;
  let thread = await readThread(client, threadId, false, deadline);
  while (Date.now() < deadline) {
    if (thread.status.type === "idle") {
      return;
    }
    assertUsableThread(thread);
    await waitForThreadChange(client, threadId, interval, deadline);
    thread = await readThread(client, threadId, false, deadline);
    interval = nextPollInterval(interval);
  }
  throw new Error("Timed out waiting for the target Codex thread to become idle");
}

function assertUsableThread(thread: AppServerThread): void {
  if (thread.status.type === "systemError") {
    throw new Error("The target Codex thread is in a system-error state");
  }
  if (thread.status.type === "notLoaded") {
    throw threadNotLoadedError();
  }
}

async function readThread(
  client: AppServerClient,
  threadId: string,
  includeTurns: boolean,
  deadline: number,
): Promise<AppServerThread> {
  const response = await client.request<{ thread: AppServerThread }>(
    "thread/read",
    { includeTurns, threadId },
    remainingTimeout(deadline),
  );
  return response.thread;
}

async function waitForThreadChange(
  client: AppServerClient,
  threadId: string,
  interval: number,
  deadline: number,
): Promise<void> {
  try {
    await client.waitForNotification(
      (notification) => isTargetThreadStatusChange(notification, threadId),
      Math.min(interval, Math.max(1, deadline - Date.now())),
    );
  } catch (error) {
    if (!isNotificationTimeout(error)) {
      throw error;
    }
  }
}

async function waitForMessageOrThreadChange(
  client: AppServerClient,
  threadId: string,
  clientId: string,
  interval: number,
  deadline: number,
): Promise<JsonRpcNotification | undefined> {
  try {
    return await client.waitForNotification(
      (notification) =>
        notificationHasClientId(notification, clientId) ||
        isTargetThreadStatusChange(notification, threadId),
      Math.min(interval, Math.max(1, deadline - Date.now())),
    );
  } catch (error) {
    if (isNotificationTimeout(error)) {
      return undefined;
    }
    throw error;
  }
}

function isTargetThreadStatusChange(
  notification: JsonRpcNotification,
  threadId: string,
): boolean {
  return (
    notification.method === "thread/status/changed" &&
    isRecord(notification.params) &&
    notification.params.threadId === threadId
  );
}

function isRetryableRpcError(error: AppServerRpcError): boolean {
  if (isActiveTurnNotSteerableRpcError(error)) {
    return true;
  }
  return (
    error.code === -32_001 ||
    /thread .* is closing; retry thread\/resume after the thread is closed/i.test(
      error.message,
    ) ||
    /active turn|expected.*turn|no active turn|not.*steerable|not idle/i.test(
      error.message,
    )
  );
}

function isActiveTurnNotSteerableRpcError(
  error: AppServerRpcError,
): boolean {
  return (
    boundedJsonValueMatches(
      error.data,
      (item) => isRecord(item) && "activeTurnNotSteerable" in item,
      128,
      250_000,
    ) || /cannot steer a (review|compact) turn/i.test(error.message)
  );
}

function threadNotLoadedError(): Error {
  return new Error(
    "The current thread is not loaded on this App Server. Start or resume " +
      "the session through codex-dynamic.",
  );
}

function isNotificationTimeout(error: unknown): boolean {
  return (
    error instanceof Error &&
    error.message === "Timed out waiting for App Server notification"
  );
}

function nextPollInterval(current: number): number {
  return Math.min(THREAD_POLL_MAX_MS, current * 2);
}

function remainingTimeout(deadline: number): number {
  return Math.min(30_000, Math.max(1, deadline - Date.now()));
}

function deliveryDiagnostic(error: unknown): string {
  const value = errorMessage(error);
  const encoded = Buffer.from(value, "utf8");
  if (encoded.length <= MAX_DIAGNOSTIC_BYTES) {
    return value;
  }
  const suffix = "\n[truncated callback diagnostic]";
  const suffixBytes = Buffer.byteLength(suffix, "utf8");
  let end = MAX_DIAGNOSTIC_BYTES - suffixBytes;
  while (end > 0 && ((encoded[end] as number) & 0xc0) === 0x80) {
    end -= 1;
  }
  return `${encoded.subarray(0, end).toString("utf8")}${suffix}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
