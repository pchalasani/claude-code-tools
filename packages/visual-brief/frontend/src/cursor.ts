/**
 * The cursor's arithmetic, as pure functions over rows.
 *
 * The application owns which row is selected. Nothing here consults the
 * browser: not ``:focus``, not ``document.activeElement``, not scroll
 * position. Given a list of rows and the current id, these functions say
 * which id comes next — which is exactly what the tests assert and exactly
 * what the interface paints.
 */

import { ancestorIds, type Row, type RowKind } from "./outline";

/** Which end of the document a jump lands on. */
export type Edge = "top" | "bottom";

/**
 * Move the cursor to the next or previous row of one kind, stopping at the ends.
 *
 * Movement deliberately does NOT wrap. Wrapping meant that pressing "up" at the
 * top of the page threw the reader to the oldest entry at the very bottom, and
 * "down" at the bottom threw them back to the newest — which reads as the page
 * losing its place rather than as a feature. At either end the cursor simply
 * stays put.
 *
 * A cursor sitting on a different kind of row steps to the nearest row of the
 * wanted kind in the direction of travel, so pressing the lane key from an
 * item lands on the next lane rather than jumping to the start.
 *
 * @param rows - Rows currently on the page, in document order.
 * @param cursorId - Row the cursor is on, if any.
 * @param kind - Kind of row to move between.
 * @param delta - Positive to move forward, negative to move back.
 * @returns The row id to select, or the current one when nothing can move.
 */
export function moveByKind(
  rows: Row[],
  cursorId: string | null,
  kind: RowKind,
  delta: number,
): string | null {
  const candidates = rows.filter((row) => row.kind === kind);
  return moveAmong(rows, cursorId, candidates, delta);
}

/**
 * Move through the content rows the reader can currently see.
 *
 * Updates and lanes are containers, with their own shifted-key movement.
 * Ordinary movement walks the content inside them: items, conversations and
 * evidence. Callers supply painted rows so a folded-away child is never an
 * invisible stop.
 *
 * @param rows - Painted rows, in painted order.
 * @param cursorId - Row the cursor is on, if any.
 * @param delta - Positive to move forward, negative to move back.
 * @returns The row id to select, or the current one when nothing can move.
 */
export function moveByContent(
  rows: Row[],
  cursorId: string | null,
  delta: number,
): string | null {
  const candidates = rows.filter(
    (row) =>
      row.kind === "item"
      || row.kind === "thread"
      || row.kind === "evidence",
  );
  return moveAmong(rows, cursorId, candidates, delta);
}

/**
 * Move among an ordered subset of rows, stopping at the ends.
 *
 * @param rows - All rows establishing painted order.
 * @param cursorId - Row the cursor is on, if any.
 * @param candidates - Rows this movement may land on.
 * @param delta - Positive to move forward, negative to move back.
 * @returns The row id to select, or the current one when nothing can move.
 */
function moveAmong(
  rows: Row[],
  cursorId: string | null,
  candidates: Row[],
  delta: number,
): string | null {
  const last = candidates[candidates.length - 1];
  const first = candidates[0];
  if (first === undefined || last === undefined) {
    return cursorId;
  }
  const here = indexOf(rows, cursorId);
  if (here < 0) {
    return delta > 0 ? first.id : last.id;
  }
  const current = indexOf(candidates, cursorId);
  if (current >= 0) {
    const next = candidates[current + delta];
    return next === undefined ? cursorId : next.id;
  }
  // Stepping from a row of a different kind: take the nearest row of the wanted
  // kind in the direction of travel, and stay put when there is none. Falling
  // back to the far end here was the last piece of wrapping — pressing "up"
  // from the top update sent the reader to the oldest row on the page.
  if (delta > 0) {
    const after = candidates.find((row) => indexOf(rows, row.id) > here);
    return after === undefined ? cursorId : after.id;
  }
  const before = [...candidates]
    .reverse()
    .find((row) => indexOf(rows, row.id) < here);
  return before === undefined ? cursorId : before.id;
}

/**
 * Jump to the first or last row of the document.
 *
 * @param rows - Rows currently on the page, in document order.
 * @param edge - Which end to jump to.
 * @returns The row id to select, or null when the page has no rows.
 */
export function edgeRow(rows: Row[], edge: Edge): string | null {
  const row = edge === "top" ? rows[0] : rows[rows.length - 1];
  return row?.id ?? null;
}

/**
 * Move to the next conversation that still needs the human's attention.
 *
 * The search starts after the cursor and wraps, so repeating the key walks
 * every open chat exactly once before returning to the first. A chat is open
 * while it awaits an answer or while a newly arrived answer remains unseen.
 *
 * @param rows - Rows currently on the page, in document order.
 * @param cursorId - Row the cursor is on, if any.
 * @param fresh - Conversations answered since the human last looked.
 * @returns The row id to select, or the current one when nothing is open.
 */
export function nextOutstanding(
  rows: Row[],
  cursorId: string | null,
  fresh: ReadonlySet<string>,
): string | null {
  const outstanding = rows.filter(
    (row) =>
      row.kind === "thread" && (row.awaiting || fresh.has(row.id)),
  );
  const first = outstanding[0];
  if (first === undefined) {
    return cursorId;
  }
  const here = indexOf(rows, cursorId);
  const after = outstanding.find((row) => indexOf(rows, row.id) > here);
  return (after ?? first).id;
}

