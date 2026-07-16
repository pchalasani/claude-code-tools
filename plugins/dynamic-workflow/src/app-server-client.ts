import { createHash, randomBytes } from "node:crypto";
import { request as httpRequest } from "node:http";
import { homedir } from "node:os";
import path from "node:path";
import type { Duplex } from "node:stream";

import { boundedJsonValueMatches, errorMessage } from "./utils.js";

const WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
const MAX_MESSAGE_BYTES = 128 * 1024 * 1024;
const MAX_QUEUED_NOTIFICATION_BYTES = 8 * 1024 * 1024;
const MAX_QUEUED_NOTIFICATIONS = 1_000;
const MAX_RESPONSE_DEPTH = 128;
const MAX_RESPONSE_NODES = 250_000;
const MAX_RPC_ERROR_MESSAGE_BYTES = 4 * 1024;
const REQUEST_TIMEOUT_MS = 30_000;
const MINIMUM_APP_SERVER_VERSION = [0, 136, 0] as const;
const MINIMUM_APP_SERVER_VERSION_TEXT = MINIMUM_APP_SERVER_VERSION.join(".");

export interface JsonRpcNotification {
  method: string;
  params?: unknown;
}

interface JsonRpcResponse {
  error?: { code: number; data?: unknown; message: string };
  id: number;
  result?: unknown;
}

interface PendingRequest {
  reject: (error: Error) => void;
  resolve: (value: unknown) => void;
  timer: NodeJS.Timeout;
}

interface NotificationWaiter {
  predicate: (notification: JsonRpcNotification) => boolean;
  reject: (error: Error) => void;
  resolve: (notification: JsonRpcNotification) => void;
  timer: NodeJS.Timeout;
}

interface QueuedNotification {
  bytes: number;
  notification: JsonRpcNotification;
}

export class AppServerRpcError extends Error {
  readonly code: number;
  readonly data?: unknown;

  constructor(error: { code: number; data?: unknown; message: string }) {
    super(truncateRpcDiagnostic(error.message));
    this.name = "AppServerRpcError";
    this.code = error.code;
    this.data = error.data;
  }
}

export interface AppServerThread {
  id: string;
  status: AppServerThreadStatus;
  turns?: AppServerTurn[];
}

export interface AppServerTurn {
  id: string;
  items?: unknown[];
  status: "completed" | "failed" | "inProgress" | "interrupted";
}

export type AppServerThreadStatus =
  | { type: "active"; activeFlags?: string[] }
  | { type: "idle" }
  | { type: "notLoaded" }
  | { type: "systemError" };

export interface ThreadResumeResult {
  thread: AppServerThread;
}

export class AppServerClient {
  private readonly connection: UnixWebSocketConnection;
  private readonly notifications: QueuedNotification[] = [];
  private readonly pending = new Map<number, PendingRequest>();
  private readonly waiters = new Set<NotificationWaiter>();
  private nextRequestId = 1;
  private notificationBytes = 0;
  private closedError?: Error;

  private constructor(connection: UnixWebSocketConnection) {
    this.connection = connection;
    connection.setHandlers(
      (text) => {
        this.handleMessage(text);
      },
      (error) => {
        this.handleClose(error);
      },
    );
  }

  static async connect(
    endpoint: string,
    timeoutMs = REQUEST_TIMEOUT_MS,
  ): Promise<AppServerClient> {
    const deadline = Date.now() + timeoutMs;
    const socketPath = socketPathFromEndpoint(endpoint);
    const connection = await UnixWebSocketConnection.connect(
      socketPath,
      timeoutMs,
    );
    const client = new AppServerClient(connection);
    try {
      const initialized = await client.request<unknown>(
        "initialize",
        {
          capabilities: {
            optOutNotificationMethods: [
              "item/agentMessage/delta",
              "item/commandExecution/outputDelta",
              "item/reasoning/summaryPartAdded",
              "item/reasoning/summaryTextDelta",
              "item/reasoning/textDelta",
              "thread/tokenUsage/updated",
              "turn/diff/updated",
              "turn/plan/updated",
            ],
          },
          clientInfo: {
            name: "cctools_dynamic_workflow",
            title: "Dynamic Workflow Callback",
            version: "0.2.0",
          },
        },
        Math.max(1, deadline - Date.now()),
      );
      requireCompatibleAppServer(initialized);
      client.notify("initialized", {});
      return client;
    } catch (error) {
      client.close();
      throw error;
    }
  }

