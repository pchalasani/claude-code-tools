/**
 * The cursor, folding and search as live application state.
 *
 * This is the half of the state the keyboard drives. It holds which row is
 * selected, which rows are expanded and what the search filter is showing,
 * and it is the only authority on all three: the browser is asked to scroll,
 * never asked where the cursor is.
 */

import { createMemo, createSignal, type Accessor } from "solid-js";

import {
  countItems,
  edgeRow,
  filterRows,
  moveByKind,
  nextAwaiting,
  restoreCursor,
  type Edge,
} from "./cursor";
import type { BriefDocument } from "./document";
import {
  ancestorIds,
  awaitingThreadCount,
  defaultOpenIds,
  outline,
  type Row,
  type RowKind,
} from "./outline";
import { withTransition } from "./transitions";

/** Which full-page surface, if any, is showing. */
export type Overlay = "none" | "search" | "help";

/** Base name the cursor is stored under, before the run is added to it. */
export const CURSOR_STORAGE_KEY = "visual-brief-cursor";

/** The navigable state of one open brief. */
export interface Navigation {
  /** The rows the current search leaves on the page. */
  visible: Accessor<Row[]>;
  /** Whether one row survives the current search. */
  isVisible: (id: string) => boolean;
  /** Look one row up by id. */
  row: (id: string) => Row | undefined;
  /** The row the cursor is painted on, one frame behind a move. */
  cursorId: Accessor<string | null>;
  /**
   * The row the cursor is on, read synchronously.
   *
   * ``cursorId`` is written inside a view transition the browser defers to
   * the next frame, so it is the paint signal and nothing else. Anything that
   * acts on the cursor — folding it, writing at it — has to read this, or it
   * acts on the row the previous key press already left.
   */
  currentId: () => string | null;
  /** Whether one row is the cursor. */
  isCursor: (id: string) => boolean;
  /** Put the cursor on a row because the pointer is over it. */
  pointAt: (id: string) => void;
  /**
   * Put the cursor on one row.
   *
   * This is the only door into the cursor, so it is the only place that can
   * guarantee the cursor is on the page. A row the current search filtered
   * away — the structure map still offers every lane, and a click still
   * reaches every rendered row — drops the search rather than leaving the
   * page with nothing marked on it.
   */
  select: (id: string, options?: { scroll?: boolean }) => void;
  /** Move the cursor between rows of one kind. */
  move: (kind: RowKind, delta: number) => void;
  /** Send the cursor to one end of the document. */
  jump: (edge: Edge) => void;
  /** Send the cursor to the next question awaiting an answer. */
  toAwaiting: () => void;
  /** Whether one row is expanded. */
  isOpen: (id: string) => boolean;
  /** Expand or fold one row. */
  toggle: (id: string) => void;
  /** Expand or fold one row explicitly. */
  setOpen: (id: string, open: boolean) => void;
  /** The current search text. */
  query: Accessor<string>;
  /**
   * Replace the search text.
   *
   * A search that filters the cursor away moves it onto the first surviving
   * match, which also expands the rows holding that match: the human must
   * never be left looking at a page with nothing marked on it.
   */
  setQuery: (value: string) => void;
  /** How many items the current search matches. */
  matchCount: Accessor<number>;
  /** Which overlay is showing. */
  overlay: Accessor<Overlay>;
  /** Show one overlay. */
  openOverlay: (overlay: Overlay) => void;
  /** Close whichever overlay is showing. */
  closeOverlay: () => void;
  /** How many question threads await an answer. */
  awaitingCount: Accessor<number>;
}

/**
 * Build the navigable state for one delivered document.
 *
 * @param brief - The document the page is showing.
 * @param onFold - Told each time a row is folded, so anything rendered inside
 *     that row can let go of it.
 * @returns The live cursor, folding and search state.
 */
