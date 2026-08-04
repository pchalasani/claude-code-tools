import { evidenceRows, itemSearchText } from "./evidence";
import {
  isStructuredCurrentState,
  threadIsAwaiting,
  type BriefDocument,
  type Item,
  type Lane,
  type SuggestedReply,
  type Thread,
  type Update,
} from "./document";

/** Collision-free because authored update ids cannot contain a slash. */
export const CURRENT_STATE_ROOT_ID = "//current-state";
export type RowKind =
  | "state"
  | "update"
  | "lane"
  | "item"
  | "thread"
  | "evidence";

export interface Row {
  id: string;
  kind: RowKind;
  anchorId: string;
  parentThreadId?: string;
  parentId: string | null;
  label: string;
  search: string;
  awaiting: boolean;
  human: boolean;
  answerState?: string;
  suggestions?: SuggestedReply[];
}

export function orderedUpdates(brief: BriefDocument): Update[] {
  return [...brief.updates].reverse();
}

export function orderedThreads(threads: Thread[] | undefined): Thread[] {
  return [...(threads ?? [])].reverse();
}

export function laneRowId(updateId: string, lane: Lane): string {
  return `${updateId}/${lane.id}`;
}

export function itemRowId(lanePath: string, item: Item): string {
  return `${lanePath}/${item.id}`;
}

export function currentStateLaneRowId(lane: Lane): string {
  return `${CURRENT_STATE_ROOT_ID}/lanes/${lane.id}`;
}

export function currentStateItemRowId(item: Item): string {
  return `${CURRENT_STATE_ROOT_ID}/items/${item.id}`;
}

export function threadRowId(anchorId: string, thread: Thread): string {
  return `${anchorId}#${thread.id}`;
}

/** Return row ancestors by declared parent, nearest first. */
export function ancestorRowIds(rows: Row[], id: string): string[] {
  const byId = new Map(rows.map((row) => [row.id, row]));
  const found: string[] = [];
  let parent = byId.get(id)?.parentId ?? null;
  while (parent !== null) {
    found.push(parent);
    parent = byId.get(parent)?.parentId ?? null;
  }
  return found;
}

export function outline(brief: BriefDocument): Row[] {
  const rows: Row[] = [];
  const current = brief.current_state;
  if (current !== undefined && isStructuredCurrentState(current)) {
    const stateRow = containerRow(
      CURRENT_STATE_ROOT_ID,
      "state",
      null,
      current.headline,
      `${current.headline} ${current.summary}`.toLowerCase(),
    );
    rows.push(stateRow);
    appendThreads(rows, stateRow, current.questions);
    for (const lane of current.lanes ?? []) {
      const laneRow = appendLane(
        rows,
        lane,
        stateRow.id,
        currentStateLaneRowId(lane),
        currentStateItemRowId,
      );
      stateRow.awaiting ||= laneRow.awaiting;
    }
  }
  for (const update of orderedUpdates(brief)) {
    const updateRow = containerRow(
      update.id,
      "update",
      null,
      update.headline,
      `${update.headline} ${update.summary}`.toLowerCase(),
    );
    rows.push(updateRow);
    for (const lane of update.lanes ?? []) {
      const lanePath = laneRowId(update.id, lane);
      const laneRow = appendLane(
        rows,
        lane,
        update.id,
        lanePath,
        (item) => itemRowId(lanePath, item),
      );
      updateRow.awaiting ||= laneRow.awaiting;
    }
  }
  return rows;
}

export function awaitingThreadCount(rows: Row[]): number {
  return rows.filter((row) => row.kind === "thread" && row.awaiting).length;
}

function appendLane(
  rows: Row[],
  lane: Lane,
  parentId: string,
  lanePath: string,
  itemPath: (item: Item) => string,
): Row {
  const laneRow = containerRow(
    lanePath,
    "lane",
    parentId,
    lane.name,
    lane.name.toLowerCase(),
  );
  rows.push(laneRow);
  appendThreads(rows, laneRow, lane.questions);
  for (const item of lane.items ?? []) {
    const path = itemPath(item);
    const itemRow: Row = {
      id: path,
      kind: "item",
      anchorId: path,
      parentId: lanePath,
      label: item.glance,
      search: itemSearchText(item),
      awaiting: false,
      human: false,
      suggestions: item.suggestions,
    };
    rows.push(itemRow);
    rows.push(...evidenceRows(item, path));
    appendThreads(rows, itemRow, item.questions);
    laneRow.awaiting ||= itemRow.awaiting;
  }
  return laneRow;
}

function appendThreads(
  rows: Row[],
  owner: Row,
  threads: Thread[] | undefined,
): void {
  for (const thread of orderedThreads(threads)) {
    rows.push(threadRow(thread, owner.anchorId, owner.id));
    owner.awaiting ||= threadIsAwaiting(thread);
  }
}

function containerRow(
  id: string,
  kind: "state" | "update" | "lane",
  parentId: string | null,
  label: string,
  search: string,
): Row {
  return {
    id,
    kind,
    anchorId: id,
    parentId,
    label,
    search,
    awaiting: false,
    human: false,
  };
}

function threadRow(thread: Thread, anchorId: string, parentId: string): Row {
  const opening = thread.turns.find((turn) => turn.author === "human");
  const awaiting = threadIsAwaiting(thread);
  return {
    id: threadRowId(anchorId, thread),
    kind: "thread",
    anchorId,
    parentThreadId: thread.id,
    parentId,
    label: opening?.text ?? "Conversation",
    search: thread.turns.map((turn) => turn.text).join(" ").toLowerCase(),
    awaiting,
    human: opening !== undefined,
    answerState: `${thread.turns.length}:${awaiting ? "asked" : "answered"}`,
  };
}
