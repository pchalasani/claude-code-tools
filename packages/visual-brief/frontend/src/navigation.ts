import { createMemo, createSignal, type Accessor } from "solid-js";
import {
  chatRows,
  countItems,
  edgeRow,
  effectiveCursor,
  filterRows,
  moveByKind,
  moveByRow,
  type Edge,
} from "./cursor";
import type { BriefDocument } from "./document";
import type { HumanState } from "./human-state";
import { createRowIndex } from "./live-rows";
import { createOpenness, foldChoiceIds } from "./open";
import { ancestorIds, type Row, type RowKind } from "./outline";
import { explicitSelectionTookOver } from "./pointer";
import { scrollRowIntoView } from "./reveal";
export type Overlay = "none" | "search" | "help";
export interface Navigation {
  visible: Accessor<Row[]>;
  painted: Accessor<Row[]>;
  isVisible: (id: string) => boolean;
  row: (id: string) => Row | undefined;
  cursorId: Accessor<string | null>;
  currentId: () => string | null;
  isCursor: (id: string) => boolean;
  pointAt: (id: string) => void;
  select: (id: string, options?: { scroll?: boolean; dropFilter?: boolean }) => void;
  move: (kind: RowKind | "row", delta: number) => void;
  jump: (edge: Edge) => void;
  toOpenChat: () => void;
  isFresh: (id: string) => boolean;
  isOpen: (id: string) => boolean;
  toggle: (id: string) => void;
  setOpen: (id: string, open: boolean) => void;
  expandAll: () => void;
  collapseAll: () => void;
  chats: Accessor<boolean>;
  toggleChats: () => void;
  chatCount: Accessor<number>;
  ordinal: (id: string) => number | null;
  query: Accessor<string>;
  setQuery: (value: string) => void;
  matchCount: Accessor<number>;
  overlay: Accessor<Overlay>;
  openOverlay: (overlay: Overlay) => void;
  closeOverlay: () => void;
  awaitingCount: Accessor<number>;
  outstandingCount: Accessor<number>;
}
export function createNavigation(
  brief: Accessor<BriefDocument>,
  human: HumanState,
): Navigation {
  const index = createRowIndex(brief);
  const rows = index.rows;
  const openness = createOpenness(
    rows,
    () => human.chosen,
    () => human.seen,
  );
  const [query, setQuery] = createSignal("");
  const [chats, setChats] = createSignal(false);
  const [overlay, setOverlay] = createSignal<Overlay>("none");
  const filterActive = (): boolean =>
    chats() || query().trim().length > 0;
  const visible = createMemo(() =>
    chats() ? chatRows(rows()) : filterRows(rows(), query()),
  );
  const visibleIds = createMemo(
    () => new Set(visible().map((row) => row.id)),
  );
  const painted = createMemo(() => {
    if (filterActive()) {
      for (const row of visible()) {
        openness.isOpen(row);
      }
      return visible();
    }
    return openness.painted(rows());
  });
  const cursorId = createMemo(() =>
    effectiveCursor(painted(), human.cursor()),
  );
  const ordinals = createMemo(() => {
    const result = new Map<string, number>();
    let next = 0;
    for (const row of painted()) {
      if (row.kind === "item") {
        next += 1;
        result.set(row.id, next);
      }
    }
    return result;
  });
  const outstanding = createMemo(() => openness.outstanding(rows()));
  const visit = (row: Row): void => {
    if (
      row.kind === "thread"
      && !row.awaiting
      && row.parentThreadId !== undefined
      && row.answerState !== undefined
    ) {
      human.visit(row.parentThreadId, row.answerState);
    }
  };
  const select = (
    id: string,
    options?: { scroll?: boolean; dropFilter?: boolean },
  ): void => {
    const row = index.row(id);
    if (row === undefined) {
      return;
    }
    const filtered = options?.dropFilter === true || !visibleIds().has(id);
    if (filtered) {
      setQuery("");
      setChats(false);
      for (const ancestor of ancestorIds(id)) {
        human.choose(ancestor, true);
      }
    }
    explicitSelectionTookOver();
    human.select(id);
    visit(row);
    if (options?.scroll !== false) {
      queueMicrotask(() => scrollRowIntoView(id));
    }
  };
  const step = (kind: RowKind | "row", delta: number): void => {
    const current = cursorId();
    const next = kind === "row"
      ? moveByRow(painted(), current, delta)
      : moveByKind(painted(), current, kind, delta);
    if (next !== null && next !== current) {
      select(next);
    }
  };
  const baseOpen = (id: string): boolean => {
    const row = index.row(id);
    return row !== undefined && openness.isOpen(row);
  };
  const shownOpen = (id: string): boolean =>
    (filterActive() && visibleIds().has(id))
    || baseOpen(id);
  const toggle = (id: string): void => {
    const row = index.row(id);
    if (row === undefined) {
      return;
    }
    human.choose(id, !baseOpen(id));
  };
  return {
    visible,
    painted,
    isVisible: (id) => visibleIds().has(id),
    row: index.row,
    cursorId,
    currentId: cursorId,
    isCursor: (id) => cursorId() === id,
    select,
    pointAt: (id) => select(id, { scroll: false }),
    move: step,
    jump: (edge) => {
      const next = edgeRow(painted(), edge);
      if (next !== null) {
        select(next);
      }
    },
    toOpenChat: () => {
      const chatsNeedingAttention = outstanding();
      const next = cycleAfter(chatsNeedingAttention, cursorId());
      if (next !== null) {
        select(next, { dropFilter: true });
      }
    },
    isFresh: (id) => {
      const row = index.row(id);
      return row?.kind === "thread"
        && !row.awaiting
        && openness.isOutstanding(row, rows());
    },
    isOpen: shownOpen,
    toggle,
    setOpen: (id, open) => {
      const row = index.row(id);
      if (row !== undefined) {
        human.choose(id, open);
      }
    },
    expandAll: () => human.chooseAll(foldChoiceIds(rows()), true),
    collapseAll: () => human.chooseAll(foldChoiceIds(rows()), false),
    chats,
    toggleChats: () => setChats((value) => !value),
    chatCount: createMemo(() => outstanding().length),
    ordinal: (id) => ordinals().get(id) ?? null,
    query,
    setQuery,
    matchCount: createMemo(() => countItems(filterRows(rows(), query()))),
    overlay,
    openOverlay: setOverlay,
    closeOverlay: () => setOverlay("none"),
    awaitingCount: index.awaitingCount,
    outstandingCount: createMemo(() => outstanding().length),
  };
}
function cycleAfter(rows: Row[], currentId: string | null): string | null {
  if (rows.length === 0) {
    return null;
  }
  const index = rows.findIndex((row) => row.id === currentId);
  return rows[(index + 1) % rows.length]?.id ?? null;
}
