import { createHash, randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { constants } from "node:fs";
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
  return JSON.parse(JSON.stringify(value)) as JsonValue;
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
): Promise<void> {
  await mkdir(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await rename(temporary, filePath);
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

export function isPidRunning(pid: number | undefined): boolean {
  if (pid === undefined) {
    return false;
  }
  try {
    process.kill(pid, 0);
    if (process.platform !== "win32") {
      const state = spawnSync("ps", ["-o", "stat=", "-p", String(pid)], {
        encoding: "utf8",
      });
      const processState = state.stdout.trim();
      if (
        state.status !== 0 ||
        processState === "" ||
        processState.startsWith("Z")
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
  if (!isPidRunning(pid)) {
    return undefined;
  }
  const result =
    process.platform === "win32"
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
  return identity === "" ? undefined : identity;
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
