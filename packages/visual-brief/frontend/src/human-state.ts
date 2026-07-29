import { createSignal, type Accessor } from "solid-js";
import { createStore } from "solid-js/store";
export type ChosenMap = Record<string, boolean>;
export type DraftMap = Record<string, string>;
export type SeenMap = Record<string, string>;
export type HumanStoragePart = "chosen" | "cursor" | "drafts" | "seen";
// `<run>.localhost` and `/r/<run>` are different origins. Browser storage
// cannot share these keys between the two serving modes.
export const HUMAN_STORAGE_PREFIX = "visual-brief-v2";
export interface HumanState {
  readonly chosen: ChosenMap;
  readonly cursor: Accessor<string | null>;
  readonly drafts: DraftMap;
  readonly seen: SeenMap;
  readonly draftWarning: Accessor<string>;
  choose: (rowId: string, open: boolean) => void;
  chooseAll: (rowIds: Iterable<string>, open: boolean) => void;
  select: (rowId: string | null) => void;
  writeDraft: (rowId: string, text: string) => void;
  discardDraft: (rowId: string) => void;
  visit: (threadId: string, answerState: string) => void;
}
export function runIdFromLocation(): string {
  if (typeof window === "undefined") {
    return "";
  }
  const path = /^\/r\/([^/]+)(?:\/|$)/.exec(window.location.pathname);
  if (path !== null) {
    return path[1] ?? "";
  }
  const host = window.location.hostname.toLowerCase();
  return host.endsWith(".localhost")
    ? host.slice(0, -".localhost".length)
    : "";
}
export function humanStorageKey(
  part: HumanStoragePart,
  runId: string = runIdFromLocation(),
): string {
  return `${HUMAN_STORAGE_PREFIX}:${runId}:${part}`;
}
export function createHumanState(
  runId: string = runIdFromLocation(),
): HumanState {
  const chosenKey = humanStorageKey("chosen", runId);
  const cursorKey = humanStorageKey("cursor", runId);
  const draftsKey = humanStorageKey("drafts", runId);
  const seenKey = humanStorageKey("seen", runId);
  const [chosen, setChosen] = createStore<ChosenMap>(
    readRecord(sessionStore(), chosenKey, isBoolean),
  );
  const [cursor, setCursor] = createSignal<string | null>(
    readCursor(sessionStore(), cursorKey),
  );
  const [drafts, setDrafts] = createStore<DraftMap>(
    readRecord(sessionStore(), draftsKey, isString, localStore()),
  );
  const [seen, setSeen] = createStore<SeenMap>(
    readRecord(sessionStore(), seenKey, isString),
  );
  const [draftWarning, setDraftWarning] = createSignal("");
  const saveDrafts = (): void => {
    const value = JSON.stringify({ ...drafts });
    const sessionSaved = write(sessionStore(), draftsKey, value);
    const localSaved = write(localStore(), draftsKey, value);
    setDraftWarning(
      sessionSaved && localSaved
        ? ""
        : "Draft storage is unavailable. Reloading will lose this text.",
    );
  };
  return {
    chosen,
    cursor,
    drafts,
    seen,
    draftWarning,
    choose: (rowId, open) => {
      setChosen(rowId, open);
      write(sessionStore(), chosenKey, JSON.stringify({ ...chosen }));
    },
    chooseAll: (rowIds, open) => {
      const next: ChosenMap = { ...chosen };
      for (const rowId of rowIds) {
        next[rowId] = open;
      }
      setChosen(next);
      write(sessionStore(), chosenKey, JSON.stringify(next));
    },
    select: (rowId) => {
      setCursor(rowId);
      write(sessionStore(), cursorKey, JSON.stringify(rowId));
    },
    writeDraft: (rowId, text) => {
      setDrafts(rowId, text);
      saveDrafts();
    },
    discardDraft: (rowId) => {
      setDrafts(rowId, undefined as unknown as string);
      saveDrafts();
    },
    visit: (threadId, answerState) => {
      setSeen(threadId, answerState);
      write(sessionStore(), seenKey, JSON.stringify({ ...seen }));
    },
  };
}
function readRecord<T extends string | boolean>(
  primary: Storage | null,
  key: string,
  accepts: (value: unknown) => value is T,
  fallback: Storage | null = null,
): Record<string, T> {
  const parsed = parse(read(primary, key) ?? read(fallback, key));
  if (
    parsed === null
    || typeof parsed !== "object"
    || Array.isArray(parsed)
  ) {
    return {};
  }
  const kept: Record<string, T> = {};
  for (const [name, value] of Object.entries(parsed)) {
    if (accepts(value)) {
      kept[name] = value;
    }
  }
  return kept;
}
function isString(value: unknown): value is string {
  return typeof value === "string";
}
function isBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}
function readCursor(store: Storage | null, key: string): string | null {
  const parsed = parse(read(store, key));
  return typeof parsed === "string" ? parsed : null;
}
function parse(raw: string | null): unknown {
  if (raw === null) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}
function sessionStore(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}
function localStore(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
function read(store: Storage | null, key: string): string | null {
  try {
    return store?.getItem(key) ?? null;
  } catch {
    return null;
  }
}
function write(store: Storage | null, key: string, value: string): boolean {
  try {
    if (store === null) {
      return false;
    }
    store.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}