  close(): void {
    this.connection.close();
  }

  notify(method: string, params: unknown): void {
    this.assertOpen();
    this.connection.sendJson({ method, params });
  }

  async request<T>(
    method: string,
    params: unknown,
    timeoutMs = REQUEST_TIMEOUT_MS,
  ): Promise<T> {
    this.assertOpen();
    const id = this.nextRequestId;
    this.nextRequestId += 1;
    const response = new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`App Server request ${method} timed out`));
      }, timeoutMs);
      this.pending.set(id, {
        reject,
        resolve: (value) => resolve(value as T),
        timer,
      });
    });
    try {
      this.connection.sendJson({ id, method, params });
    } catch (error) {
      const pending = this.pending.get(id);
      if (pending) {
        clearTimeout(pending.timer);
        this.pending.delete(id);
      }
      throw error;
    }
    return await response;
  }

  async waitForNotification(
    predicate: (notification: JsonRpcNotification) => boolean,
    timeoutMs: number,
  ): Promise<JsonRpcNotification> {
    this.assertOpen();
    const existingIndex = this.notifications.findIndex(({ notification }) =>
      predicate(notification),
    );
    if (existingIndex !== -1) {
      const queued = this.notifications.splice(existingIndex, 1)[0];
      if (queued === undefined) {
        throw new Error("Queued App Server notification disappeared");
      }
      this.notificationBytes -= queued.bytes;
      return queued.notification;
    }
    return await new Promise<JsonRpcNotification>((resolve, reject) => {
      const waiter: NotificationWaiter = {
        predicate,
        reject,
        resolve,
        timer: setTimeout(() => {
          this.waiters.delete(waiter);
          reject(new Error("Timed out waiting for App Server notification"));
        }, timeoutMs),
      };
      this.waiters.add(waiter);
    });
  }

  private assertOpen(): void {
    if (this.closedError) {
      throw this.closedError;
    }
  }

  private handleClose(error?: Error): void {
    const closed = error ?? new Error("App Server connection closed");
    this.closedError = closed;
    for (const request of this.pending.values()) {
      clearTimeout(request.timer);
      request.reject(closed);
    }
    this.pending.clear();
    for (const waiter of this.waiters) {
      clearTimeout(waiter.timer);
      waiter.reject(closed);
    }
    this.waiters.clear();
  }

  private handleMessage(text: string): void {
    let message: unknown;
    try {
      message = JSON.parse(text);
    } catch (error) {
      this.handleClose(
        new Error(`Invalid App Server JSON: ${errorMessage(error)}`),
      );
      return;
    }
    if (!isRecord(message)) {
      return;
    }
    if (typeof message.id === "number" && !message.method) {
      const pending = this.pending.get(message.id);
      if (!pending) {
        return;
      }
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      const response = message as unknown as JsonRpcResponse;
      if (response.error) {
        pending.reject(new AppServerRpcError(response.error));
      } else {
        pending.resolve(response.result);
      }
      return;
    }
    if (typeof message.method !== "string") {
      return;
    }
    // Server-initiated requests are deliberately left unanswered. The TUI is
    // another subscriber and owns approvals and user-input requests.
    if (message.id !== undefined) {
      return;
    }
    const notification = {
      method: message.method,
      ...(message.params === undefined ? {} : { params: message.params }),
    } satisfies JsonRpcNotification;
    let consumed = false;
    for (const waiter of [...this.waiters]) {
      if (!waiter.predicate(notification)) {
        continue;
      }
      consumed = true;
      this.waiters.delete(waiter);
      clearTimeout(waiter.timer);
      waiter.resolve(notification);
    }
    if (!consumed) {
      const bytes = Buffer.byteLength(text, "utf8");
      this.notifications.push({ bytes, notification });
      this.notificationBytes += bytes;
      while (
        this.notifications.length > MAX_QUEUED_NOTIFICATIONS ||
        this.notificationBytes > MAX_QUEUED_NOTIFICATION_BYTES
      ) {
        const discarded = this.notifications.shift();
        if (discarded === undefined) {
          this.notificationBytes = 0;
          break;
        }
        this.notificationBytes -= discarded.bytes;
      }
    }
  }
}

