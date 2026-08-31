import {
  createEffect,
  createMemo,
  createSignal,
  type Accessor,
} from "solid-js";
import {
  createComposer,
  postJson,
  type Composer,
  type ComposeTarget,
} from "./composer";
import { composeRow } from "./cursor";
import {
  isStructuredCurrentState,
  type BriefDocument,
} from "./document";
import { createHints, type Hints } from "./hints";
import { createHumanState, type HumanState } from "./human-state";
import { isTypingTarget, resolveAction, type Action } from "./keys";
import {
  createNavigation,
  type Navigation,
  type Overlay,
} from "./navigation";
import {
  CURRENT_STATE_ROOT_ID,
  outline,
  type Row,
} from "./outline";
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
  globalMessageRow: Accessor<Row | null>;
  rowShortcutNumber: (rowId: string) => number | null;
  suggestionShortcutNumber: (rowId: string, index: number) => number | null;
  composeAt: (row: Row) => void;
  composeGlobally: () => void;
  run: (action: Action) => void;
  handleKey: (event: KeyboardEvent) => void;
}
export const GLOBAL_MESSAGE_ROW_ID = "//message-agent";
export function createBriefState(brief: Accessor<BriefDocument>): BriefState {
  const human = createHumanState();
  const pending = createPending(brief);
  const [activityReady, setActivityReady] = createSignal(false);
  let activeComposer: Composer | undefined;
  const activeUpdateIds = createMemo<ReadonlySet<string>>(() => {
    activityReady();
    if (activeComposer === undefined) {
      return new Set();
    }
    const rows = outline(brief());
    const byId = new Map(rows.map((row) => [row.id, row]));
    const active = new Set<string>();
    for (const row of rows) {
      const working = activeComposer.target()?.rowId === row.id
        || activeComposer.sendingAt(row.id)
        || activeComposer.pendingAt(row.id).length !== 0
        || pending.sessionActiveAt(row.id)
        || activeComposer.signalPendingAt(row.id)
        || activeComposer.signalWorkingAt(row.id);
      if (!working) {
        continue;
      }
      let ancestor: Row | undefined = row;
      while (ancestor !== undefined) {
        if (ancestor.kind === "update") {
          active.add(ancestor.id);
          break;
        }
        ancestor = ancestor.parentId === null
          ? undefined
          : byId.get(ancestor.parentId);
      }
    }
    return active;
  });
  const nav = createNavigation(
    brief,
    human,
    (updateId) => activeUpdateIds().has(updateId),
  );
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
  activeComposer = composer;
  setActivityReady(true);
  const globalMessageRow = createMemo<Row | null>(() => {
    const latest = brief().updates.at(-1);
    const current = brief().current_state;
    const anchorId = latest?.id
      ?? (current !== undefined && isStructuredCurrentState(current)
        ? CURRENT_STATE_ROOT_ID
        : undefined);
    if (anchorId === undefined) {
      return null;
    }
    return {
      id: GLOBAL_MESSAGE_ROW_ID,
      kind: "update",
      anchorId,
      parentId: null,
      label: "Message agent",
      search: "",
      awaiting: false,
      human: true,
    };
  });
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
    const target = composer.target();
    const id = target?.rowId;
    const global = id === GLOBAL_MESSAGE_ROW_ID;
    const globalRow = global ? globalMessageRow() : null;
    if (
      global
      && target !== null
      && globalRow !== null
      && target.anchorId !== globalRow.anchorId
      && pending.failureAt(target.rowId) === null
    ) {
      composer.retarget({
        rowId: globalRow.id,
        anchorId: globalRow.anchorId,
        keepOpenAfterSubmit: true,
      });
    }
    const row = id === undefined
      ? undefined
      : global ? globalRow ?? undefined : nav.row(id);
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
    const absent = id !== undefined && (
      row === undefined
      || (global && target !== null && nav.row(target.anchorId) === undefined)
    );
    const folded = id !== undefined && nav.query() === ""
      && !global
      && !nav.painted().some((row) => row.id === id);
    if (id !== undefined && (absent || folded)) {
      queueMicrotask(() => {
        const current = composer.target()?.rowId;
        const stillGlobal = id === GLOBAL_MESSAGE_ROW_ID;
        const stillTarget = composer.target();
        const stillAbsent = stillGlobal
          ? stillTarget === null
            || nav.row(stillTarget.anchorId) === undefined
          : nav.row(id) === undefined;
        const stillFolded = nav.query() === ""
          && !stillGlobal
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
    if (row.kind !== "update" || !alreadyUnfolded) {
      nav.setOpen(row.id, true);
    }
    if (!alreadyOpen || alreadyUnfolded) {
      composer.toggleAt(target);
    }
    nav.select(row.id, { scroll: false });
    if (composer.isOpenAt(row.id)) focusLater(".composer textarea");
  };
  const composeGlobally = (): void => {
    const row = globalMessageRow();
    if (row === null) {
      return;
    }
    composer.toggleAt({
      rowId: row.id,
      anchorId: row.anchorId,
      keepOpenAfterSubmit: true,
    });
    if (composer.isOpenAt(row.id)) {
      focusLater(".global-message .composer textarea");
    }
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
      "compose-global": composeGlobally,
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
    globalMessageRow,
    rowShortcutNumber,
    suggestionShortcutNumber,
    composeAt,
    composeGlobally,
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
