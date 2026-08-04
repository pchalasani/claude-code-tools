import { createEffect, createMemo, type Accessor } from "solid-js";
import {
  createComposer,
  postJson,
  type Composer,
  type ComposeTarget,
} from "./composer";
import { composeRow } from "./cursor";
import type { BriefDocument } from "./document";
import { createHints, type Hints } from "./hints";
import { createHumanState, type HumanState } from "./human-state";
import { isTypingTarget, resolveAction, type Action } from "./keys";
import {
  createNavigation,
  type Navigation,
  type Overlay,
} from "./navigation";
import type { Row } from "./outline";
import {
  createPending,
  suggestedReplyAnswered,
  type Pending,
} from "./pending";
import { focusLater } from "./reveal";
import { createSignalWork } from "./signal-work";
export interface BriefState {
  readonly brief: BriefDocument;
  human: HumanState;
  nav: Navigation;
  composer: Composer;
  hints: Hints;
  pending: Pending;
  owningItem: Accessor<Row | null>;
  feedbackItem: Accessor<Row | null>;
  rowShortcutNumber: (rowId: string) => number | null;
  suggestionShortcutNumber: (rowId: string, index: number) => number | null;
  composeAt: (row: Row) => void;
  run: (action: Action) => void;
  handleKey: (event: KeyboardEvent) => void;
}
export function createBriefState(brief: Accessor<BriefDocument>): BriefState {
  const human = createHumanState();
  const pending = createPending(brief);
  const nav = createNavigation(brief, human);
  const signalWork = createSignalWork({
    latestUpdateId: () => brief().updates.at(-1)?.id,
    rowExists: (rowId) => nav.row(rowId) !== undefined,
    answered: (rowId, text, at) =>
      suggestedReplyAnswered(brief(), rowId, text, at),
  });
  const composer = createComposer(
    postJson,
    () => undefined,
    pending,
    human,
    signalWork,
  );
  const owningItem = createMemo(() => {
    const selected = nav.currentId();
    if (selected === null) {
      return null;
    }
    return [selected, ...nav.ancestors(selected)]
      .map((id) => nav.row(id))
      .find((row) => row?.kind === "item") ?? null;
  });
  const feedbackItem = createMemo(() => {
    const item = owningItem();
    return item !== null
      && (item.suggestions?.length ?? 0) > 0
      && nav.isPainted(item.id)
      && nav.isOpen(item.id)
      ? item
      : null;
  });
  const numberedRows = createMemo(() => {
    const currentId = nav.currentId();
    const current = currentId === null ? undefined : nav.row(currentId);
    if (current === undefined) {
      return [];
    }
    const parentId = nav.isOpen(current.id) ? current.id : current.parentId;
    const suggestionCount = feedbackItem()?.suggestions?.length ?? 0;
    const rowLimit = Math.max(0, 9 - suggestionCount);
    return nav.painted()
      .filter((row) => row.parentId === parentId)
      .slice(0, rowLimit);
  });
  const rowShortcutNumber = (rowId: string): number | null => {
    const index = numberedRows().findIndex((row) => row.id === rowId);
    return index === -1 ? null : index + 1;
  };
  const suggestionShortcutNumber = (
    rowId: string,
    index: number,
  ): number | null => {
    const item = feedbackItem();
    if (item?.id !== rowId || item.suggestions?.[index] === undefined) {
      return null;
    }
    const number = numberedRows().length + index + 1;
    return number <= 9 ? number : null;
  };
  let previousTarget: { id: string; ancestors: string[] } | null = null;
  let previousCursor: { id: string; ancestors: string[] } | null = null;
  createEffect(() => {
    const cursorId = human.cursor();
    const cursorRow = cursorId === null ? undefined : nav.row(cursorId);
    const cursorAncestors = cursorId === null ? [] : nav.ancestors(cursorId);
    const previousSelection = previousCursor;
    const cursorMoved = cursorRow !== undefined
      && previousSelection !== null
      && previousSelection.id === cursorId
      && (
        cursorAncestors.length !== previousSelection.ancestors.length
        || cursorAncestors.some(
          (ancestor, index) => ancestor !== previousSelection.ancestors[index],
        )
      );
    previousCursor = cursorId === null
      ? null
      : { id: cursorId, ancestors: cursorAncestors };
    if (cursorMoved) {
      for (const ancestor of [...cursorAncestors].reverse()) {
        nav.openMovedAncestor(ancestor);
      }
    }
    const id = composer.target()?.rowId;
    const row = id === undefined ? undefined : nav.row(id);
    const ancestors = id === undefined ? [] : nav.ancestors(id);
    const previous = previousTarget;
    const moved = row !== undefined
      && previous !== null
      && previous.id === id
      && (
        ancestors.length !== previous.ancestors.length
        || ancestors.some(
          (ancestor, index) => ancestor !== previous.ancestors[index],
        )
      );
    previousTarget = id === undefined ? null : { id, ancestors };
    if (moved) {
      for (const ancestor of [...ancestors].reverse()) {
        nav.openMovedAncestor(ancestor);
      }
    }
    const absent = id !== undefined && row === undefined;
    const folded = id !== undefined && nav.query() === ""
      && !nav.painted().some((row) => row.id === id);
    if (id !== undefined && (absent || folded)) {
      queueMicrotask(() => {
        const current = composer.target()?.rowId;
        const stillAbsent = nav.row(id) === undefined;
        const stillFolded = nav.query() === ""
          && !nav.painted().some((row) => row.id === id);
        if (current === id && (stillAbsent || stillFolded)) {
          composer.close();
        }
      });
    }
  });
  const hints = createHints({
    rows: nav.painted,
    select: nav.select,
  });
  const composeAt = (row: Row): void => {
    const target: ComposeTarget = {
      rowId: row.id,
      anchorId: row.anchorId,
      ...(row.kind === "thread" && row.parentThreadId !== undefined
        ? { parentId: row.parentThreadId } : {}),
    };
    const alreadyOpen = composer.isOpenAt(row.id);
    const alreadyUnfolded = nav.isOpen(row.id);
    for (const id of [...nav.ancestors(row.id)].reverse()) {
      if (!nav.isOpen(id)) nav.setOpen(id, true);
    }
    nav.setOpen(row.id, true);
    if (!alreadyOpen || alreadyUnfolded) {
      composer.toggleAt(target);
    }
    nav.select(row.id, { scroll: false });
    if (composer.isOpenAt(row.id)) focusLater(".composer textarea");
  };
  const composeAtCursor = (): void => {
    const selected = human.cursor();
    const targeted = composer.target();
    const row = targeted?.rowId === selected && selected !== null
      ? nav.row(selected) ?? null
      : composeRow(nav.painted(), nav.currentId());
    if (row !== null) composeAt(row);
  };
  const closeOne = (): void => {
    if (nav.overlay() === "help") {
      nav.closeOverlay();
    } else if (nav.overlay() === "search") {
      nav.setQuery("");
      nav.closeOverlay();
    } else if (composer.target() !== null) {
      composer.escape();
    } else {
      nav.retreat();
    }
  };
  const showOverlay = (overlay: Exclude<Overlay, "none">): void => {
    hints.leave();
    nav.openOverlay(overlay);
  };
  const run = (action: Action): void => {
    const sendSuggestion = (index: number): void => {
      const item = feedbackItem();
      const suggestion = item?.suggestions?.[index];
      if (item !== null && suggestion !== undefined) {
        void composer.sendSignal(item.id, item.anchorId, suggestion);
      }
    };
    const activateDigit = (number: number): void => {
      const row = numberedRows()[number - 1];
      if (row !== undefined) {
        nav.select(row.id);
        return;
      }
      sendSuggestion(number - numberedRows().length - 1);
    };
    const actions: Record<Action, () => void> = {
      "next-row": () => nav.move("row", 1),
      "previous-row": () => nav.move("row", -1),
      "next-lane": () => nav.move("lane", 1),
      "previous-lane": () => nav.move("lane", -1),
      toggle: () => {
        const id = nav.currentId();
        if (id !== null) {
          nav.toggle(id);
        }
      },
      "expand-all": nav.expandAll,
      "collapse-all": nav.collapseAll,
      compose: composeAtCursor,
      "next-awaiting": nav.toOpenChat,
      "reveal-chats": nav.toggleChatReveal,
      hints: () => {
        if (nav.overlay() !== "help") {
          hints.enter();
        }
      },
      search: () => {
        showOverlay("search");
        focusLater("#brief-search");
      },
      top: () => nav.jump("top"),
      bottom: () => nav.jump("bottom"),
      help: () => showOverlay("help"),
      close: closeOne,
      "digit-1": () => activateDigit(1),
      "digit-2": () => activateDigit(2),
      "digit-3": () => activateDigit(3),
      "digit-4": () => activateDigit(4),
      "digit-5": () => activateDigit(5),
      "digit-6": () => activateDigit(6),
      "digit-7": () => activateDigit(7),
      "digit-8": () => activateDigit(8),
      "digit-9": () => activateDigit(9),
    };
    actions[action]();
  };
  return {
    get brief(): BriefDocument {
      return brief();
    },
    human,
    nav,
    composer,
    hints,
    pending,
    owningItem,
    feedbackItem,
    rowShortcutNumber,
    suggestionShortcutNumber,
    composeAt,
    run,
    handleKey: (event) => {
      const chorded = event.ctrlKey || event.metaKey || event.altKey;
      const typing = isTypingTarget(event.target ?? null);
      if (!chorded && !typing && nav.overlay() === "help") {
        if (event.key === "Escape") {
          event.preventDefault();
          closeOne();
        }
        return;
      }
      if (!chorded && !typing && hints.handleKey(event.key)) {
        event.preventDefault();
        return;
      }
      const action = resolveAction(event);
      if (action !== null) {
        if (action.startsWith("digit-")) {
          const number = Number(action.at(-1));
          const row = numberedRows()[number - 1];
          const suggestionIndex = number - numberedRows().length - 1;
          if (
            row === undefined
            && feedbackItem()?.suggestions?.[suggestionIndex] === undefined
          ) {
            return;
          }
        }
        event.preventDefault();
        if (
          (action.endsWith("-row") || action.endsWith("-lane")
            || action === "top" || action === "bottom")
          && event.target instanceof HTMLElement
        ) {
          event.target.blur();
        }
        run(action);
      }
    },
  };
}