export function canonicalAppServerEndpoint(endpoint: string): string {
  const socketPath = socketPathFromEndpoint(endpoint);
  return `unix://${socketPath}`;
}

export function notificationHasClientId(
  notification: JsonRpcNotification,
  clientId: string,
): boolean {
  if (
    notification.method !== "item/started" &&
    notification.method !== "item/completed"
  ) {
    return false;
  }
  if (!isRecord(notification.params) || !isRecord(notification.params.item)) {
    return false;
  }
  return (
    notification.params.item.type === "userMessage" &&
    notification.params.item.clientId === clientId
  );
}

export function valueContainsClientId(value: unknown, clientId: string): boolean {
  return boundedJsonValueMatches(
    value,
    (item) =>
      isRecord(item) &&
      item.type === "userMessage" &&
      item.clientId === clientId,
    MAX_RESPONSE_DEPTH,
    MAX_RESPONSE_NODES,
  );
}

function truncateRpcDiagnostic(value: string): string {
  const encoded = Buffer.from(value, "utf8");
  if (encoded.length <= MAX_RPC_ERROR_MESSAGE_BYTES) {
    return value;
  }
  const suffix = "\n[truncated App Server RPC diagnostic]";
  const suffixBytes = Buffer.byteLength(suffix, "utf8");
  let end = MAX_RPC_ERROR_MESSAGE_BYTES - suffixBytes;
  while (end > 0 && ((encoded[end] as number) & 0xc0) === 0x80) {
    end -= 1;
  }
  return `${encoded.subarray(0, end).toString("utf8")}${suffix}`;
}

function socketPathFromEndpoint(endpoint: string): string {
  if (!endpoint.startsWith("unix://")) {
    throw new Error(
      "Completion callbacks currently require a local unix:// App Server endpoint",
    );
  }
  const configured = endpoint.slice("unix://".length);
  if (configured === "") {
    const codexHome = process.env.CODEX_HOME ?? path.join(homedir(), ".codex");
    return path.join(
      path.resolve(codexHome),
      "app-server-control",
      "app-server-control.sock",
    );
  }
  return path.resolve(configured);
}

function sandboxSocketError(socketDirectory: string): Error {
  return new Error(
    "The default Codex sandbox blocks the App Server callback socket. " +
      "Obtain explicit approval to run only the trusted dynamic-workflow " +
      "launcher and notifier outside the sandbox, then retry. Workers keep " +
      `their declared Codex sandboxes. Blocked socket: ${socketDirectory}`,
  );
}

