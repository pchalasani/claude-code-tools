import type { Accessor } from "solid-js";
import type { ChosenMap, SeenMap } from "./human-state";
import { ancestorRowIds, type Row } from "./outline";
export interface Openness {
  isOpen: (row: Row) => boolean;
  painted: (rows: Row[]) => Row[];
  isOutstanding: (row: Row, rows: Row[]) => boolean;
  outstanding: (rows: Row[]) => Row[];
  born: (rowId: string) => boolean | undefined;
}
export function createOpenness(
  rows: Accessor<Row[]>,
  chosen: Accessor<Readonly<ChosenMap>>,
  seen: Accessor<Readonly<SeenMap>>,
): Openness {
  const defaults = new Map<string, boolean>();
  const outstanding = (row: Row, current: Row[]): boolean => {
    if (row.kind === "thread") {
      return threadOutstanding(row, seen());
    }
    return current.some(
      (candidate) =>
        candidate.kind === "thread"
        && ancestorRowIds(current, candidate.id).includes(row.id)
        && threadOutstanding(candidate, seen()),
    );
  };
  const isOpen = (row: Row): boolean => {
    const choice = chosen()[row.id];
    if (choice !== undefined) {
      return choice;
    }
    let initial = defaults.get(row.id);
    if (initial === undefined) {
      initial = bornDefault(row, rows(), seen());
      defaults.set(row.id, initial);
    }
    return initial;
  };
  return {
    isOpen,
    isOutstanding: outstanding,
    outstanding: (current) =>
      current.filter(
        (row) => row.kind === "thread" && threadOutstanding(row, seen()),
      ),
    born: (rowId) => defaults.get(rowId),
    painted: (current) => {
      const painted = new Set<string>();
      const byId = new Map(current.map((row) => [row.id, row]));
      return current.filter((row) => {
        if (row.parentId === null) {
          painted.add(row.id);
          isOpen(row);
          return true;
        }
        const parent = byId.get(row.parentId);
        if (
          parent !== undefined
          && painted.has(parent.id)
          && isOpen(parent)
        ) {
          painted.add(row.id);
          isOpen(row);
          return true;
        }
        return false;
      });
    },
  };
}
export function bornDefault(
  row: Row,
  rows: Row[],
  seen: Readonly<SeenMap>,
): boolean {
  if (row.kind === "state") {
    return true;
  }
  if (row.kind === "update") {
    return rows.find((candidate) => candidate.kind === "update")?.id === row.id;
  }
  if (row.kind === "lane") {
    return true;
  }
  if (row.kind === "thread") {
    return threadOutstanding(row, seen);
  }
  if (row.kind === "item") {
    return rows.some(
      (candidate) =>
        candidate.kind === "thread"
        && ancestorRowIds(rows, candidate.id).includes(row.id)
        && threadOutstanding(candidate, seen),
    );
  }
  return false;
}
export function threadOutstanding(
  row: Row,
  seen: Readonly<SeenMap>,
): boolean {
  if (row.kind !== "thread") {
    return false;
  }
  if (row.awaiting) {
    return true;
  }
  return (
    row.answerState !== undefined
    && row.parentThreadId !== undefined
    && seen[row.parentThreadId] !== row.answerState
  );
}
export function foldChoiceIds(rows: Row[]): string[] {
  return rows.map((row) => row.id);
}
