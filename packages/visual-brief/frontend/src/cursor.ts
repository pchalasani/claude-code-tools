import { ancestorIds, type Row, type RowKind } from "./outline";
export type Edge = "top" | "bottom";
export function moveByRow(
  rows: Row[],
  cursorId: string | null,
  delta: number,
): string | null {
  return moveAmong(rows, cursorId, rows, delta);
}
export function moveByKind(
  rows: Row[],
  cursorId: string | null,
  kind: RowKind,
  delta: number,
): string | null {
  return moveAmong(
    rows,
    cursorId,
    rows.filter((row) => row.kind === kind),
    delta,
  );
}
function moveAmong(
  rows: Row[],
  cursorId: string | null,
  candidates: Row[],
  delta: number,
): string | null {
  const first = candidates[0];
  const last = candidates[candidates.length - 1];
  if (first === undefined || last === undefined) {
    return cursorId;
  }
  const here = indexOf(rows, cursorId);
  if (here < 0) {
    return delta > 0 ? first.id : last.id;
  }
  const current = indexOf(candidates, cursorId);
  if (current >= 0) {
    return candidates[current + delta]?.id ?? cursorId;
  }
  const nearest = delta > 0
    ? candidates.find((row) => indexOf(rows, row.id) > here)
    : [...candidates]
      .reverse()
      .find((row) => indexOf(rows, row.id) < here);
  return nearest?.id ?? cursorId;
}
export function edgeRow(rows: Row[], edge: Edge): string | null {
  const row = edge === "top" ? rows[0] : rows[rows.length - 1];
  return row?.id ?? null;
}
export function effectiveCursor(
  rows: Row[],
  selectedId: string | null,
): string | null {
  return rows.some((row) => row.id === selectedId) ? selectedId : null;
}
export function filterRows(rows: Row[], query: string): Row[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") {
    return rows;
  }
  const kept = new Set<string>();
  for (const row of rows) {
    if (!row.search.includes(needle)) {
      continue;
    }
    kept.add(row.id);
    ancestorIds(row.id).forEach((id) => kept.add(id));
  }
  return rows.filter((row) => kept.has(row.id));
}
export function countItems(rows: Row[]): number {
  return rows.filter((row) => row.kind === "item").length;
}
export function composeRow(
  rows: Row[],
  rowId: string | null,
): Row | null {
  const row = rows.find((candidate) => candidate.id === rowId);
  if (row === undefined) {
    return null;
  }
  if (row.kind === "evidence") {
    return rows.find((candidate) => candidate.id === row.anchorId) ?? null;
  }
  if (row.kind !== "update") {
    return row;
  }
  return rows.find(
    (candidate) =>
      candidate.kind === "lane" && candidate.parentId === row.id,
  ) ?? null;
}
function indexOf(rows: Row[], id: string | null): number {
  return id === null ? -1 : rows.findIndex((row) => row.id === id);
}
