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
interface SelectOptions {
  scroll?: boolean;
  dropFilter?: boolean;
  visit?: boolean;
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
  select: (id: string, options?: SelectOptions) => void;
  move: (kind: RowKind | "row", delta: number) => void;
  jump: (edge: Edge) => void;
  retreat: () => void;
  toOpenChat: () => void;
  toLatestBriefingAttention: () => void;
  isFresh: (id: string) => boolean;
  isOpen: (id: string) => boolean;
  toggle: (id: string) => void;
  setOpen: (id: string, open: boolean) => void;
  openMovedAncestor: (id: string) => void;
  expandAll: () => void;
  collapseAll: () => void;
  chatRevealActive: Accessor<boolean>;
  toggleChatReveal: () => void;
  query: Accessor<string>;
  setQuery: (value: string) => void;
  matchCount: Accessor<number>;
  overlay: Accessor<Overlay>;
  openOverlay: (overlay: Overlay) => void;
  closeOverlay: () => void;
  latestBriefingAttentionCount: Accessor<number>;
  latestBriefingFresh: Accessor<boolean>;
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
  const initialLatestBriefing = brief().updates.at(-1)?.id;
  if (
    initialLatestBriefing !== undefined
    && human.latestBriefing() === null
  ) {
    human.visitBriefing(initialLatestBriefing);
  }
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
    effectiveCursor(painted(), human.cursor())
      ?? edgeRow(painted(), "top"),
  );
  const outstanding = createMemo(() => openness.outstanding(rows()));
  const latestBriefingAttention = createMemo(() => {
    const latestBriefingId = brief().updates.at(-1)?.id;
    if (latestBriefingId === undefined) {
      return [];
    }
    return rows().filter(
      (row) => row.kind === "thread"
        && openness.isOutstanding(row, rows())
        && ancestorRowIds(rows(), row.id).includes(latestBriefingId),
    );
  });
  const latestBriefingFresh = createMemo(() => {
    const latest = brief().updates.at(-1)?.id;
    return latest !== undefined && human.latestBriefing() !== latest;
  });
  const ancestors = (id: string): string[] => ancestorRowIds(rows(), id);
  const blurActiveControl = (): void => {
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  };
  const visit = (row: Row): void => {
    const latest = brief().updates.at(-1)?.id;
    if (
      latest !== undefined
      && (row.id === latest || ancestors(row.id).includes(latest))
    ) {
      human.visitBriefing(latest);
    }
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
    options?: SelectOptions,
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
    if (options?.visit !== false) {
      visit(row);
    }
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
    visit(row);
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
    pointAt: (id) => select(id, { scroll: false, visit: false }),
    move: step,
    jump: (edge) => {
      const next = edgeRow(painted(), edge);
      if (next !== null) {
        select(next);
      }
    },
    retreat: () => {
      const current = cursorId();
      if (current === null) {
        return;
      }
      blurActiveControl();
      if (baseOpen(current)) {
        human.choose(current, false);
        explicitSelectionTookOver();
        return;
      }
      const parent = index.row(current)?.parentId;
      if (parent !== null && parent !== undefined) {
        select(parent);
      }
    },
    toOpenChat: () => {
      const chatsNeedingAttention = outstanding();
      const next = cycleAfter(chatsNeedingAttention, cursorId());
      if (next !== null) {
        select(next, { dropFilter: true });
      }
    },
    toLatestBriefingAttention: () => {
      const next = cycleAfter(latestBriefingAttention(), cursorId());
      if (next !== null) {
        human.choose(next, true);
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
    collapseAll: () => {
      human.chooseAll(foldChoiceIds(rows()), false);
      const first = edgeRow(painted(), "top");
      if (first !== null) {
        select(first);
      }
    },
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
    query,
    setQuery,
    matchCount: createMemo(() => countItems(filterRows(rows(), query()))),
    overlay,
    openOverlay: setOverlay,
    closeOverlay: () => setOverlay("none"),
    latestBriefingAttentionCount: createMemo(
      () => latestBriefingAttention().length,
    ),
    latestBriefingFresh,
  };
}
function cycleAfter(rows: Row[], currentId: string | null): string | null {
  if (rows.length === 0) {
    return null;
  }
  const index = rows.findIndex((row) => row.id === currentId);
  return rows[(index + 1) % rows.length]?.id ?? null;
}
