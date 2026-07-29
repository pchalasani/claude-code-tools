/**
 * One state object for the whole page.
 *
 * The keyboard resolves to an action, the action changes state, and the
 * interface is painted from that state. Nothing in this chain asks the
 * browser what is selected, which is the whole point of the rewrite.
 */

import type { Accessor } from "solid-js";

import { composeRow } from "./cursor";
import {
  createComposer,
  postJson,
  type Composer,
  type ComposeTarget,
} from "./composer";
import type { BriefDocument } from "./document";
import { createHints, type Hints } from "./hints";
import { isTypingTarget, resolveAction, type Action } from "./keys";
import {
  createNavigation,
  type Navigation,
  type Overlay,
} from "./navigation";
import { ancestorIds, type Row } from "./outline";
import { createPending, type Pending } from "./pending";
import { focusLater } from "./reveal";

/** Everything the page reads and writes. */
export interface BriefState {
  /** The document being shown, read live: a publish replaces it in place. */
  readonly brief: BriefDocument;
  /** Cursor, folding and search. */
  nav: Navigation;
  /** What the human is writing. */
  composer: Composer;
  /** The jump labels, when they are showing. */
  hints: Hints;
  /** What this page has sent and not yet seen arrive. */
  pending: Pending;
  /** Open the composer against one row. */
  composeAt: (row: Row) => void;
  /** Run one resolved action. */
  run: (action: Action) => void;
  /** Handle one key press. */
  handleKey: (event: KeyboardEvent) => void;
}

/**
 * Build the state for a document that can change under the page.
 *
 * @param brief - The document the page is showing, read live.
 * @returns The live page state.
 */
export function createBriefState(brief: Accessor<BriefDocument>): BriefState {
  // Read before anything is built: a message this tab sent just before a
  // reload names the conversation this load should open on, which the cursor
  // has to know before it decides where it is.
  const pending = createPending(brief);
  const landing = pending.landing();
  // The conversation a send landed in opens with the page: the human is being
  // returned to something they wrote, and a folded row is not a return.
  const nav = createNavigation(
    brief,
    abandonComposer,
    landing,
    landing === null ? pending.waiting() : [...pending.waiting(), landing],
    removeComposer,
  );
  const hints = createHints({
    rows: () => nav.painted(),
    select: (id) => nav.select(id),
  });

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
   *
   * Opening that row is not enough on its own. A request in flight is seconds
   * long and the page stays live throughout — Escape, a fold, a collapse of
   * the whole page — so everything containing the row is opened with it. A
   * note inside a folded container is a note the human never sees, and it was
   * the reassurance they were waiting for.
   */
  const releaseRow = (rowId: string, sent: boolean): void => {
    const borrowed = expandedForComposer === rowId;
    if (borrowed) {
      expandedForComposer = null;
    }
    if (sent) {
      revealNow(nav, rowId);
      return;
    }
    if (borrowed) {
      nav.setOpen(rowId, false);
    }
  };

  const composer = createComposer(postJson, releaseRow, pending);

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

  /**
   * Close a composer whose row no longer exists in the document.
   *
   * The row cannot keep painting its chat box, but the draft remains the
   * human's until they send or explicitly discard it.
   *
   * @param rowId - Row removed by a newly delivered document.
   */
  function removeComposer(rowId: string): void {
    composer.removeRow(rowId);
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
      composer.escape();
      return;
    }
    if (nav.chats()) {
      nav.toggleChats();
    }
  };

  /**
   * Show one overlay, and put the jump labels away as it opens.
   *
   * An overlay owns the keyboard while it is up — Escape closes it, and that
   * is the only way out an overlay advertises. Hint labels own the keyboard
   * too, and they own it first, so a page left in hint mode would swallow the
   * Escape and leave the human holding a dialog that will not close. The
   * labels lose: they describe a page nobody is reading any more, and jumping
   * the cursor around underneath a modal dialog was never the point.
   *
   * Every way in comes through here, because the key and the on-screen
   * control both run the same action.
   *
   * @param overlay - The overlay to show.
   */
  const showOverlay = (overlay: Exclude<Overlay, "none">): void => {
    hints.leave();
    nav.openOverlay(overlay);
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
      "expand-all": () => nav.expandAll(),
      "collapse-all": () => nav.collapseAll(),
      compose: composeAtCursor,
      "next-awaiting": () => nav.toOpenChat(),
      chats: () => {
        if (!nav.chats()) {
          nav.setQuery("");
          nav.closeOverlay();
        }
        nav.toggleChats();
      },
      // Labels never arm over a modal: they would paint behind the scrim
      // and their handler would swallow the dialog's Escape. The search
      // panel is not modal, and jumping between its matches is fine.
      hints: () => {
        if (nav.overlay() !== "help") {
          hints.enter();
        }
      },
      search: () => {
        showOverlay("search");
        focusLater("#brief-search");
      },
      top: () => nav.jump("top"),
      bottom: () => nav.jump("bottom"),
      help: () => showOverlay("help"),
      close: closeOne,
    };
    actions[action]();
  };

  return {
    get brief(): BriefDocument {
      return brief();
    },
    nav,
    composer,
    hints,
    pending,
    composeAt,
    run,
    handleKey: (event) => {
      // While the labels are up they own the keyboard: a key that quietly did
      // something else would act on a page the human is no longer reading.
      // Browser and system chords are never taken, so Command-R still works.
      const chorded =
        event.ctrlKey || event.metaKey || event.altKey;
      const typing = isTypingTarget(event.target ?? null);
      if (!chorded && !typing && nav.overlay() === "help") {
        // A MODAL overlay owns the keyboard: only its dismissal is honoured,
        // so no global shortcut can arm hint labels over the scrim or mutate
        // a page the human cannot see. Search is deliberately not gated —
        // it is a filter panel, and j/k walking the matches is its point.
        if (event.key === "Escape") {
          event.preventDefault();
          closeOne();
        }
        return;
      }
      if (
        !chorded
        && !typing
        && hints.handleKey(event.key)
      ) {
        event.preventDefault();
        return;
      }
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
