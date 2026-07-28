/**
 * Which rows are open, as pure arithmetic over the outline.
 *
 * Opening one row is a keystroke; opening or folding the whole page is a
 * decision about what the page is for. Both end up as one set of row ids, and
 * everything here computes such a set without touching the DOM, so what the
 * human sees after "expand all" is what a test can assert without a browser.
 */

import type { BriefDocument } from "./document";
import { ancestorIds, defaultOpenIds, type Row } from "./outline";

/**
 * Open every row of the document, to the most granular level there is.
 *
 * @param rows - Every row of the document.
 * @returns Ids of all rows, all expanded.
 */
export function expandAllIds(rows: Row[]): Set<string> {
  return new Set(rows.map((row) => row.id));
}

/**
 * Fold the page back to its lanes.
 *
 * The updates stay open so their lanes are still on the page — a page folded
 * down to nothing but headlines is a page with nowhere to go — and every lane
 * is closed, which is the coarsest reading of the document that still shows
 * its shape.
 *
 * @param rows - Every row of the document.
 * @returns Ids of the rows that remain expanded.
 */
export function collapseToLaneIds(rows: Row[]): Set<string> {
  return new Set(
    rows.filter((row) => row.kind === "update").map((row) => row.id),
  );
}

/**
 * Keep only the rows the page actually paints.
 *
 * A row is drawn inside its container's body, so a row whose container is
 * folded is not on the page at all however visible the search leaves it.
 * Anything that labels or numbers what the human can see has to start here.
 *
 * @param rows - Rows the search leaves on the page, in document order.
 * @param open - Ids of the expanded rows.
 * @returns The rows the human can see, in document order.
 */
export function paintedRows(rows: Row[], open: ReadonlySet<string>): Row[] {
  return rows.filter((row) =>
    ancestorIds(row.id).every((ancestor) => open.has(ancestor)),
  );
}

/**
 * Number the items the human can see, so they can be cited by number.
 *
 * The numbers run across the whole page rather than restarting inside each
 * lane: "item 12" has to name one item, not one per lane. Only painted items
 * are numbered, which is what makes the numbering stable to read off the
 * screen — folded content carries no number because it is not there.
 *
 * @param painted - The rows the page is painting, in document order.
 * @returns Each painted item's position, keyed by row id.
 */
export function itemOrdinals(painted: Row[]): Map<string, number> {
  const ordinals = new Map<string, number>();
  for (const row of painted) {
    if (row.kind === "item") {
      ordinals.set(row.id, ordinals.size + 1);
    }
  }
  return ordinals;
}

/**
 * Find the nearest row that would still be painted once folding is applied.
 *
 * Folding the page must not take the cursor with it: a cursor inside a row
 * that just closed is invisible, unmovable and unfoldable. The cursor climbs
 * to the innermost container that survives.
 *
 * @param id - Row the cursor is on, if any.
 * @param open - Ids of the rows that will be expanded.
 * @returns The row id to hold the cursor, or null when there is none.
 */
export function nearestPainted(
  id: string | null,
  open: ReadonlySet<string>,
): string | null {
  if (id === null) {
    return null;
  }
  for (const candidate of [id, ...ancestorIds(id)]) {
    if (ancestorIds(candidate).every((ancestor) => open.has(ancestor))) {
      return candidate;
    }
  }
  return null;
}

/**
 * Choose the rows that are expanded when the page opens.
 *
 * @param brief - The delivered document.
 * @param rows - The document's rows.
 * @param cursorId - Row the cursor was restored to.
 * @param fresh - Conversations answered since the human last looked.
 * @param waiting - Rows carrying a message this tab sent and has not yet seen
 *     arrive, whose waiting sign has to survive the reload that hid it.
 * @returns The initially expanded row ids.
 */
export function openedFor(
  brief: BriefDocument,
  rows: Row[],
  cursorId: string | null,
  fresh: ReadonlySet<string>,
  waiting: Iterable<string> = [],
): Set<string> {
  const opened = defaultOpenIds(brief, rows);
  if (cursorId !== null) {
    for (const ancestor of ancestorIds(cursorId)) {
      opened.add(ancestor);
    }
  }
  // An answer nobody can see is an answer that did not arrive: a conversation
  // answered since the last look opens itself and everything holding it. The
  // same goes for a question of the human's own that is still in flight.
  for (const id of [...fresh, ...waiting]) {
    opened.add(id);
    for (const ancestor of ancestorIds(id)) {
      opened.add(ancestor);
    }
  }
  return opened;
}

/**
 * Carry folding across a newly delivered document.
 *
 * A publish patched into an open page must not refold it. Every row the human
 * expanded stays expanded and every row they folded stays folded, because
 * those are decisions they made about a page they are reading and the agent
 * publishing is not a reason to undo them.
 *
 * Only two things move. A row that was not in the previous document has no
 * decision behind it, so it gets the ordinary default treatment — which is
 * what keeps new material from arriving hidden. And a conversation whose
 * answer has just landed opens itself and everything holding it, because an
 * answer nobody can see is an answer that did not arrive.
 *
 * @param brief - The newly delivered document.
 * @param rows - Its rows.
 * @param open - The rows that are expanded right now.
 * @param arrived - Ids of the rows that were not in the previous document.
 * @param fresh - Conversations answered since the human last looked.
 * @returns The rows that should be expanded now.
 */
export function carriedOpen(
  brief: BriefDocument,
  rows: Row[],
  open: ReadonlySet<string>,
  arrived: ReadonlySet<string>,
  fresh: ReadonlySet<string>,
): Set<string> {
  const present = new Set(rows.map((row) => row.id));
  const next = new Set([...open].filter((id) => present.has(id)));
  if (arrived.size > 0) {
    for (const id of defaultOpenIds(brief, rows)) {
      if (arrived.has(id)) {
        next.add(id);
      }
    }
  }
  for (const id of fresh) {
    if (!present.has(id)) {
      continue;
    }
    next.add(id);
    for (const ancestor of ancestorIds(id)) {
      next.add(ancestor);
    }
  }
  return next;
}
