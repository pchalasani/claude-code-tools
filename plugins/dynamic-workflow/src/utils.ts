import { createHash, randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { constants, readdirSync, readFileSync } from "node:fs";
import {
  access,
  mkdir,
  readFile,
  rename,
  writeFile,
} from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";

import type { JsonValue } from "./types.js";

const MAX_DURABLE_JSON_DEPTH = 128;
const MAX_DURABLE_JSON_NODES = 250_000;
const MAX_DURABLE_VALUE_BYTES = 1_000_000;

export class JsonStructureLimitError extends RangeError {
  constructor(message: string) {
    super(message);
    this.name = "JsonStructureLimitError";
  }
}

const DARWIN_PROCESS_START_SCRIPT = String.raw`
function run(argv) {
  ObjC.import("Foundation");
  ObjC.bindFunction("proc_pidinfo", [
    "int",
    ["int", "int", "uint64", "void *", "int"],
  ]);
  const data = $.NSMutableData.dataWithLength(136);
  const size = $.proc_pidinfo(Number(argv[0]), 3, 0, data.mutableBytes, 136);
  if (size !== 136) {
    return "";
  }
  return ObjC.unwrap(data.base64EncodedStringWithOptions(0));
}
`;

const DARWIN_PROCESS_GROUP_SCRIPT = String.raw`
function run(argv) {
  ObjC.import("Foundation");
  ObjC.bindFunction("proc_listpids", [
    "int",
    ["uint32", "uint32", "void *", "int"],
  ]);
  const data = $.NSMutableData.dataWithLength(1024 * 1024);
  const size = $.proc_listpids(
    2,
    Number(argv[0]),
    data.mutableBytes,
    1024 * 1024,
  );
  if (size < 0) {
    return "";
  }
  data.length = size;
  return ObjC.unwrap(data.base64EncodedStringWithOptions(0));
}
`;

export function nowIso(): string {
  return new Date().toISOString();
}

export function createRunId(): string {
  const stamp = new Date().toISOString().replaceAll(/[-:.TZ]/g, "");
  return `${stamp}-${randomUUID().slice(0, 8)}`;
}

export function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

export function stableStringify(value: unknown): string {
  const seen = new WeakSet<object>();

  function normalize(item: unknown): unknown {
    if (item === null || typeof item !== "object") {
      return item;
    }
    if (seen.has(item)) {
      throw new TypeError("Cannot serialize a circular value");
    }
    seen.add(item);
    if (Array.isArray(item)) {
      const normalized = item.map(normalize);
      seen.delete(item);
      return normalized;
    }
    const normalized = Object.fromEntries(
      Object.entries(item)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, child]) => [key, normalize(child)]),
    );
    seen.delete(item);
    return normalized;
  }

  return JSON.stringify(normalize(value));
}

