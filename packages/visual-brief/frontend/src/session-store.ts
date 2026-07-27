/**
 * The little the page remembers about itself across its own reloads.
 *
 * The agent rewrites this page whenever it publishes, so every reload is an
 * amnesia event. One session-storage record, keyed by the run rather than by
 * the address the run was reached through, carries the human's place forward:
 * where the cursor was, and which answers they had already seen. Both live
 * here so there is exactly one store to reason about — a second one would
 * drift out of step with the first at the first bug.
 *
 * Exactly one fact is kept twice, and it earns it: whether this tab has
 * already reloaded itself to escape a page it could not read. Session storage
 * is where that belongs and is also the first thing a browser withdraws, and
 * it withdraws it silently. Everything else here is worth losing on a page
 * that has no storage; this one costs a reload loop, so the page's own history
 * entry carries a copy.
 */

/** Base name the cursor is stored under, before the run is added to it. */
export const CURSOR_STORAGE_KEY = "visual-brief-cursor";

/** Base name the seen answers are stored under. */
export const SEEN_STORAGE_KEY = "visual-brief-seen";

/** Base name the unanswered submissions are stored under. */
export const SENT_STORAGE_KEY = "visual-brief-sent";

/** Base name the self-healing reload is remembered under. */
export const HEALED_STORAGE_KEY = "visual-brief-healed";

/**
 * Property the page's history entry carries its fallback memory under.
 *
 * Namespaced because the history entry belongs to the page rather than to this
 * module: anything else that ever writes to it has to find its own state again
 * where it left it.
 */
export const HISTORY_NAMESPACE = "visual-brief";

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
  // Two stores that fail independently, exactly as for healed generations:
  // a browser that silently takes session storage away must not also take
  // the human's waiting sign and their way back to the conversation.
  const key = sentStorageKey();
  const raw = readItem(key) ?? readHistoryItem(key);
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
  const key = sentStorageKey();
  const value = JSON.stringify(records);
  writeItem(key, value);
  writeHistoryItem(key, value);
}

/**
 * Read the page generation this tab last reloaded itself to escape.
 *
 * A generation that was stored as an empty string is a memory like any other,
 * not an absence. A page served without a generation of its own is exactly the
 * page that heals — it is unintelligible to the daemon by definition — and
 * reading its memory back as "never happened" is what made such a page reload
 * on every single load rather than once.
 *
 * @returns The generation, or null when this tab has never had to.
 */
export function readHealedGeneration(): string | null {
  const key = healedStorageKey();
  const stored = readItem(key);
  return stored !== null ? stored : readHistoryItem(key);
}

/**
 * Remember that this tab reloaded itself to escape one page generation.
 *
 * It is written twice, to two stores that fail independently. Session storage
 * is the right home for it and is also the first thing a browser takes away —
 * switched off, full, or blocked by policy — and it takes it away silently.
 * Losing this particular memory is not a small loss: the page reloads to
 * escape a state it cannot read, comes back into the same state, and reloads
 * again, for as long as the tab is open.
 *
 * @param generation - The generation the tab was showing when it gave up on
 *     understanding the daemon.
 */
export function rememberHealedGeneration(generation: string): void {
  const key = healedStorageKey();
  writeItem(key, generation);
  writeHistoryItem(key, generation);
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

/**
 * Read one value out of the history entry this page came back to.
 *
 * @param key - The key to read.
 * @returns The stored value, or null when there is none.
 */
function readHistoryItem(key: string): string | null {
  try {
    const state = asRecord(window.history.state);
    const carried = asRecord(state?.[HISTORY_NAMESPACE]);
    const value = carried?.[key];
    return typeof value === "string" ? value : null;
  } catch {
    return null;
  }
}

/**
 * Write one value into the history entry, alongside whatever it already holds.
 *
 * The state a history entry carries survives ``location.reload()`` the same
 * way session storage does, and is refused under different circumstances, so
 * a page denied one store still remembers through the other.
 *
 * @param key - The key to write.
 * @param value - The value to store.
 */
function writeHistoryItem(key: string, value: string): void {
  try {
    const state = { ...(asRecord(window.history.state) ?? {}) };
    const carried = { ...(asRecord(state[HISTORY_NAMESPACE]) ?? {}) };
    carried[key] = value;
    state[HISTORY_NAMESPACE] = carried;
    window.history.replaceState(state, "");
  } catch {
    // No history to write to either; the page still works within this load.
  }
}

/**
 * Read a value as a plain record, when that is what it turns out to be.
 *
 * @param value - Anything at all.
 * @returns The value as a record, or null when it is not one.
 */
function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}
