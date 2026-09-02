import { humanStorageKey } from "./human-state";
export interface SentRecord {
  rowId: string;
  anchorId: string;
  text: string;
  at: string;
  displayAt?: string;
  after?: number;
  failed?: boolean;
}
export interface AcceptedSignalRecord {
  baseline: string | null;
  at?: string;
  signal?: string;
  text?: string;
}
export type AcceptedSignalWork = Record<string, AcceptedSignalRecord>;
export type StoredAcceptedSignalWork = Record<
  string,
  AcceptedSignalRecord | string | null
>;
const HISTORY_NAMESPACE = "visual-brief-v2";
export function sentStorageKey(): string { return humanStorageKey("sent"); }
export function healedStorageKey(): string { return humanStorageKey("healed"); }
export function signalWorkStorageKey(): string {
  return humanStorageKey("signal-work");
}
export function readSentRecords(): SentRecord[] {
  const raw = readSession(sentStorageKey());
  if (raw === null || raw === "") {
    return [];
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isSentRecord) : [];
  } catch {
    return [];
  }
}
export function saveSentRecords(records: SentRecord[]): void {
  writeSession(sentStorageKey(), JSON.stringify(records));
}
export function readAcceptedSignalWork(): AcceptedSignalWork {
  const raw = readSession(signalWorkStorageKey());
  if (raw === null || raw === "") {
    return {};
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      parsed === null
      || typeof parsed !== "object"
      || Array.isArray(parsed)
    ) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).flatMap(([rowId, value]) => {
        const record = acceptedSignalRecord(value);
        return rowId === "" || record === null ? [] : [[rowId, record]];
      }),
    );
  } catch {
    return {};
  }
}
export function saveAcceptedSignalWork(work: StoredAcceptedSignalWork): void {
  writeSession(signalWorkStorageKey(), JSON.stringify(work));
}
function acceptedSignalRecord(value: unknown): AcceptedSignalRecord | null {
  if (typeof value === "string" || value === null) {
    return { baseline: value };
  }
  if (typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (
    typeof record.baseline !== "string"
    && record.baseline !== null
  ) {
    return null;
  }
  if (record.signal !== undefined && typeof record.signal !== "string") {
    return null;
  }
  if (record.at !== undefined && typeof record.at !== "string") {
    return null;
  }
  if (record.text !== undefined && typeof record.text !== "string") {
    return null;
  }
  return {
    baseline: record.baseline,
    ...(record.at === undefined ? {} : { at: record.at }),
    ...(record.signal === undefined ? {} : { signal: record.signal }),
    ...(record.text === undefined ? {} : { text: record.text }),
  };
}
export function readHealedStandoff(): string | null {
  const storageKey = healedStorageKey();
  return readSession(storageKey) ?? readHistory(storageKey);
}
export function rememberHealedStandoff(standoff: string): void {
  const storageKey = healedStorageKey();
  writeSession(storageKey, standoff);
  writeHistory(storageKey, standoff);
}
function isSentRecord(value: unknown): value is SentRecord {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.rowId === "string"
    && typeof record.anchorId === "string"
    && typeof record.text === "string"
    && typeof record.at === "string"
    && (record.displayAt === undefined || typeof record.displayAt === "string")
    && (record.after === undefined || (
      Number.isSafeInteger(record.after) && Number(record.after) >= 0
    ))
    && (record.failed === undefined || typeof record.failed === "boolean")
  );
}
function readSession(storageKey: string): string | null {
  try {
    return window.sessionStorage.getItem(storageKey);
  } catch {
    return null;
  }
}
function writeSession(storageKey: string, value: string): void {
  try {
    window.sessionStorage.setItem(storageKey, value);
  } catch {
  }
}
function readHistory(storageKey: string): string | null {
  try {
    const state = asRecord(window.history.state);
    const held = asRecord(state?.[HISTORY_NAMESPACE]);
    const value = held?.[storageKey];
    return typeof value === "string" ? value : null;
  } catch {
    return null;
  }
}
function writeHistory(storageKey: string, value: string): void {
  try {
    const state = { ...(asRecord(window.history.state) ?? {}) };
    const held = { ...(asRecord(state[HISTORY_NAMESPACE]) ?? {}) };
    held[storageKey] = value;
    state[HISTORY_NAMESPACE] = held;
    window.history.replaceState(state, "");
  } catch {
  }
}
function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object"
    ? value as Record<string, unknown>
    : null;
}