export function toJsonValue(value: unknown): JsonValue {
  if (value === undefined) {
    return null;
  }
  const serialized = boundedJsonStringify(
    value,
    MAX_DURABLE_VALUE_BYTES,
    MAX_DURABLE_JSON_DEPTH,
  );
  return JSON.parse(serialized) as JsonValue;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export async function sleep(
  milliseconds: number,
  signal?: AbortSignal,
): Promise<void> {
  if (signal?.aborted) {
    throw signal.reason;
  }
  await new Promise<void>((resolve, reject) => {
    const finish = (): void => {
      signal?.removeEventListener("abort", abort);
      resolve();
    };
    const timer = setTimeout(finish, milliseconds);
    const abort = (): void => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    signal?.addEventListener("abort", abort, { once: true });
  });
}

export async function atomicWriteJson(
  filePath: string,
  value: unknown,
  maximumBytes = Number.MAX_SAFE_INTEGER,
): Promise<void> {
  await mkdir(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  const serialized = boundedJsonStringify(
    value,
    maximumBytes - 1,
    MAX_DURABLE_JSON_DEPTH,
  );
  await writeFile(temporary, `${serialized}\n`, "utf8");
  await rename(temporary, filePath);
}

export function boundedJsonStringify(
  value: unknown,
  maximumBytes: number,
  maximumDepth = MAX_DURABLE_JSON_DEPTH,
  maximumNodes = MAX_DURABLE_JSON_NODES,
): string {
  if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 0) {
    throw new RangeError("Maximum JSON byte length must be a safe integer");
  }
  if (!Number.isSafeInteger(maximumDepth) || maximumDepth < 0) {
    throw new RangeError("Maximum JSON depth must be a safe integer");
  }
  if (!Number.isSafeInteger(maximumNodes) || maximumNodes < 1) {
    throw new RangeError("Maximum JSON node count must be a positive integer");
  }
  const depths = new WeakMap<object, number>();
  let nodes = 0;
  const serialized = JSON.stringify(value, function (key, item: unknown) {
    nodes += 1;
    if (nodes > maximumNodes) {
      throw new JsonStructureLimitError(
        `JSON value exceeds the maximum node count of ${maximumNodes}`,
      );
    }
    const parentDepth = key === "" ? -1 : (depths.get(this) ?? -1);
    const depth = parentDepth + 1;
    if (item !== null && typeof item === "object") {
      if (depth >= maximumDepth) {
        throw new JsonStructureLimitError(
          `JSON value exceeds the maximum depth of ${maximumDepth}`,
        );
      }
      depths.set(item, depth);
    }
    return item;
  });
  if (serialized === undefined) {
    throw new TypeError("Value is not JSON serializable");
  }
  if (Buffer.byteLength(serialized, "utf8") > maximumBytes) {
    throw new RangeError(
      `JSON value exceeds the ${maximumBytes}-byte durable limit`,
    );
  }
  return serialized;
}

export function assertBoundedJsonStructure(
  root: unknown,
  maximumDepth: number,
  maximumNodes: number,
): void {
  boundedJsonValueMatches(
    root,
    () => false,
    maximumDepth,
    maximumNodes,
  );
}

export function boundedJsonValueMatches(
  root: unknown,
  predicate: (value: unknown) => boolean,
  maximumDepth: number,
  maximumNodes: number,
): boolean {
  if (!Number.isSafeInteger(maximumDepth) || maximumDepth < 0) {
    throw new RangeError("Maximum JSON depth must be a safe integer");
  }
  if (!Number.isSafeInteger(maximumNodes) || maximumNodes < 1) {
    throw new RangeError("Maximum JSON node count must be a positive integer");
  }
  const stack: Array<{ depth: number; value: unknown }> = [
    { depth: 0, value: root },
  ];
  let found = false;
  let nodes = 0;

  while (stack.length > 0) {
    const frame = stack.pop() as { depth: number; value: unknown };
    nodes += 1;
    if (nodes > maximumNodes) {
      throw new JsonStructureLimitError(
        `JSON value exceeds the maximum node count of ${maximumNodes}`,
      );
    }
    const item = frame.value;
    found ||= predicate(item);
    if (item === null || typeof item !== "object") {
      continue;
    }
    if (frame.depth >= maximumDepth) {
      throw new JsonStructureLimitError(
        `JSON value exceeds the maximum depth of ${maximumDepth}`,
      );
    }
    const children = Array.isArray(item) ? item : Object.values(item);
    if (children.length > maximumNodes - nodes - stack.length) {
      throw new JsonStructureLimitError(
        `JSON value exceeds the maximum node count of ${maximumNodes}`,
      );
    }
    for (let index = children.length - 1; index >= 0; index -= 1) {
      stack.push({
        depth: frame.depth + 1,
        value: children[index],
      });
    }
  }
  return found;
}

export async function readJson<T>(filePath: string): Promise<T> {
  return JSON.parse(await readFile(filePath, "utf8")) as T;
}

export async function fileExists(filePath: string): Promise<boolean> {
  try {
    await access(filePath, constants.F_OK);
    return true;
  } catch {
    return false;
  }
}

export function workflowHome(): string {
  const configured = process.env.CODEX_WORKFLOW_HOME;
  return path.resolve(configured ?? path.join(homedir(), ".codex", "workflows"));
}

export function isDeadPosixProcessState(state: string): boolean {
  return /^[ZXx]/.test(state);
}

export function processGroupIsRunning(pid: number): boolean {
  try {
    process.kill(process.platform === "win32" ? pid : -pid, 0);
  } catch {
    return false;
  }
  if (process.platform === "win32") {
    return true;
  }
  if (process.platform === "darwin") {
    const members = spawnSync(
      "/usr/bin/osascript",
      [
        "-l",
        "JavaScript",
        "-e",
        DARWIN_PROCESS_GROUP_SCRIPT,
        String(pid),
      ],
      { encoding: "utf8", windowsHide: true },
    );
    if (members.status !== 0 || members.stdout.trim() === "") {
      return true;
    }
    const pids = Buffer.from(members.stdout.trim(), "base64");
    for (let offset = 0; offset + 4 <= pids.length; offset += 4) {
      const memberPid = pids.readInt32LE(offset);
      if (
        memberPid > 0 &&
        processStartIdentity(memberPid) !== undefined
      ) {
        return true;
      }
    }
    return false;
  }
  if (process.platform !== "linux") {
    const processes = spawnSync("ps", ["-axo", "pgid=,stat="], {
      encoding: "utf8",
    });
    if (processes.status !== 0) {
      return true;
    }
    return processes.stdout.split("\n").some((line) => {
      const match = line.match(/^\s*(\d+)\s+(\S+)/);
      return (
        match !== null &&
        Number(match[1]) === pid &&
        !isDeadPosixProcessState(match[2] ?? "")
      );
    });
  }
  try {
    for (const entry of readdirSync("/proc")) {
      if (!/^\d+$/.test(entry)) {
        continue;
      }
      let stat: string;
      try {
        stat = readFileSync(`/proc/${entry}/stat`, "utf8");
      } catch {
        continue;
      }
      const commandEnd = stat.lastIndexOf(")");
      if (commandEnd < 0) {
        continue;
      }
      const fields = stat.slice(commandEnd + 1).trim().split(/\s+/);
      if (
        Number(fields[2]) === pid &&
        !isDeadPosixProcessState(fields[0] ?? "")
      ) {
        return true;
      }
    }
    return false;
  } catch {
    return true;
  }
}

export function isPidRunning(pid: number | undefined): boolean {
  if (pid === undefined) {
    return false;
  }
  try {
    process.kill(pid, 0);
    if (process.platform === "darwin" || process.platform === "linux") {
      return processStartIdentity(pid) !== undefined;
    }
    if (process.platform !== "win32") {
      const state = spawnSync("ps", ["-o", "stat=", "-p", String(pid)], {
        encoding: "utf8",
      });
      const processState = state.stdout.trim();
      if (
        state.status !== 0 ||
        processState === "" ||
        isDeadPosixProcessState(processState)
      ) {
        return false;
      }
    }
    return true;
  } catch {
    return false;
  }
}

export function processStartIdentity(pid: number): string | undefined {
  if (pid <= 0) {
    return undefined;
  }
  if (process.platform === "linux") {
    try {
      const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
      const bootId = readFileSync(
        "/proc/sys/kernel/random/boot_id",
        "utf8",
      ).trim();
      const commandEnd = stat.lastIndexOf(")");
      if (commandEnd < 0) {
        return undefined;
      }
      const fields = stat.slice(commandEnd + 1).trim().split(/\s+/);
      const state = fields[0];
      const kernelStartedAt = fields[19];
      if (!bootId || !kernelStartedAt || isDeadPosixProcessState(state ?? "")) {
        return undefined;
      }
      return `linux:${bootId}:${kernelStartedAt}`;
    } catch {
      return undefined;
    }
  }
  const darwin = process.platform === "darwin";
  if (!darwin && !isPidRunning(pid)) {
    return undefined;
  }
  const result = darwin
    ? spawnSync(
        "/usr/bin/osascript",
        ["-l", "JavaScript", "-e", DARWIN_PROCESS_START_SCRIPT, String(pid)],
        { encoding: "utf8", windowsHide: true },
      )
    : process.platform === "win32"
    ? spawnSync(
        "powershell.exe",
        [
          "-NoProfile",
          "-NonInteractive",
          "-Command",
          `(Get-Process -Id ${pid}).StartTime.ToUniversalTime().Ticks`,
        ],
        { encoding: "utf8", windowsHide: true },
      )
    : spawnSync("ps", ["-o", "lstart=", "-p", String(pid)], {
        encoding: "utf8",
      });
  if (result.status !== 0) {
    return undefined;
  }
  const identity = result.stdout.trim();
  if (identity === "") {
    return undefined;
  }
  if (!darwin) {
    return identity;
  }
  const processInfo = Buffer.from(identity, "base64");
  if (processInfo.length !== 136 || processInfo.readUInt32LE(4) === 5) {
    return undefined;
  }
  const startSeconds = processInfo.readBigUInt64LE(120);
  const startMicroseconds = processInfo.readBigUInt64LE(128);
  return `darwin:${startSeconds}:${startMicroseconds}`;
}

export function processIdentityMatches(
  pid: number | undefined,
  expectedStartedAt: string | undefined,
): boolean {
  return (
    pid !== undefined &&
    expectedStartedAt !== undefined &&
    processStartIdentity(pid) === expectedStartedAt
  );
}

export function safeIdentifier(value: string): string {
  const cleaned = value.replaceAll(/[^A-Za-z0-9_.\/-]+/g, "-");
  return cleaned.replaceAll(/^[-/.]+|[-/.]+$/g, "") || "step";
}
