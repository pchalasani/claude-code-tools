/**
 * The commands that change what the whole page is showing.
 *
 * Folding one row is navigation; folding every row, or hiding everything that
 * is not a conversation the human wrote in, changes what the page is for. All
 * three are whole-page moves, and all three share one hazard: the cursor can
 * be left inside something that just stopped being on the page. So each of
 * them computes the new open set first, decides where the cursor has to land
 * given that set, and applies both inside a single transition — a second
 * transition started inside the first cancels it, and the fold is what the
 * eye is following.
 */

import { createSignal, type Accessor } from "solid-js";

import { chatRows, filterRows } from "./cursor";
import { collapseToLaneIds, expandAllIds, nearestPainted } from "./folding";
import { ancestorIds, type Row } from "./outline";
import { scrollRowIntoView } from "./reveal";
import { withTransition } from "./transitions";

/** The whole-page view commands. */
export interface ViewModes {
  /** Whether the page is showing only the human's own conversations. */
  chats: Accessor<boolean>;
  /** Turn the conversations-only view on or off explicitly. */
  setChats: (on: boolean) => void;
  /** Enter the conversations-only view, or leave it. */
  toggleChats: () => void;
  /** Open every row, to the most granular level there is. */
  expandAll: () => void;
  /** Fold the whole page back to its lanes. */
  collapseAll: () => void;
}

/** What the view commands need from the navigable state around them. */
export interface ViewModeDeps {
  /** Every row of the document, in order. */
  rows: Row[];
  /** The current search text. */
  query: Accessor<string>;
  /** The rows currently expanded. */
  open: Accessor<ReadonlySet<string>>;
  /** Replace the expanded rows. */
  setOpen: (next: Set<string>) => void;
  /** The row the cursor is on, read synchronously. */
  cursorId: () => string | null;
  /** Put the cursor on a row as part of a change already being animated. */
  place: (id: string) => void;
  /** Told about each row that has just been folded away. */
  onFold: (id: string) => void;
}

/**
 * Build the whole-page view commands.
 *
 * @param deps - The navigable state these commands act on.
 * @returns The live view commands.
 */
export function createViewModes(deps: ViewModeDeps): ViewModes {
  const [chats, setChats] = createSignal(false);

  /**
   * Apply one wholesale change to what is open, taking the cursor with it.
   *
   * @param wanted - The rows that should be expanded afterwards.
   */
  const refold = (wanted: Set<string>): void => {
    const here = deps.cursorId();
    const closed = [...deps.open()].filter((id) => !wanted.has(id));
    const landing = nearestPainted(here, wanted);
    withTransition(() => {
      deps.setOpen(wanted);
      if (landing !== null && landing !== here) {
        deps.place(landing);
      }
    });
    for (const id of closed) {
      deps.onFold(id);
    }
    if (landing !== null) {
      queueMicrotask(() => scrollRowIntoView(landing));
    }
  };

  const toggleChats = (): void => {
    if (chats()) {
      setChats(false);
      return;
    }
    const wanted = chatRows(filterRows(deps.rows, deps.query()));
    const here = deps.cursorId();
    // The view is a list of conversations, so the cursor lands on one: the
    // one it was already in, or the first the human wrote.
    const landing =
      wanted.find((row) => row.id === here && row.kind === "thread")
      ?? wanted.find((row) => row.kind === "thread");
    if (landing === undefined) {
      // Nothing to show. Entering an empty view would take the cursor off the
      // page and leave the human with a blank screen and no way back.
      return;
    }
    const opened = new Set(deps.open());
    for (const row of wanted) {
      for (const ancestor of ancestorIds(row.id)) {
        opened.add(ancestor);
      }
    }
    withTransition(() => {
      deps.setOpen(opened);
      setChats(true);
      deps.place(landing.id);
    });
    queueMicrotask(() => scrollRowIntoView(landing.id));
  };

  return {
    chats,
    setChats,
    toggleChats,
    expandAll: () => refold(expandAllIds(deps.rows)),
    collapseAll: () => refold(collapseToLaneIds(deps.rows)),
  };
}
