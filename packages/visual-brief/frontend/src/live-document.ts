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
    apply: (next) => setHeld("brief", reconcile(next, { key: "id" })),
  };
}
