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
import { answerStates, freshAnswers, rememberSeen } from "./freshness";
import {
  ancestorIds,
  awaitingThreadCount,
  defaultOpenIds,
  outline,
  type Row,
  type RowKind,
} from "./outline";
import {
  readSavedCursor,
  readSeenAnswers,
  saveCursor,
  saveSeenAnswers,
  type SeenAnswers,
} from "./session-store";
import { withTransition } from "./transitions";

/** Which full-page surface, if any, is showing. */
export type Overlay = "none" | "search" | "help";

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
  /**
   * Whether one conversation's answer arrived since the human last looked.
   *
   * Cleared by visiting it — the cursor landing on it, or it being folded or
   * unfolded — never by a timer, so an answer that lands while the human is
   * away is still marked when they come back.
   */
  isFresh: (id: string) => boolean;
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
  const states = answerStates(rows);
  const [fresh, setFresh] = createSignal<ReadonlySet<string>>(
    freshAnswers(states, readSeenAnswers()),
  );
  // Written now rather than on the way out: this page is closed by being
  // replaced, so there is no later moment to write in. What is still marked
  // new is left out, which is what keeps it marked across further reloads.
  let seen: SeenAnswers = rememberSeen(states, fresh());
  saveSeenAnswers(seen);
  const [open, setOpen] = createSignal<ReadonlySet<string>>(
    openedFor(brief, rows, restored, fresh()),
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

  /**
   * Note that the human has now seen one conversation.
   *
   * @param id - Row that was visited.
   */
  const visit = (id: string): void => {
    if (!fresh().has(id)) {
      return;
    }
    setFresh((current) => {
      const next = new Set(current);
      next.delete(id);
      return next;
    });
    seen = { ...seen, [id]: states[id] ?? "" };
    saveSeenAnswers(seen);
  };

  /**
   * Report whether a row is already the cursor, painted and reachable.
   *
   * The painted signal is read as well as the authoritative one: a move whose
   * paint is still held inside a view transition has not finished landing, and
   * treating it as settled would drop the second of two presses in one frame.
   *
   * @param id - Row to check.
   * @returns True when selecting it again would change nothing.
   */
  const settled = (id: string): boolean =>
    selected === id
    && cursorId() === id
    && ancestorIds(id).every((one) => open().has(one));

  const select = (id: string, options?: { scroll?: boolean }): void => {
    if (!rowIds.has(id)) {
      return;
    }
    const filtered = !visibleIds().has(id);
    if (filtered) {
      // The row exists but the search is hiding it, and a cursor on a row
      // that is not rendered is a cursor the human cannot see, cannot move
      // from and cannot fold. Whoever asked for this row wins; the filter is
      // the thing that gives way.
      setQueryValue("");
    }
    visit(id);
    const scroll = options?.scroll !== false;
    // Selecting the row that is already selected has to be free. Hover selects,
    // and hover fires again for every relayout, so a select that always wrote
    // state and always animated turned a stationary mouse into an endless
    // stream of view transitions — and a page mid-transition cannot be clicked
    // at all.
    if (!filtered && settled(id)) {
      if (scroll) {
        queueMicrotask(() => scrollRowIntoView(id));
      }
      return;
    }
    selected = id;
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
    isFresh: (id) => fresh().has(id),
    isOpen: (id) => open().has(id),
    toggle: (id) => {
      const wasOpen = open().has(id);
      visit(id);
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
 * @param fresh - Conversations answered since the human last looked.
 * @returns The initially expanded row ids.
 */
function openedFor(
  brief: BriefDocument,
  rows: Row[],
  cursorId: string | null,
  fresh: ReadonlySet<string>,
): Set<string> {
  const opened = defaultOpenIds(brief, rows);
  if (cursorId !== null) {
    for (const ancestor of ancestorIds(cursorId)) {
      opened.add(ancestor);
    }
  }
  // An answer nobody can see is an answer that did not arrive: a conversation
  // answered since the last look opens itself and everything holding it.
  for (const id of fresh) {
    opened.add(id);
    for (const ancestor of ancestorIds(id)) {
      opened.add(ancestor);
    }
  }
  return opened;
}
