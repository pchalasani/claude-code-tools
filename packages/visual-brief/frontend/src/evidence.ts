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
 * reads them without knowing what they are, and every segment the page
 * invents opens with ``~``, which no id under a ``#`` may contain — so no
 * document can name a conversation that collides with one. The segment
 * carries no whitespace, because a row id ends up in ``aria-controls``, which
 * is a whitespace-separated list of id references: a space there would send
 * assistive technology looking for several ids that do not exist instead of
 * the one body the toggle opens.
 *
 * A note's segment is the note's own name — the ``id`` the author declared,
 * or a slug of its title — and never its position in the list. Position is
 * not identity: given notes ``[A, B]`` with the cursor saved on B, a publish
 * that prepends a note hands B's old position to A, and the restored cursor,
 * the fold state and every key that acts on a row would then quietly act on
 * the wrong note.
 *
 * The two kinds of name live in namespaces that cannot touch. A declared name
 * is used exactly as written, and a derived one is marked with the ``~`` a
 * declared name may not hold, so neither kind can ever spell the other. That
 * is what stops a name from changing hands between publishes: giving a note
 * an ``id``, or writing a sibling whose title slugs to that id, renames
 * nothing that already exists. What is left is the one collision nobody can
 * resolve — two siblings with the same title and no declared name — and those
 * are separated in document order, so two rows still can never claim one id.
 */

import type { Forensic, Item, NestedNote } from "./document";
import type { Row } from "./outline";

/** What the fold holding an item's evidence is called. */
export const EVIDENCE_LABEL = "Raw evidence and deeper forensics";

/** Separator opening a row id segment the page invented, not the document. */
const RESERVED = "#~";

/**
 * Mark worn by a name the page derived rather than read.
 *
 * A declared name may not hold ``~``, so a name opening with one is a name no
 * document can spell. That is the whole guarantee: the derived names and the
 * declared ones are separate namespaces, and neither can take from the other.
 */
const DERIVED_MARK = "~";

/** Longest name derived from a note's title. */
const NAME_LIMIT = 48;

/** The name a note falls back to when nothing usable can be slugged. */
const FALLBACK_NAME = "note";

/**
 * What a declared name has to look like to be usable as a row id segment.
 *
 * The renderer refuses to publish anything else, so this is the front end
 * holding its own end of that contract rather than trusting the blob it was
 * handed. What is checked is what would actually break the id the name lands
 * in: the separators, and the five characters HTML splits an id-reference
 * list on. Reading it as "whatever JavaScript calls whitespace" would be a
 * different rule from the renderer's, and a name the renderer accepted would
 * silently fall back to a title-derived one.
 */
const USABLE_NAME = /^[^ \t\n\r\f#/~]+$/u;

/**
 * Return the row id of the evidence hanging from one item.
 *
 * @param itemPath - Row id of the item.
 * @returns The evidence row's id.
 */
export function evidenceRowId(itemPath: string): string {
  return `${itemPath}${RESERVED}evidence`;
}

/**
 * Return the row id of one nested note.
 *
 * @param parentId - Row id of the evidence or note holding it.
 * @param name - The note's name among its siblings.
 * @returns The note's row id.
 */
export function noteRowId(parentId: string, name: string): string {
  return `${parentId}${RESERVED}${name}`;
}

/** One evidence entry as the page paints it. */
export type PaintedEvidence =
  | { kind: "text"; text: string }
  | { kind: "note"; id: string; note: NestedNote };

/**
 * Pair every evidence entry with the row id it paints under.
 *
 * The outline and the rendered tree both read this one list, so what the
 * cursor believes about a note and what the page paints for it are the same
 * string by construction.
 *
 * @param entries - The evidence entries of one owner, in document order.
 * @param parentId - Row id of the owner.
 * @returns Each entry in document order, notes carrying the row id they own.
 */
export function paintedEvidence(
  entries: Forensic[],
  parentId: string,
): PaintedEvidence[] {
  const taken = new Set<string>();
  return entries.map((entry): PaintedEvidence => {
    if (typeof entry === "string") {
      return { kind: "text", text: entry };
    }
    const name = claim(noteName(entry), taken);
    return { kind: "note", id: noteRowId(parentId, name), note: entry };
  });
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
  for (const painted of paintedEvidence(entries, parentId)) {
    if (painted.kind === "text") {
      continue;
    }
    rows.push(evidenceRow(painted.id, anchorId, parentId, painted.note.title));
    collectNotes(painted.note.children ?? [], painted.id, anchorId, rows);
  }
}

/**
 * Return the name one note answers to.
 *
 * A declared name is taken exactly as written: folding its case or cutting it
 * short would merge names the renderer accepted as different, and merged
 * names are settled by document order — which is the identity-by-position
 * this module exists to be rid of. Anything else is derived from the title
 * and marked as derived.
 *
 * @param note - The note to name.
 * @returns The name, before any collision with a sibling is settled.
 */
function noteName(note: NestedNote): string {
  const declared = note.id;
  if (declared !== undefined && USABLE_NAME.test(declared)) {
    return declared;
  }
  return `${DERIVED_MARK}${derivedName(note)}`;
}

/**
 * Take one name, or the first free variation of it.
 *
 * Two siblings that would answer to the same name are separated rather than
 * allowed to collide: a duplicate id would paint two rows the cursor cannot
 * tell apart.
 *
 * @param base - The name being asked for.
 * @param taken - Names already claimed by its siblings, added to here.
 * @returns A name none of those siblings holds.
 */
function claim(base: string, taken: Set<string>): string {
  let candidate = base;
  let suffix = 1;
  while (taken.has(candidate)) {
    suffix += 1;
    candidate = `${base}-${suffix}`;
  }
  taken.add(candidate);
  return candidate;
}

/**
 * Return the name a note that declares none is known by: a slug of its title.
 *
 * @param note - The note to name.
 * @returns A name safe inside a row id, before any collision is settled.
 */
function derivedName(note: NestedNote): string {
  const slug = note.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/gu, "-")
    .slice(0, NAME_LIMIT)
    .replace(/^-+|-+$/gu, "");
  return slug === "" ? FALLBACK_NAME : slug;
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
