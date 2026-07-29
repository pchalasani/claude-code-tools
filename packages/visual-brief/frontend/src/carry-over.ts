/**
 * What a newly delivered document does to the state that was already there.
 *
 * A publish used to answer this by throwing the state away with the page. Now
 * the state outlives the document it was built from, so every question has to
 * be answered on purpose, once, and in one order — which is what this is.
 *
 * The answers all lean the same way: the human's decisions win. They chose
 * what to expand and what to fold, and where to leave the cursor, about a page
 * they are reading; the agent publishing is not a reason to undo any of it.
 * Only two things move, and each is a thing the human would want moved: a row
 * that was not in the previous document has no decision behind it and takes
 * the ordinary defaults, and a cursor whose row has gone climbs to the nearest
 * container the page is still painting.
 */

import { createComputed, on, type Accessor } from "solid-js";

import { restoreCursor } from "./cursor";
import type { BriefDocument } from "./document";
import { carriedOpen } from "./folding";
import type { Freshness } from "./freshness";
import type { Row } from "./outline";

/** The state one newly delivered document has to be carried across. */
export interface CarryOverDeps {
  /** The document being shown, read live. */
  brief: Accessor<BriefDocument>;
  /** Its rows, as they stand now. */
  rows: Accessor<Row[]>;
  /**
   * The rows the page is actually painting, as they stand now.
   *
   * A cursor has to land on one of these. A search or the chats view can be
   * hiding the row a structural climb would choose, and a cursor on a row
   * nobody is looking at is a cursor that cannot be seen, moved or folded.
   */
  painted: Accessor<Row[]>;
  /** The ids of those rows. */
  ids: Accessor<ReadonlySet<string>>;
  /** What has been answered since the human last looked. */
  fresh: Freshness;
  /** Replace the expanded rows. */
  setOpen: (update: (held: ReadonlySet<string>) => Set<string>) => void;
  /** The row the cursor is on, read synchronously. */
  cursorId: () => string | null;
  /** Put the cursor on a row without scrolling to it. */
  place: (id: string) => void;
  /** Told about each row that has just left the page. */
  onRemove: (id: string) => void;
}

/**
 * Keep one page's state in step with a document that keeps arriving.
 *
 * @param deps - The state to carry across each newly delivered document.
 */
export function carryAcrossPublishes(deps: CarryOverDeps): void {
  let known = deps.ids();
  createComputed(
    on(
      deps.rows,
      (current) => {
        const present = deps.ids();
        const arrived = new Set(
          [...present].filter((id) => !known.has(id)),
        );
        const gone = [...known].filter((id) => !present.has(id));
        known = present;
        deps.fresh.settle(current);
        deps.setOpen((held) =>
          carriedOpen(deps.brief(), current, held, arrived, deps.fresh.ids()),
        );
        // A row that has gone takes with it anything rendered inside it: a
        // chat box written at it has nowhere left to be.
        for (const id of gone) {
          deps.onRemove(id);
        }
        // The cursor holds its row. Only a row that is no longer in the
        // document moves it, and then only as far as the nearest surviving
        // container — never to the top of the page and never to nothing.
        //
        // It climbs through what the page is painting rather than through the
        // document, because a container the search has filtered away is as
        // unreachable as one that has gone: landing there would leave nothing
        // marked on a page still showing the other matches. The filter is the
        // human's and is not dropped to make room for the cursor.
        const here = deps.cursorId();
        if (here !== null && !present.has(here)) {
          const landing = restoreCursor(deps.painted(), here);
          if (landing !== null) {
            deps.place(landing);
          }
        }
      },
      { defer: true },
    ),
  );
}
