import type { Forensic, Item, NestedNote } from "./document";
import type { Row } from "./outline";
export const EVIDENCE_LABEL = "Raw evidence and deeper forensics";
const RESERVED = "#~";
const DERIVED_MARK = "~";
const NAME_LIMIT = 48;
const FALLBACK_NAME = "note";
const USABLE_NAME = /^[^ \t\n\r\f#/~]+$/u;
export function evidenceRowId(itemPath: string): string {
  return `${itemPath}${RESERVED}evidence`;
}
export function noteRowId(parentId: string, name: string): string {
  return `${parentId}${RESERVED}${name}`;
}
export type PaintedEvidence =
  | { kind: "text"; text: string }
  | { kind: "note"; id: string; note: NestedNote };
type NamedEvidence =
  | { kind: "text"; text: string }
  | { kind: "note"; name: string; note: NestedNote };
export function paintedEvidence(
  entries: Forensic[],
  parentId: string,
): PaintedEvidence[] {
  const named: NamedEvidence[] = entries.map((entry) =>
    typeof entry === "string"
      ? { kind: "text", text: entry }
      : { kind: "note", name: noteName(entry), note: entry },
  );
  const shared = sharedNames(named);
  return named.map((one): PaintedEvidence => {
    if (one.kind === "text") {
      return one;
    }
    if (shared.has(one.name)) {
      return { kind: "text", text: forensicText(one.note) };
    }
    return { kind: "note", id: noteRowId(parentId, one.name), note: one.note };
  });
}
export function evidenceRows(item: Item, itemPath: string): Row[] {
  const entries = item.forensics ?? [];
  if (entries.length === 0) {
    return [];
  }
  const foldId = evidenceRowId(itemPath);
  const search = entries.map(forensicText).join(" ").toLowerCase();
  const rows: Row[] = [
    evidenceRow(foldId, itemPath, itemPath, EVIDENCE_LABEL, search),
  ];
  collectNotes(entries, foldId, itemPath, rows);
  return rows;
}
export function itemSearchText(item: Item): string {
  const parts: string[] = [item.glance, item.explanation];
  for (const table of item.tables ?? []) {
    parts.push(table.caption, ...table.columns, ...table.rows.flat());
  }
  return parts.join(" ").toLowerCase();
}
export function forensicText(entry: Forensic): string {
  if (typeof entry === "string") {
    return entry;
  }
  const children = (entry.children ?? []).map(forensicText);
  return [entry.title, entry.body, ...children].join(" ");
}
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
    const search = forensicText(painted.note).toLowerCase();
    rows.push(evidenceRow(
      painted.id, anchorId, parentId, painted.note.title, search,
    ));
    collectNotes(painted.note.children ?? [], painted.id, anchorId, rows);
  }
}
function noteName(note: NestedNote): string {
  const declared = note.id;
  if (declared !== undefined && USABLE_NAME.test(declared)) {
    return declared;
  }
  return `${DERIVED_MARK}${derivedName(note)}`;
}
function sharedNames(named: NamedEvidence[]): Set<string> {
  const seen = new Set<string>();
  const shared = new Set<string>();
  for (const one of named) {
    if (one.kind === "text") {
      continue;
    }
    if (seen.has(one.name)) {
      shared.add(one.name);
    }
    seen.add(one.name);
  }
  return shared;
}
function derivedName(note: NestedNote): string {
  const slug = note.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/gu, "-")
    .slice(0, NAME_LIMIT)
    .replace(/^-+|-+$/gu, "");
  return slug === "" ? FALLBACK_NAME : slug;
}
function evidenceRow(
  id: string,
  anchorId: string,
  parentId: string,
  label: string,
  search: string,
): Row {
  return {
    id,
    kind: "evidence",
    anchorId,
    parentId,
    label,
    search,
    awaiting: false,
    human: false,
  };
}
