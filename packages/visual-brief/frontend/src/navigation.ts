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
  countChats,
  countItems,
  edgeRow,
  filterRows,
  moveByKind,
  nextAwaiting,
  restoreCursor,
  chatRows,
  type Edge,
} from "./cursor";
import type { BriefDocument } from "./document";
import { itemOrdinals, openedFor, paintedRows } from "./folding";
import { createFreshness } from "./freshness";
import {
  ancestorIds,
  awaitingThreadCount,
  outline,
  type Row,
  type RowKind,
} from "./outline";
import { revealRowSoon, scrollRowIntoView } from "./reveal";
import { readSavedCursor, saveCursor } from "./session-store";
import { withTransition } from "./transitions";
import { createViewModes } from "./view-modes";

/** Which full-page surface, if any, is showing. */
export type Overlay = "none" | "search" | "help";

/** The navigable state of one open brief. */
export interface Navigation {
  /** The rows the current search and view leave on the page. */
  visible: Accessor<Row[]>;
  /** The rows the page is actually painting, containers included. */
  painted: Accessor<Row[]>;
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
   * guarantee the cursor is on the page. A row a filter hid — the structure
   * map still offers every lane, and a click still reaches every rendered row
   * — drops that filter rather than leaving the page with nothing marked.
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
  /** Open every row, to the most granular level there is. */
  expandAll: () => void;
  /** Fold the whole page back to its lanes. */
  collapseAll: () => void;
  /** Whether the page is showing only the human's own conversations. */
  chats: Accessor<boolean>;
  /** Show, or stop showing, only the human's own conversations. */
  toggleChats: () => void;
  /** How many conversations the human has written in. */
  chatCount: Accessor<number>;
  /** One painted item's position on the page, for citing it by number. */
  ordinal: (id: string) => number | null;
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
  /**
   * Bring the row this page load was anchored to into reading position.
   *
   * Only a load that followed the human's own message carries an anchor, so
   * this scrolls exactly once, and only to where they were already looking.
   */
  revealAnchor: () => void;
}

/**
 * Build the navigable state for one delivered document.
 *
 * @param brief - The document the page is showing.
 * @param onFold - Told each time a row is folded, so anything rendered inside
 *     that row can let go of it.
 * @param anchorId - Row this load should open on, overriding the remembered
 *     cursor: the conversation the human wrote in just before the reload.
 * @param waiting - Rows whose waiting sign has to be visible from the first
 *     paint, because it was already up before the reload.
 * @returns The live cursor, folding and search state.
 */
export function createNavigation(
  brief: BriefDocument,
  onFold: (id: string) => void = () => undefined,
  anchorId: string | null = null,
  waiting: string[] = [],
): Navigation {
  const rows = outline(brief);
  const rowIds = new Set(rows.map((row) => row.id));
  const restored = restoreCursor(rows, anchorId ?? readSavedCursor());
  if (restored !== null) {
    // The landing is written into the store, not just painted from it: an
    // anchored load (the human's own message) must survive the NEXT reload
    // too, or the agent's answer arriving seconds later throws the human
    // back to wherever they were before they wrote.
    saveCursor(restored);
  }
  const [cursorId, setCursorId] = createSignal<string | null>(restored);
  // The authority on where the cursor is. The painted signal is written
  // inside a view transition, which the browser defers to the next frame, so
  // two keys pressed within one frame would otherwise both move from the same
  // row and one of the two presses would be lost.
  let selected: string | null = restored;
  const fresh = createFreshness(rows);
  const [open, setOpen] = createSignal<ReadonlySet<string>>(
    openedFor(brief, rows, restored, fresh.ids(), waiting),
  );
  const [query, setQueryValue] = createSignal("");
  const [overlay, setOverlay] = createSignal<Overlay>("none");

  /**
   * Move the cursor as part of a change already being animated.
   *
   * Folding the page and moving the cursor off what just folded are one
   * event, and a second view transition started inside the first cancels it,
   * so whole-page commands write the cursor rather than calling ``select``.
   *
   * @param id - Row the cursor lands on.
   */
  const place = (id: string): void => {
    selected = id;
    setCursorId(id);
    saveCursor(id);
  };

  const modes = createViewModes({
    rows,
    query,
    open,
    setOpen: (next) => setOpen(next),
    cursorId: () => selected,
    place,
    onFold,
  });

  const visible = createMemo(() => {
    const matching = filterRows(rows, query());
    return modes.chats() ? chatRows(matching) : matching;
  });
  const painted = createMemo(() => paintedRows(visible(), open()));
  const ordinals = createMemo(() => itemOrdinals(painted()));
  const visibleIds = createMemo(
    () => new Set(visible().map((row) => row.id)),
  );
  const byId = new Map(rows.map((row) => [row.id, row]));
  const awaiting = awaitingThreadCount(rows);
  const chatting = countChats(rows);

  const expand = (id: string, current: ReadonlySet<string>): Set<string> => {
    const next = new Set(current);
    for (const ancestor of ancestorIds(id)) {
      next.add(ancestor);
    }
    return next;
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
      // The row exists but a filter is hiding it, and a cursor on a row that
      // is not rendered is a cursor the human cannot see, cannot move from and
      // cannot fold. Whoever asked for this row wins; the filter gives way.
      setQueryValue("");
      modes.setChats(false);
    }
    fresh.visit(id);
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
    // In the chats view the item key walks conversations: the view is a list
    // of the human's chats, and the key that means "the next thing" has to
    // mean the next thing on the page they are looking at.
    const wanted = modes.chats() && kind === "item" ? "thread" : kind;
    const next = moveByKind(visible(), selected, wanted, delta);
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
    painted,
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
    isFresh: fresh.isFresh,
    isOpen: (id) => open().has(id),
    toggle: (id) => {
      const wasOpen = open().has(id);
      fresh.visit(id);
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
    expandAll: modes.expandAll,
    collapseAll: modes.collapseAll,
    chats: modes.chats,
    toggleChats: modes.toggleChats,
    chatCount: () => chatting,
    ordinal: (id) => ordinals().get(id) ?? null,
    query,
    setQuery: search,
    matchCount: () => countItems(visible()),
    overlay,
    openOverlay: setOverlay,
    closeOverlay: () => setOverlay("none"),
    awaitingCount: () => awaiting,
    revealAnchor: () => {
      if (anchorId !== null && restored !== null) {
        revealRowSoon(restored);
      }
    },
  };
}