export function createNavigation(
  brief: BriefDocument,
  onFold: (id: string) => void = () => undefined,
): Navigation {
  const rows = outline(brief);
  const rowIds = new Set(rows.map((row) => row.id));
  const restored = restoreCursor(rows, readSavedCursor());
  const [cursorId, setCursorId] = createSignal<string | null>(restored);
  // The authority on where the cursor is. The painted signal is written
  // inside a view transition, which the browser defers to the next frame, so
  // two keys pressed within one frame would otherwise both move from the same
  // row and one of the two presses would be lost.
  let selected: string | null = restored;
  const [open, setOpen] = createSignal<ReadonlySet<string>>(
    openedFor(brief, rows, restored),
  );
  const [query, setQueryValue] = createSignal("");
  const [overlay, setOverlay] = createSignal<Overlay>("none");

  const visible = createMemo(() => filterRows(rows, query()));
  const visibleIds = createMemo(
    () => new Set(visible().map((row) => row.id)),
  );
  const byId = new Map(rows.map((row) => [row.id, row]));
  const awaiting = awaitingThreadCount(rows);

  const expand = (id: string, current: ReadonlySet<string>): Set<string> => {
    const next = new Set(current);
    for (const ancestor of ancestorIds(id)) {
      next.add(ancestor);
    }
    return next;
  };

  const select = (id: string, options?: { scroll?: boolean }): void => {
    if (!rowIds.has(id)) {
      return;
    }
    if (!visibleIds().has(id)) {
      // The row exists but the search is hiding it, and a cursor on a row
      // that is not rendered is a cursor the human cannot see, cannot move
      // from and cannot fold. Whoever asked for this row wins; the filter is
      // the thing that gives way.
      setQueryValue("");
    }
    selected = id;
    const scroll = options?.scroll !== false;
    withTransition(() => {
      setOpen((current) => expand(id, current));
      setCursorId(id);
      if (scroll) {
        queueMicrotask(() => scrollRowIntoView(id));
      }
    });
    saveCursor(id);
  };

  const step = (kind: RowKind, delta: number): void => {
    const next = moveByKind(visible(), selected, kind, delta);
    if (next !== null) {
      select(next);
    }
  };

  const search = (value: string): void => {
    setQueryValue(value);
    const surviving = visible();
    if (selected !== null && surviving.some((row) => row.id === selected)) {
      return;
    }
    const landing = surviving.find((row) => row.kind === "item")
      ?? surviving[0];
    if (landing !== undefined) {
      select(landing.id);
    }
  };

  return {
    visible,
    isVisible: (id) => visibleIds().has(id),
    row: (id) => byId.get(id),
    cursorId,
    currentId: () => selected,
    isCursor: (id) => cursorId() === id,
    select,
    // Hover moves the cursor but never scrolls: the pointer is already looking
    // at the row, and scrolling under a moving mouse chases the page away.
    pointAt: (id) => select(id, { scroll: false }),
    move: step,
    jump: (edge) => {
      const next = edgeRow(visible(), edge);
      if (next !== null) {
        select(next);
      }
    },
    toAwaiting: () => {
      const next = nextAwaiting(visible(), selected);
      if (next !== null) {
        select(next);
        setOpen((current) => new Set(current).add(next));
      }
    },
    isOpen: (id) => open().has(id),
    toggle: (id) => {
      const wasOpen = open().has(id);
      withTransition(() =>
        setOpen((current) => {
          const next = new Set(current);
          if (!next.delete(id)) {
            next.add(id);
          }
          return next;
        }),
      );
      if (wasOpen) {
        onFold(id);
      }
    },
    setOpen: (id, wanted) => {
      setOpen((current) => {
        const next = new Set(current);
        if (wanted) {
          next.add(id);
        } else {
          next.delete(id);
        }
        return next;
      });
      if (!wanted) {
        onFold(id);
      }
    },
    query,
    setQuery: search,
    matchCount: () => countItems(visible()),
    overlay,
    openOverlay: setOverlay,
    closeOverlay: () => setOverlay("none"),
    awaitingCount: () => awaiting,
  };
}

/**
 * Return the key this page's cursor is remembered under.
 *
 * The daemon serves one run at two addresses — ``<run>.localhost/`` and
 * ``localhost/r/<run>/`` — so the key carries the run's id rather than the
 * address it was reached through: opening the same run the other way restores
 * the same place, and a tab pointed at a different run does not adopt it.
 *
 * @returns The session-storage key for the run being shown.
 */
export function cursorStorageKey(): string {
  return `${CURSOR_STORAGE_KEY}:${runIdFromLocation()}`;
}

/**
 * Read the run's id out of the address this page was opened at.
 *
 * @returns The run id, or an empty string when the address names no run.
 */
function runIdFromLocation(): string {
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
 * Read the row the cursor was on before the page last reloaded.
 *
 * @returns The saved row id, or null when there is none.
 */
export function readSavedCursor(): string | null {
  try {
    return window.sessionStorage.getItem(cursorStorageKey());
  } catch {
    return null;
  }
}

/**
 * Remember the row the cursor is on, so a reload can restore it.
 *
 * @param id - The row id to remember.
 */
export function saveCursor(id: string): void {
  try {
    window.sessionStorage.setItem(cursorStorageKey(), id);
  } catch {
    // Storage can be disabled; the cursor still works within this page.
  }
}

/**
 * Bring one row into comfortable reading position.
 *
 * The row carries a generous scroll margin, so the page moves around the
 * cursor rather than pinning the cursor to an edge of the window.
 *
 * @param id - Row id to scroll to.
 */
function scrollRowIntoView(id: string): void {
  if (typeof document === "undefined") {
    return;
  }
  const row = document.querySelector(`[data-row-id=${JSON.stringify(id)}]`);
  const head = row?.querySelector(".row-head");
  if (head instanceof Element && typeof head.scrollIntoView === "function") {
    head.scrollIntoView({ block: "nearest" });
  }
}

/**
 * Choose the rows that are expanded when the page opens.
 *
 * @param brief - The delivered document.
 * @param rows - The document's rows.
 * @param cursorId - Row the cursor was restored to.
 * @returns The initially expanded row ids.
 */
function openedFor(
  brief: BriefDocument,
  rows: Row[],
  cursorId: string | null,
): Set<string> {
  const opened = defaultOpenIds(brief, rows);
  if (cursorId !== null) {
    for (const ancestor of ancestorIds(cursorId)) {
      opened.add(ancestor);
    }
  }
  return opened;
}
