import { evidenceRows, itemSearchText } from "./evidence";
import {
  threadIsAwaiting,
  type BriefDocument,
  type Item,
  type Lane,
  type Thread,
  type Update,
} from "./document";
export type RowKind = "update" | "lane" | "item" | "thread" | "evidence";
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
export function threadRowId(anchorId: string, thread: Thread): string {
  return `${anchorId}#${thread.id}`;
}
export function ancestorIds(id: string): string[] {
  const found: string[] = [];
  let current = id;
  while (true) {
    const hash = current.lastIndexOf("#");
    if (hash > 0) {
      current = current.slice(0, hash);
    } else {
      const slash = current.lastIndexOf("/");
      if (slash <= 0) {
        return found;
      }
      current = current.slice(0, slash);
    }
    found.push(current);
  }
}
export function outline(brief: BriefDocument): Row[] {
  const rows: Row[] = [];
  for (const update of orderedUpdates(brief)) {
    const updateRow: Row = {
      id: update.id,
      kind: "update",
      anchorId: update.id,
      parentId: null,
      label: update.headline,
      search: "",
      awaiting: false,
      human: false,
    };
    rows.push(updateRow);
    for (const lane of update.lanes ?? []) {
      const lanePath = laneRowId(update.id, lane);
      const laneRow: Row = {
        id: lanePath,
        kind: "lane",
        anchorId: lanePath,
        parentId: update.id,
        label: lane.name,
        search: "",
        awaiting: false,
        human: false,
      };
      rows.push(laneRow);
      for (const thread of orderedThreads(lane.questions)) {
        rows.push(threadRow(thread, lanePath, lanePath));
        laneRow.awaiting ||= threadIsAwaiting(thread);
      }
      for (const item of lane.items ?? []) {
        const itemPath = itemRowId(lanePath, item);
        const itemRow: Row = {
          id: itemPath,
          kind: "item",
          anchorId: itemPath,
          parentId: lanePath,
          label: item.glance,
          search: itemSearchText(item),
          awaiting: false,
          human: false,
        };
        rows.push(itemRow);
        rows.push(...evidenceRows(item, itemPath));
        for (const thread of orderedThreads(item.questions)) {
          rows.push(threadRow(thread, itemPath, itemPath));
          itemRow.awaiting ||= threadIsAwaiting(thread);
        }
        laneRow.awaiting ||= itemRow.awaiting;
      }
      updateRow.awaiting ||= laneRow.awaiting;
    }
  }
  return rows;
}
export function awaitingThreadCount(rows: Row[]): number {
  return rows.filter((row) => row.kind === "thread" && row.awaiting).length;
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
