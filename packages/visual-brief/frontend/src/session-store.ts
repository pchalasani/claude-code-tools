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

/** What each conversation looked like the last time the human saw it. */
export type SeenAnswers = Record<string, string>;

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
