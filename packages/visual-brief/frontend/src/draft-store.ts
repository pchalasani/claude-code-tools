/**
 * Per-row message drafts remembered for the life of this browser tab.
 *
 * The rest of the page's irreplaceable reader state uses session storage,
 * keyed by run. Drafts use that same boundary: a reload of this run restores
 * them, another run cannot inherit them, and closing the tab ends the session.
 */

import { runIdFromLocation } from "./session-store";

/** Base name for the run's row drafts. */
export const DRAFTS_STORAGE_KEY = "visual-brief-drafts";

/** Text being composed, keyed by the row that owns it. */
export type Drafts = Record<string, string>;

/** Return the storage key for the run being shown. */
export function draftsStorageKey(): string {
  return `${DRAFTS_STORAGE_KEY}:${runIdFromLocation()}`;
}

/**
 * Read every usable draft saved for this run.
 *
 * @returns The saved row-to-text map, or an empty map when storage is absent.
 */
export function readDrafts(): Drafts {
  try {
    const raw = window.sessionStorage.getItem(draftsStorageKey());
    if (raw === null || raw === "") {
      return {};
    }
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object") {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).filter(
        ([rowId, text]) => rowId !== "" && typeof text === "string",
      ),
    );
  } catch {
    return {};
  }
}

/**
 * Save every draft for this run.
 *
 * @param drafts - Complete row-to-text map.
 */
export function saveDrafts(drafts: Drafts): void {
  try {
    window.sessionStorage.setItem(
      draftsStorageKey(),
      JSON.stringify(drafts),
    );
  } catch {
    // A disabled store costs reload survival, not the composer in this load.
  }
}
