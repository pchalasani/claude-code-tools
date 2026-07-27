/**
 * One state object for the whole page.
 *
 * The keyboard resolves to an action, the action changes state, and the
 * interface is painted from that state. Nothing in this chain asks the
 * browser what is selected, which is the whole point of the rewrite.
 */

import { composeRow } from "./cursor";
import {
  createComposer,
  postJson,
  type Composer,
  type ComposeTarget,
} from "./composer";
import type { BriefDocument } from "./document";
import { resolveAction, type Action } from "./keys";
import { createNavigation, type Navigation } from "./navigation";
import { ancestorIds, type Row } from "./outline";

/** Everything the page reads and writes. */
export interface BriefState {
  /** The document being shown. */
  brief: BriefDocument;
  /** Cursor, folding and search. */
  nav: Navigation;
  /** What the human is writing. */
  composer: Composer;
  /** Open the composer against one row. */
  composeAt: (row: Row) => void;
  /** Run one resolved action. */
  run: (action: Action) => void;
  /** Handle one key press. */
  handleKey: (event: KeyboardEvent) => void;
}

/**
 * Build the state for one delivered document.
 *
 * @param brief - The document the page is showing.
 * @returns The live page state.
 */
export function createBriefState(brief: BriefDocument): BriefState {
  const nav = createNavigation(brief, abandonComposer);

  /** Row the open composer expanded, so closing it can fold it back. */
  let expandedForComposer: string | null = null;

  /**
   * Hand back a row the composer borrowed, however the composer let go.
   *
   * Every close path — the ask key pressed again, Escape, the cancel button,
   * moving the composer to another row, a successful send — arrives here, so
   * the row cannot be left expanded by one route and folded by another. A row
   * that was written into is opened rather than merely left alone: the note
   * that just landed is rendered inside it, and Escape during a send would
   * otherwise fold the confirmation out of sight before it arrived.
   */
  const releaseRow = (rowId: string, sent: boolean): void => {
    const borrowed = expandedForComposer === rowId;
    if (borrowed) {
      expandedForComposer = null;
    }
    if (sent) {
      nav.setOpen(rowId, true);
      return;
    }
    if (borrowed) {
      nav.setOpen(rowId, false);
    }
  };

  const composer = createComposer(postJson, releaseRow);

  /**
   * Let go of the composer when the row it is written in folds away.
   *
   * The box only renders inside an expanded row, and inside every row that
   * contains it, so a fold that leaves the composer pointed at something
   * nobody can see makes the next ask key close a box that is not there.
   * Folding a row and abandoning its composer are one event.
   *
   * @param rowId - Row that was just folded.
   */
  function abandonComposer(rowId: string): void {
    const written = composer.target()?.rowId;
    if (written === undefined) {
      return;
    }
    if (written === rowId || ancestorIds(written).includes(rowId)) {
      composer.close();
    }
  }

  const composeAt = (row: Row): void => {
    const target: ComposeTarget =
      row.kind === "thread" && row.parentThreadId !== undefined
        ? { rowId: row.id, anchorId: row.anchorId, parentId: row.parentThreadId }
        : { rowId: row.id, anchorId: row.anchorId };
    composer.toggleAt(target);
    if (composer.isOpenAt(row.id)) {
      expandedForComposer = revealNow(nav, row.id) ? row.id : null;
      nav.select(row.id, { scroll: false });
      focusLater(".composer textarea");
      return;
    }
    nav.select(row.id, { scroll: false });
  };

  const composeAtCursor = (): void => {
    const row = composeRow(nav.visible(), nav.currentId());
    if (row !== null) {
      composeAt(row);
    }
  };

  const closeOne = (): void => {
    if (nav.overlay() === "help") {
      nav.closeOverlay();
      return;
    }
    if (nav.overlay() === "search") {
      nav.setQuery("");
      nav.closeOverlay();
      return;
    }
    if (composer.target() !== null) {
      composer.close();
    }
  };

  const run = (action: Action): void => {
    const actions: Record<Action, () => void> = {
      "next-item": () => nav.move("item", 1),
      "previous-item": () => nav.move("item", -1),
      "next-lane": () => nav.move("lane", 1),
      "previous-lane": () => nav.move("lane", -1),
      toggle: () => {
        const id = nav.currentId();
        if (id !== null) {
          nav.toggle(id);
        }
      },
      compose: composeAtCursor,
      "next-awaiting": () => nav.toAwaiting(),
      search: () => {
        nav.openOverlay("search");
        focusLater("#brief-search");
      },
      top: () => nav.jump("top"),
      bottom: () => nav.jump("bottom"),
      help: () => nav.openOverlay("help"),
      close: closeOne,
    };
    actions[action]();
  };

  return {
    brief,
    nav,
    composer,
    composeAt,
    run,
    handleKey: (event) => {
      const action = resolveAction(event);
      if (action === null) {
        return;
      }
      event.preventDefault();
      run(action);
    },
  };
}

/**
 * Expand one row and everything containing it, at once.
 *
 * The composer renders inside the row's body, so the row and its ancestors
 * have to be open before the caret can be put in its text box. ``select``
 * expands too, but through a view transition the browser defers by a frame —
 * a frame too late, which is how a human ended up typing into the page
 * instead of into the composer.
 *
 * The containers stay open afterwards whatever happens next: the cursor is
 * now on this row, and a cursor inside a folded container is invisible.
 *
 * @param nav - The navigable state.
 * @param rowId - Row that has to become visible now.
 * @returns Whether the row itself had to be expanded.
 */
function revealNow(nav: Navigation, rowId: string): boolean {
  for (const ancestor of ancestorIds(rowId)) {
    nav.setOpen(ancestor, true);
  }
  if (nav.isOpen(rowId)) {
    return false;
  }
  nav.setOpen(rowId, true);
  return true;
}

/**
 * Put the browser's text caret in a box once it exists.
 *
 * Typing needs the browser's focus; selection does not. This is the only
 * place the page asks for focus, and it asks only for text boxes.
 *
 * @param selector - Selector of the text box to focus.
 */
function focusLater(selector: string): void {
  if (typeof document === "undefined") {
    return;
  }
  queueMicrotask(() => {
    const box = document.querySelector(selector);
    if (box instanceof HTMLElement) {
      box.focus();
    }
  });
}
