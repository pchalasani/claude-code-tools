/**
 * Raw evidence as rows the cursor can occupy.
 *
 * The forensics under an item used to be folds that kept their own open
 * state, which made the deepest layer of the page — the thing an item's
 * claims actually rest on — reachable only with a mouse. Every other layer is
 * a row, and being a row is what earns a thing the whole navigation at once:
 * a jump label, the fold key, expand-all and collapse-all, the search's
 * ancestry, the cursor's memory across a reload. A key of its own would have
 * bought one of those and left the rest still blind.
 *
 * Only notes become rows. A note has a title, which is a head to fold; a
 * plain string of evidence has none, so it is content inside the row above it
 * and is on the page the moment that row opens.
 *
 * The ids nest with ``#`` the way conversation ids do, so ``ancestorIds``
 * reads them without knowing what they are, and every segment begins with a
 * space — which no thread id may contain — so no document can name a
 * conversation that collides with one.
 */

import type { Forensic, Item, NestedNote } from "./document";
import type { Row } from "./outline";

/** What the fold holding an item's evidence is called. */
export const EVIDENCE_LABEL = "Raw evidence and deeper forensics";

/**
 * Return the row id of the evidence hanging from one item.
 *
 * @param itemPath - Row id of the item.
 * @returns The evidence row's id.
 */
export function evidenceRowId(itemPath: string): string {
  return `${itemPath}# evidence`;
}

/**
 * Return the row id of one nested note.
 *
 * @param parentId - Row id of the evidence or note holding it.
 * @param index - The note's position among its parent's entries.
 * @returns The note's row id.
 */
export function noteRowId(parentId: string, index: number): string {
  return `${parentId}# ${index}`;
}

/**
 * Flatten one item's evidence into rows, in the order the page paints them.
 *
 * @param item - A delivered item.
 * @param itemPath - Row id of the item.
 * @returns The evidence row and every note row under it, or nothing when the
 *     item carries no evidence at all.
 */
export function evidenceRows(item: Item, itemPath: string): Row[] {
  const entries = item.forensics ?? [];
  if (entries.length === 0) {
    return [];
  }
  const foldId = evidenceRowId(itemPath);
  const rows: Row[] = [
    evidenceRow(foldId, itemPath, itemPath, EVIDENCE_LABEL),
  ];
  collectNotes(entries, foldId, itemPath, rows);
  return rows;
}

/**
 * Collect every word of an item that search should reach.
 *
 * @param item - A delivered item.
 * @returns The item's searchable text, lowercased.
 */
export function itemSearchText(item: Item): string {
  const parts: string[] = [item.glance, item.explanation];
  for (const entry of item.forensics ?? []) {
    parts.push(forensicText(entry));
  }
  for (const table of item.tables ?? []) {
    parts.push(table.caption, ...table.columns, ...table.rows.flat());
  }
  for (const thread of item.questions ?? []) {
    for (const turn of thread.turns) {
      parts.push(turn.text);
    }
  }
  return parts.join(" ").toLowerCase();
}

/**
 * Flatten one forensic entry to text.
 *
 * @param entry - Raw evidence or a nested note.
 * @returns The entry's text.
 */
export function forensicText(entry: Forensic): string {
  if (typeof entry === "string") {
    return entry;
  }
  const children = (entry.children ?? []).map(forensicText);
  return [entry.title, entry.body, ...children].join(" ");
}

/**
 * Add a row for each note in one list, and for everything under it.
 *
 * @param entries - The evidence entries of one owner, in document order.
 * @param parentId - Row id of the owner.
 * @param anchorId - Row id of the item all of this belongs to.
 * @param rows - The rows collected so far, appended to in painted order.
 */
function collectNotes(
  entries: Forensic[],
  parentId: string,
  anchorId: string,
  rows: Row[],
): void {
  entries.forEach((entry, index) => {
    if (typeof entry === "string") {
      return;
    }
    const note: NestedNote = entry;
    const id = noteRowId(parentId, index);
    rows.push(evidenceRow(id, anchorId, parentId, note.title));
    collectNotes(note.children ?? [], id, anchorId, rows);
  });
}

/**
 * Build one evidence row.
 *
 * @param id - The row's id.
 * @param anchorId - Row id of the item a message written here attaches to.
 * @param parentId - Row id of its container.
 * @param label - What the row's head says.
 * @returns The row.
 */
function evidenceRow(
  id: string,
  anchorId: string,
  parentId: string,
  label: string,
): Row {
  return {
    id,
    kind: "evidence",
    anchorId,
    parentId,
    label,
    search: "",
    awaiting: false,
    human: false,
  };
}
