/**
 * The little the page remembers about itself across its own reloads.
 *
 * The agent rewrites this page whenever it publishes, so every reload is an
 * amnesia event. One session-storage record, keyed by the run rather than by
 * the address the run was reached through, carries the human's place forward:
 * where the cursor was, and which answers they had already seen. Both live
 * here so there is exactly one store to reason about — a second one would
 * drift out of step with the first at the first bug.
 */

/** Base name the cursor is stored under, before the run is added to it. */
export const CURSOR_STORAGE_KEY = "visual-brief-cursor";

/** Base name the seen answers are stored under. */
export const SEEN_STORAGE_KEY = "visual-brief-seen";

/** Base name the unanswered submissions are stored under. */
export const SENT_STORAGE_KEY = "visual-brief-sent";

/** Base name the self-healing reload is remembered under. */
export const HEALED_STORAGE_KEY = "visual-brief-healed";

/** What each conversation looked like the last time the human saw it. */
export type SeenAnswers = Record<string, string>;

/**
 * One message this page sent that has not been seen landing yet.
 *
 * It is remembered by what the queue line says — the verbatim text and the
 * timestamp the daemon wrote — rather than by any id the page made up, because
 * the id a queued question is folded under is chosen by the daemon and changes
 * the moment the question becomes a saved conversation.
 */
export interface SentRecord {
  /** Row it was written at. */
  rowId: string;
  /** Anchor path it was attached to. */
  anchorId: string;
  /** Exactly what was sent. */
  text: string;
  /** Timestamp the daemon recorded, or empty when it did not say. */
  at: string;
  /** How many page loads it has survived without being found. */
  loads: number;
}

/**
 * Read the run's id out of the address this page was opened at.
 *
 * The daemon serves one run at two addresses — ``<run>.localhost/`` and
 * ``localhost/r/<run>/`` — so anything remembered is keyed by the run's id
 * rather than by the address: opening the same run the other way restores the
 * same place, and a tab pointed at a different run does not adopt it.
 *
 * @returns The run id, or an empty string when the address names no run.
 */
export function runIdFromLocation(): string {
  if (typeof window === "undefined") {
    return "";
  }
  const fromPath = /^\/r\/([^/]+)\//.exec(window.location?.pathname ?? "");
  if (fromPath !== null) {
    return fromPath[1] ?? "";
  }
  const host = (window.location?.hostname ?? "").toLowerCase();
  const suffix = ".localhost";
  return host.endsWith(suffix) ? host.slice(0, -suffix.length) : "";
}

/**
 * Return the key this page's cursor is remembered under.
 *
 * @returns The session-storage key for the run being shown.
 */
export function cursorStorageKey(): string {
  return `${CURSOR_STORAGE_KEY}:${runIdFromLocation()}`;
}

/**
 * Return the key this page's seen answers are remembered under.
 *
 * @returns The session-storage key for the run being shown.
 */
export function seenStorageKey(): string {
  return `${SEEN_STORAGE_KEY}:${runIdFromLocation()}`;
}

/**
 * Read the row the cursor was on before the page last reloaded.
 *
 * @returns The saved row id, or null when there is none.
 */
export function readSavedCursor(): string | null {
  return readItem(cursorStorageKey());
}

/**
 * Remember the row the cursor is on, so a reload can restore it.
 *
 * @param id - The row id to remember.
 */
export function saveCursor(id: string): void {
  writeItem(cursorStorageKey(), id);
}

/**
 * Read what every conversation looked like when the human last looked.
 *
 * A page that has never been looked at answers null rather than an empty
 * record, and the difference matters: nothing is new on a first look, while
 * everything answered since a real look is.
 *
 * @returns The remembered conversations, or null when there is no record.
 */
export function readSeenAnswers(): SeenAnswers | null {
  const raw = readItem(seenStorageKey());
  if (raw === null || raw === "") {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object") {
      return null;
    }
    return parsed as SeenAnswers;
  } catch {
    return null;
  }
}

/**
 * Remember what every conversation looked like as of this look.
 *
 * @param seen - The conversations and their states.
 */
export function saveSeenAnswers(seen: SeenAnswers): void {
  writeItem(seenStorageKey(), JSON.stringify(seen));
}

/**
 * Return the key this page's unanswered submissions are remembered under.
 *
 * @returns The session-storage key for the run being shown.
 */
export function sentStorageKey(): string {
  return `${SENT_STORAGE_KEY}:${runIdFromLocation()}`;
}

/**
 * Return the key this page's self-healing reload is remembered under.
 *
 * @returns The session-storage key for the run being shown.
 */
export function healedStorageKey(): string {
  return `${HEALED_STORAGE_KEY}:${runIdFromLocation()}`;
}

/**
 * Read the messages this tab sent and has not yet seen on the page.
 *
 * Anything unreadable is treated as nothing at all: a waiting sign is worth
 * having, but not at the price of a page that will not start.
 *
 * @returns The remembered submissions, oldest first.
 */
export function readSentRecords(): SentRecord[] {
  const raw = readItem(sentStorageKey());
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

/**
 * Remember the messages this tab is still waiting to see land.
 *
 * @param records - The submissions to carry into the next page load.
 */
export function saveSentRecords(records: SentRecord[]): void {
  writeItem(sentStorageKey(), JSON.stringify(records));
}

/**
 * Read the page generation this tab last reloaded itself to escape.
 *
 * @returns The generation, or null when this tab has never had to.
 */
export function readHealedGeneration(): string | null {
  const raw = readItem(healedStorageKey());
  return raw === null || raw === "" ? null : raw;
}

/**
 * Remember that this tab reloaded itself to escape one page generation.
 *
 * @param generation - The generation the tab was showing when it gave up on
 *     understanding the daemon.
 */
export function rememberHealedGeneration(generation: string): void {
  writeItem(healedStorageKey(), generation);
}

/**
 * Report whether one stored value is a submission this page can act on.
 *
 * @param value - A parsed record from storage.
 * @returns True when every field is the kind of value it should be.
 */
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
    && typeof record.loads === "number"
  );
}

/**
 * Read one session-storage value, treating a disabled store as empty.
 *
 * @param key - The key to read.
 * @returns The stored value, or null when there is none.
 */
function readItem(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

/**
 * Write one session-storage value, tolerating a disabled store.
 *
 * @param key - The key to write.
 * @param value - The value to store.
 */
function writeItem(key: string, value: string): void {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // Storage can be disabled; the page still works within this one load.
  }
}
