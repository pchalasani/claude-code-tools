/**
 * The delivered document as something that can change under a live page.
 *
 * A publish used to throw the whole page away. It no longer does: the new
 * document arrives as data and is diffed into the one the page is already
 * showing. What makes that worth doing is identity. Re-parsing JSON produces
 * all-new objects, and a naive swap would make every ``For`` on the page
 * discard and rebuild every row — the same flicker as a reload, now without
 * the honesty of one.
 *
 * ``reconcile`` keyed on ``id`` is what avoids it: it walks the two documents
 * together, writes only the fields that actually differ, and leaves the
 * identity of everything unchanged exactly as it was. A row nobody edited
 * keeps its objects, so it keeps its DOM, so the human reading it notices
 * nothing at all.
 */

import { createStore, reconcile } from "solid-js/store";

import type { BriefDocument } from "./document";

/** The document the page is showing, and the way to replace it. */
export interface LiveDocument {
  /** What the page is showing right now; reading it tracks. */
  readonly brief: BriefDocument;
  /** Show a newly delivered document, keeping whatever has not changed. */
  apply: (next: BriefDocument) => void;
}

interface ComposerSelection {
  element: HTMLTextAreaElement;
  start: number;
  end: number;
  direction: "forward" | "backward" | "none";
}

interface FocusBookmark {
  element: HTMLElement;
  rowId: string;
  controlId: string;
  occurrence: number;
}

const ROW_SELECTOR = "[data-row-id]";
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button",
  "input",
  "select",
  "textarea",
  "[tabindex]",
].join(", ");

function controlId(element: HTMLElement): string {
  return [
    element.localName,
    element.id,
    element.className,
    element.getAttribute("type") ?? "",
    element.dataset.action ?? "",
    element.dataset.signal ?? "",
    element.getAttribute("href") ?? "",
  ].join("|");
}

function rowControls(row: HTMLElement): HTMLElement[] {
  return [...row.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)]
    .filter((control) => control.closest(ROW_SELECTOR) === row);
}

function activeFocusBookmark(): FocusBookmark | null {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement)) {
    return null;
  }
  const row = active.closest<HTMLElement>(ROW_SELECTOR);
  if (row?.dataset.rowId === undefined) {
    return null;
  }
  const id = controlId(active);
  const occurrence = rowControls(row)
    .filter((control) => controlId(control) === id)
    .indexOf(active);
  return occurrence < 0 ? null : {
    element: active,
    rowId: row.dataset.rowId,
    controlId: id,
    occurrence,
  };
}

function restoreFocus(bookmark: FocusBookmark | null): void {
  if (bookmark === null || bookmark.element.isConnected) {
    return;
  }
  const row = [...document.querySelectorAll<HTMLElement>(ROW_SELECTOR)]
    .find((candidate) => candidate.dataset.rowId === bookmark.rowId);
  const replacement = row === undefined ? undefined : rowControls(row)
    .filter((control) => controlId(control) === bookmark.controlId)
    [bookmark.occurrence];
  replacement?.focus({ preventScroll: true });
}

function activeComposerSelection(): ComposerSelection | null {
  const active = document.activeElement;
  if (
    !(active instanceof HTMLTextAreaElement)
    || !active.matches(".composer textarea")
  ) {
    return null;
  }
  return {
    element: active,
    start: active.selectionStart,
    end: active.selectionEnd,
    direction: active.selectionDirection,
  };
}

function restoreComposerSelection(selection: ComposerSelection | null): void {
  if (selection === null || selection.element.isConnected) {
    return;
  }
  const replacement = document.querySelector<HTMLTextAreaElement>(
    ".composer textarea",
  );
  if (replacement === null) {
    return;
  }
  replacement.focus({ preventScroll: true });
  replacement.setSelectionRange(
    selection.start,
    selection.end,
    selection.direction,
  );
}

/**
 * Hold one delivered document in state a later delivery can replace.
 *
 * @param initial - The document the page was rendered from.
 * @returns The live document.
 */
export function createLiveDocument(initial: BriefDocument): LiveDocument {
  const [held, setHeld] = createStore<{ brief: BriefDocument }>({
    brief: initial,
  });
  return {
    get brief(): BriefDocument {
      return held.brief;
    },
    // Keyed on `id`, which every update, lane, item, conversation and named
    // note carries. Turns and raw evidence carry none and are matched by
    // position instead, which is what they are: an appended turn leaves every
    // turn before it untouched.
    apply: (next) => {
      const selection = activeComposerSelection();
      const focus = selection === null ? activeFocusBookmark() : null;
      setHeld("brief", reconcile(next, { key: "id" }));
      restoreFocus(focus);
      restoreComposerSelection(selection);
    },
  };
}
