import { batch, createMemo, createSignal, type Accessor } from "solid-js";
import {
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
import { ancestorRowIds, type Row, type RowKind } from "./outline";
import { explicitSelectionTookOver } from "./pointer";
import { preserveWindowScroll, scrollRowIntoView } from "./reveal";
export type Overlay = "none" | "search" | "help";
interface ChatReveal {
  layout: Map<string, boolean | undefined>;
  pathIds: ReadonlySet<string>;
}
export interface Navigation {
  visible: Accessor<Row[]>;
  painted: Accessor<Row[]>;
  isVisible: (id: string) => boolean;
  isPainted: (id: string) => boolean;
  row: (id: string) => Row | undefined;
  ancestors: (id: string) => string[];
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
  openMovedAncestor: (id: string) => void;
  expandAll: () => void;
  collapseAll: () => void;
  chatRevealActive: Accessor<boolean>;
  toggleChatReveal: () => void;
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
  const [overlay, setOverlay] = createSignal<Overlay>("none");
  const [chatReveal, setChatReveal] = createSignal<ChatReveal | null>(null);
  const filterActive = (): boolean => query().trim().length > 0;
  const visible = createMemo(() => filterRows(rows(), query()));
  const visibleIds = createMemo(
    () => new Set(visible().map((row) => row.id)),
  );
  const painted = createMemo(() => {
    if (filterActive()) {
      const revealed = chatReveal()?.pathIds;
      return rows().filter((row) => {
        const included = visibleIds().has(row.id)
          || revealed?.has(row.id) === true;
        if (included) {
          openness.isOpen(row);
        }
        return included;
      });
    }
    return openness.painted(rows());
  });
  const paintedIds = createMemo(
    () => new Set(painted().map((row) => row.id)),
  );
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
  const ancestors = (id: string): string[] => ancestorRowIds(rows(), id);
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
    const filtered = options?.dropFilter === true || !paintedIds().has(id);
    if (filtered) {
      setQuery("");
      for (const ancestor of ancestors(id)) {
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
    isPainted: (id) => paintedIds().has(id),
    row: index.row,
    ancestors,
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
    openMovedAncestor: (id) => {
      const row = index.row(id);
      if (row !== undefined) {
        const captured = chatReveal();
        if (captured?.layout.has(id) === true) {
          captured.layout.set(id, true);
        }
        human.choose(id, true);
      }
    },
    expandAll: () => human.chooseAll(foldChoiceIds(rows()), true),
    collapseAll: () => human.chooseAll(foldChoiceIds(rows()), false),
    chatRevealActive: () => chatReveal() !== null,
    toggleChatReveal: () => {
      explicitSelectionTookOver();
      preserveWindowScroll(() => {
        const captured = chatReveal();
        if (captured !== null) {
          const surviving = new Set(rows().map((row) => row.id));
          batch(() => {
            human.chooseEach(
              [...captured.layout].map(([id, choice]) => [
                id,
                surviving.has(id) ? choice : undefined,
              ] as const),
            );
            setChatReveal(null);
          });
          return;
        }
        const layout = new Map<string, boolean | undefined>();
        const needed = new Set<string>();
        const pathIds = new Set<string>();
        for (const row of rows()) {
          baseOpen(row.id);
          layout.set(
            row.id,
            Object.hasOwn(human.chosen, row.id)
              ? human.chosen[row.id]
              : undefined,
          );
          if (row.kind !== "thread" || !row.human) {
            continue;
          }
          for (const id of [row.id, ...ancestors(row.id)]) {
            pathIds.add(id);
            if (!baseOpen(id)) {
              needed.add(id);
            }
          }
        }
        batch(() => {
          setChatReveal({ layout, pathIds });
          human.chooseAll(needed, true);
        });
      });
    },
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
