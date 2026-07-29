import { HUMAN_STORAGE_PREFIX, runIdFromLocation } from "./human-state";
export interface SentRecord {
  rowId: string;
  anchorId: string;
  text: string;
  at: string;
  failed?: boolean;
}
const HISTORY_NAMESPACE = "visual-brief-v2";
function key(part: "sent" | "healed"): string {
  return `${HUMAN_STORAGE_PREFIX}:${runIdFromLocation()}:${part}`;
}
export function sentStorageKey(): string { return key("sent"); }
export function healedStorageKey(): string { return key("healed"); }
export function readSentRecords(): SentRecord[] {
  const storageKey = sentStorageKey();
  const raw = readSession(storageKey);
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
  const storageKey = sentStorageKey();
  const value = JSON.stringify(records);
  writeSession(storageKey, value);
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