/**
 * Choose where the cursor belongs given a document it may not be in.
 *
 * This answers two moments with one rule. A page that has just loaded restores
 * the cursor it remembered, and a page that has just been handed a new
 * document keeps the cursor it has. Either way, when that exact row is gone
 * the nearest surviving container takes it, and only a document with nothing
 * in common falls back to the top.
 *
 * @param rows - Rows of the document now being shown, in document order.
 * @param savedId - Row id the cursor was on.
 * @returns The row id to select, or null when the page has no rows.
 */
export function restoreCursor(
  rows: Row[],
  savedId: string | null,
): string | null {
  if (savedId !== null && savedId !== "") {
    const known = new Set(rows.map((row) => row.id));
    for (const candidate of [savedId, ...ancestorIds(savedId)]) {
      if (known.has(candidate)) {
        return candidate;
      }
    }
  }
  const firstItem = rows.find((row) => row.kind === "item");
  return (firstItem ?? rows[0])?.id ?? null;
}

/**
 * Keep only the rows a search query leaves on the page.
 *
 * Items match; the lanes and updates that hold a match stay so the match can
 * be reached, and everything hanging from a surviving row — its conversations
 * and its evidence — stays with it. An empty query keeps everything.
 *
 * The second pass walks in document order, which is what lets evidence nest:
 * a note is kept because the note above it was kept, and the note above it
 * has already been decided by the time its children are looked at.
 *
 * @param rows - Every row of the document, in document order.
 * @param query - The human's search text.
 * @returns The surviving rows, in document order.
 */
export function filterRows(rows: Row[], query: string): Row[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") {
    return rows;
  }
  const byId = new Map(rows.map((row) => [row.id, row]));
  const kept = new Set<string>();
  for (const row of rows) {
    if (row.kind !== "item" || !row.search.includes(needle)) {
      continue;
    }
    kept.add(row.id);
    let parent = row.parentId;
    while (parent !== null) {
      kept.add(parent);
      parent = byId.get(parent)?.parentId ?? null;
    }
  }
  for (const row of rows) {
    const hangs = row.kind === "thread" || row.kind === "evidence";
    if (hangs && row.parentId !== null && kept.has(row.parentId)) {
      kept.add(row.id);
    }
  }
  return rows.filter((row) => kept.has(row.id));
}

/**
 * Keep only the conversations the human has written in, and their containers.
 *
 * This is the same filtering the search does, aimed at a different question.
 * Search asks what the page says about a word; this asks where the human has
 * spoken. Answered or not, every one of their conversations survives, together
 * with the rows that hold it so it can be reached and read.
 *
 * @param rows - Rows currently on the page, in document order.
 * @returns The human's conversations and their containers, in document order.
 */
export function chatRows(rows: Row[]): Row[] {
  const kept = new Set<string>();
  for (const row of rows) {
    if (row.kind !== "thread" || !row.human) {
      continue;
    }
    kept.add(row.id);
    for (const ancestor of ancestorIds(row.id)) {
      kept.add(ancestor);
    }
  }
  return rows.filter((row) => kept.has(row.id));
}

/**
 * Count the conversations the human has written in.
 *
 * @param rows - Rows to count.
 * @returns How many of them are the human's conversations.
 */
export function countChats(rows: Row[]): number {
  return rows.filter((row) => row.kind === "thread" && row.human).length;
}

/**
 * Count the items a search query keeps.
 *
 * @param rows - Rows surviving a search.
 * @returns How many of them are items.
 */
export function countItems(rows: Row[]): number {
  return rows.filter((row) => row.kind === "item").length;
}

/**
 * Find the row a message composed at one row would attach to.
 *
 * Composition always has a target. An item or a lane is its own target; a
 * thread targets its anchor and continues itself; an update has no anchor of
 * its own, so it hands the cursor to the first lane it holds. A piece of
 * evidence hands the cursor to the item it is evidence for: the chat box is
 * painted in that item's body, and a question about a log line is a question
 * about the claim the log line is under.
 *
 * @param rows - Rows currently on the page, in document order.
 * @param rowId - Row the cursor is on.
 * @returns The row that can carry composition, or null when none can.
 */
export function composeRow(rows: Row[], rowId: string | null): Row | null {
  const row = rows.find((candidate) => candidate.id === rowId);
  if (row === undefined) {
    return null;
  }
  if (row.kind === "evidence") {
    const owner = rows.find((candidate) => candidate.id === row.anchorId);
    return owner ?? null;
  }
  if (row.kind !== "update") {
    return row;
  }
  const lane = rows.find(
    (candidate) => candidate.kind === "lane" && candidate.parentId === row.id,
  );
  return lane ?? null;
}

/**
 * Return a row's position in a list.
 *
 * @param rows - Rows to look in.
 * @param id - Row id to find.
 * @returns The index, or -1 when the id is absent.
 */
function indexOf(rows: Row[], id: string | null): number {
  return id === null ? -1 : rows.findIndex((row) => row.id === id);
}