function requireCompatibleAppServer(value: unknown): void {
  const userAgent = isRecord(value) ? value.userAgent : undefined;
  if (typeof userAgent !== "string") {
    throw new Error(
      "The connected Codex App Server did not report a compatible version; " +
        `Codex ${MINIMUM_APP_SERVER_VERSION_TEXT} or newer is required`,
    );
  }
  const match = userAgent.match(
    /\/(\d+)\.(\d+)\.(\d+)((?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)(?:[\s(]|$)/,
  );
  if (!match) {
    throw new Error(
      `Cannot parse the connected Codex App Server version from ${userAgent}; ` +
        `Codex ${MINIMUM_APP_SERVER_VERSION_TEXT} or newer is required`,
    );
  }
  const version = match.slice(1, 4).map(Number);
  const suffix = match[4] ?? "";
  const reportedVersion = `${version.join(".")}${suffix}`;
  const firstDifference = version.findIndex(
    (part, index) => part !== MINIMUM_APP_SERVER_VERSION[index],
  );
  const compatible =
    (firstDifference === -1 && !suffix.startsWith("-")) ||
    (version[firstDifference] ?? -1) >
      (MINIMUM_APP_SERVER_VERSION[firstDifference] ?? -1);
  if (!compatible) {
    throw new Error(
      `Connected Codex App Server ${reportedVersion} is incompatible with ` +
        `workflow callbacks; upgrade and restart Codex ${MINIMUM_APP_SERVER_VERSION_TEXT} ` +
        "or newer",
    );
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

class UnixWebSocketConnection {
  private buffer = Buffer.alloc(0);
  private closed = false;
  private closeError: Error | undefined;
  private fragmentChunks: Buffer[] = [];
  private fragmentLength = 0;
  private fragmentOpcode: number | undefined;
  private onClose?: (error?: Error) => void;
  private onMessage?: (text: string) => void;
  private readonly pendingMessages: string[] = [];

  private constructor(private readonly socket: Duplex) {
    socket.on("data", (chunk: Buffer) => {
      this.receive(chunk);
    });
    socket.on("error", (error) => {
      this.finish(error);
    });
    socket.on("end", () => {
      this.finish();
    });
    socket.on("close", () => {
      this.finish();
    });
  }

  static async connect(
    socketPath: string,
    timeoutMs: number,
  ): Promise<UnixWebSocketConnection> {
    const key = randomBytes(16).toString("base64");
    const expectedAccept = createHash("sha1")
      .update(`${key}${WEBSOCKET_GUID}`)
      .digest("base64");
    return await new Promise<UnixWebSocketConnection>((resolve, reject) => {
      let settled = false;
      const request = httpRequest({
        headers: {
          Connection: "Upgrade",
          Host: "localhost",
          "Sec-WebSocket-Key": key,
          "Sec-WebSocket-Version": "13",
          Upgrade: "websocket",
        },
        method: "GET",
        path: "/rpc",
        socketPath,
      });
      const timer = setTimeout(() => {
        request.destroy(new Error("App Server WebSocket upgrade timed out"));
      }, timeoutMs);
      const fail = (error: Error): void => {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          const code = (error as NodeJS.ErrnoException).code;
          reject(
            code === "EPERM" || code === "EACCES"
              ? sandboxSocketError(path.dirname(socketPath))
              : error,
          );
        }
      };
      request.once("error", fail);
      request.once("response", (response) => {
        response.resume();
        fail(
          new Error(
            `App Server WebSocket upgrade failed with HTTP ${response.statusCode}`,
          ),
        );
      });
      request.once("upgrade", (response, socket, head) => {
        if (response.headers["sec-websocket-accept"] !== expectedAccept) {
          socket.destroy();
          fail(new Error("App Server returned an invalid WebSocket handshake"));
          return;
        }
        if (settled) {
          socket.destroy();
          return;
        }
        settled = true;
        clearTimeout(timer);
        const connection = new UnixWebSocketConnection(socket);
        if (head.length > 0) {
          connection.receive(head);
        }
        resolve(connection);
      });
      request.end();
    });
  }

  close(): void {
    if (this.closed) {
      return;
    }
    try {
      this.socket.write(encodeClientFrame(0x8, Buffer.alloc(0)));
    } finally {
      this.socket.destroy();
      this.finish();
    }
  }

  setHandlers(
    onMessage: (text: string) => void,
    onClose: (error?: Error) => void,
  ): void {
    this.onMessage = onMessage;
    this.onClose = onClose;
    for (const message of this.pendingMessages.splice(0)) {
      onMessage(message);
    }
    if (this.closed) {
      onClose(this.closeError);
    }
  }

  sendJson(value: unknown): void {
    if (this.closed) {
      throw new Error("App Server connection is closed");
    }
    const payload = Buffer.from(JSON.stringify(value), "utf8");
    this.socket.write(encodeClientFrame(0x1, payload));
  }

  private finish(error?: Error): void {
    if (this.closed) {
      return;
    }
    this.closed = true;
    this.closeError = error;
    this.onClose?.(error);
  }

  private emitMessage(message: string): void {
    if (this.onMessage) {
      this.onMessage(message);
    } else {
      this.pendingMessages.push(message);
    }
  }

  private receive(chunk: Buffer): void {
    if (this.closed) {
      return;
    }
    this.buffer = Buffer.concat([this.buffer, chunk]);
    try {
      while (this.consumeFrame()) {
        // Consume all complete frames currently buffered.
      }
    } catch (error) {
      this.socket.destroy();
      this.finish(
        new Error(
          `Invalid App Server WebSocket frame: ${errorMessage(error)}`,
        ),
      );
    }
  }

  private consumeFrame(): boolean {
    if (this.buffer.length < 2) {
      return false;
    }
    const first = this.buffer[0] as number;
    const second = this.buffer[1] as number;
    if ((first & 0x70) !== 0) {
      throw new Error("reserved WebSocket bits are set");
    }
    const final = (first & 0x80) !== 0;
    const opcode = first & 0x0f;
    const masked = (second & 0x80) !== 0;
    if (masked) {
      throw new Error("server WebSocket frames must not be masked");
    }
    let payloadLength = second & 0x7f;
    let offset = 2;
    if (payloadLength === 126) {
      if (this.buffer.length < offset + 2) {
        return false;
      }
      payloadLength = this.buffer.readUInt16BE(offset);
      offset += 2;
    } else if (payloadLength === 127) {
      if (this.buffer.length < offset + 8) {
        return false;
      }
      const length = this.buffer.readBigUInt64BE(offset);
      if (length > BigInt(MAX_MESSAGE_BYTES)) {
        throw new Error("message exceeds the 128 MiB limit");
      }
      payloadLength = Number(length);
      offset += 8;
    }
    if (payloadLength > MAX_MESSAGE_BYTES) {
      throw new Error("message exceeds the 128 MiB limit");
    }
    if (this.buffer.length < offset + payloadLength) {
      return false;
    }
    const payload = this.buffer.subarray(offset, offset + payloadLength);
    this.buffer = this.buffer.subarray(offset + payloadLength);
    this.handleFrame(opcode, final, payload);
    return true;
  }

  private handleFrame(opcode: number, final: boolean, payload: Buffer): void {
    if (opcode >= 0x8) {
      if (!final || payload.length > 125) {
        throw new Error("invalid control frame");
      }
      if (opcode === 0x8) {
        this.socket.destroy();
        this.finish();
      } else if (opcode === 0x9) {
        this.socket.write(encodeClientFrame(0x0a, payload));
      }
      return;
    }
    if (opcode === 0x0) {
      if (this.fragmentOpcode === undefined) {
        throw new Error("unexpected continuation frame");
      }
      this.appendFragment(payload);
      if (final) {
        this.emitFragments();
      }
      return;
    }
    if (opcode !== 0x1) {
      throw new Error(`unsupported data opcode ${opcode}`);
    }
    if (this.fragmentOpcode !== undefined) {
      throw new Error("new data frame arrived during fragmentation");
    }
    if (final) {
      this.emitMessage(payload.toString("utf8"));
      return;
    }
    this.fragmentOpcode = opcode;
    this.appendFragment(payload);
  }

  private appendFragment(payload: Buffer): void {
    this.fragmentLength += payload.length;
    if (this.fragmentLength > MAX_MESSAGE_BYTES) {
      throw new Error("fragmented message exceeds the 128 MiB limit");
    }
    this.fragmentChunks.push(payload);
  }

  private emitFragments(): void {
    const payload = Buffer.concat(this.fragmentChunks, this.fragmentLength);
    this.fragmentChunks = [];
    this.fragmentLength = 0;
    this.fragmentOpcode = undefined;
    this.emitMessage(payload.toString("utf8"));
  }
}

function encodeClientFrame(opcode: number, payload: Buffer): Buffer {
  const mask = randomBytes(4);
  const extendedLength =
    payload.length < 126 ? 0 : payload.length <= 0xffff ? 2 : 8;
  const header = Buffer.alloc(2 + extendedLength + mask.length);
  header[0] = 0x80 | opcode;
  if (extendedLength === 0) {
    header[1] = 0x80 | payload.length;
  } else if (extendedLength === 2) {
    header[1] = 0x80 | 126;
    header.writeUInt16BE(payload.length, 2);
  } else {
    header[1] = 0x80 | 127;
    header.writeBigUInt64BE(BigInt(payload.length), 2);
  }
  mask.copy(header, 2 + extendedLength);
  const masked = Buffer.alloc(payload.length);
  for (let index = 0; index < payload.length; index += 1) {
    masked[index] = (payload[index] as number) ^ (mask[index % 4] as number);
  }
  return Buffer.concat([header, masked]);
}
