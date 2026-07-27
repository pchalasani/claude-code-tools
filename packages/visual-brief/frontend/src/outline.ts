/**
 * The document flattened into the rows the cursor can occupy.
 *
 * Every navigable thing on the page — an update, a lane, an item, a question
 * thread — is one row with a stable id. The id is the anchor path the rest of
 * the system already speaks: ``update``, ``update/lane``, ``update/lane/item``
 * and ``update/lane/item#thread``. Movement, restoration after a reload and
 * the structure map all read this one list, so what the human sees and what
 * the cursor believes cannot drift apart.
 */

import { conversationState } from "./freshness";
import {
  threadIsAwaiting,
  type BriefDocument,
  type Forensic,
  type Item,
  type Lane,
  type Thread,
  type Update,
} from "./document";

/**
 * Reserved update id marking the one "Now" panel.
 *
 * The update carrying this id is not history: it states where things stand
 * at this moment, is rewritten in place on every publish, and is pinned above
 * the dated updates. Structurally it is an ordinary update, so anchors,
 * question threads and awaiting counts work on it unchanged.
 */
export const NOW_UPDATE_ID = "now";

/** What kind of thing a row stands for. */
export type RowKind = "update" | "lane" | "item" | "thread";

/** One navigable row of the document. */
export interface Row {
  /** Stable anchor-shaped identity, unique in the page. */
  id: string;
  /** What the row stands for. */
  kind: RowKind;
  /** Anchor path a message composed here would be attached to. */
  anchorId: string;
  /** Thread a message composed here would continue. */
  parentThreadId?: string;
  /** Id of the row that contains this one. */
  parentId: string | null;
  /** Short text shown for the row in the structure map. */
  label: string;
  /**
   * Lowercased text the search filter matches against.
   *
   * Only items carry any: search is item search, as the overlay's own label
   * says, and an item's text already includes the conversations hanging from
   * it. Everything else is empty rather than dead weight the filter ignores.
   */
  search: string;
  /** Whether this row holds a question still waiting for an answer. */
  awaiting: boolean;
  /**
   * How a conversation stands, as a value two loads of the page can compare.
   *
   * Only conversation rows carry one. It changes whenever a turn is added, so
   * a reload can tell an answer that arrived while the human was away from one
   * they have already read.
   */
  answerState?: string;
}

/**
 * Order updates for the page: the Now panel first, then newest first.
 *
 * @param brief - A delivered document.
 * @returns The Now update, if present, followed by history newest first.
 */
export function orderedUpdates(brief: BriefDocument): Update[] {
  const reversed = [...brief.updates].reverse();
  const now = reversed.find((update) => update.id === NOW_UPDATE_ID);
  if (now === undefined) {
    return reversed;
  }
  return [now, ...reversed.filter((update) => update !== now)];
}

/**
 * Return the row id of one lane.
 *
 * @param updateId - Id of the update holding the lane.
 * @param lane - The lane.
 * @returns The lane's row id.
 */
export function laneRowId(updateId: string, lane: Lane): string {
  return `${updateId}/${lane.id}`;
}

/**
 * Return the row id of one item.
 *
 * @param lanePath - Row id of the lane holding the item.
 * @param item - The item.
 * @returns The item's row id.
 */
export function itemRowId(lanePath: string, item: Item): string {
  return `${lanePath}/${item.id}`;
}

/**
 * Return the row id of one question thread.
 *
 * @param anchorId - Row id of the lane or item the thread hangs from.
 * @param thread - The thread.
 * @returns The thread's row id.
 */
export function threadRowId(anchorId: string, thread: Thread): string {
  return `${anchorId}#${thread.id}`;
}

/**
 * List a row id's containing rows, nearest first.
 *
 * @param id - A row id.
 * @returns Ids of the rows that contain it, nearest ancestor first.
 */
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

/**
 * Flatten a document into rows in the order the page paints them.
 *
 * @param brief - A delivered document.
 * @returns Every navigable row, in document order.
 */
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
      };
      rows.push(laneRow);
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
        };
        rows.push(itemRow);
        for (const thread of item.questions ?? []) {
          rows.push(threadRow(thread, itemPath, itemPath));
          itemRow.awaiting ||= threadIsAwaiting(thread);
        }
        laneRow.awaiting ||= itemRow.awaiting;
      }
      for (const thread of lane.questions ?? []) {
        rows.push(threadRow(thread, lanePath, lanePath));
        laneRow.awaiting ||= threadIsAwaiting(thread);
      }
      updateRow.awaiting ||= laneRow.awaiting;
    }
  }
  return rows;
}

/**
 * Choose which rows start out expanded.
 *
 * The first update on the page opens — the Now panel when one exists,
 * otherwise the newest update — and the Now panel's lanes open with it,
 * because current state must be readable without a click. Lanes that asked
 * to be open or that hold an unanswered question open too, and anything
 * awaiting an answer opens itself all the way down. Everything else starts
 * folded, because this page is meant to be scanned before it is read.
 *
 * @param brief - A delivered document.
 * @param rows - The document's rows.
 * @returns Ids of the rows that begin expanded.
 */
export function defaultOpenIds(
  brief: BriefDocument,
  rows: Row[],
): Set<string> {
  const open = new Set<string>();
  const first = orderedUpdates(brief)[0];
  if (first !== undefined) {
    open.add(first.id);
    if (first.id === NOW_UPDATE_ID) {
      for (const lane of first.lanes ?? []) {
        open.add(laneRowId(first.id, lane));
      }
    }
  }
  for (const update of brief.updates ?? []) {
    for (const lane of update.lanes ?? []) {
      if (lane.open === true) {
        open.add(laneRowId(update.id, lane));
      }
    }
  }
  for (const row of rows) {
    if (!row.awaiting) {
      continue;
    }
    open.add(row.id);
    for (const ancestor of ancestorIds(row.id)) {
      open.add(ancestor);
    }
  }
  return open;
}

/**
 * Count the threads still waiting for an answer.
 *
 * @param rows - The document's rows.
 * @returns How many question threads await an agent answer.
 */
export function awaitingThreadCount(rows: Row[]): number {
  return rows.filter((row) => row.kind === "thread" && row.awaiting).length;
}

/**
 * Build one thread row.
 *
 * @param thread - The delivered thread.
 * @param anchorId - Row id of the lane or item it hangs from.
 * @param parentId - Row id of its container.
 * @returns The thread's row.
 */
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
    search: "",
    awaiting,
    answerState: conversationState(thread.turns.length, awaiting),
  };
}

/**
 * Collect every word of an item that search should reach.
 *
 * @param item - A delivered item.
 * @returns The item's searchable text, lowercased.
 */
function itemSearchText(item: Item): string {
  const parts: string[] = [item.glance, item.explanation];
  for (const entry of item.forensics ?? []) {
    parts.push(forensicText(entry));
  }
  for (const table of item.tables ?? []) {
    parts.push(table.caption, ...table.columns, ...table.rows.flat());
  }
  for (const thread of item.questions ?? []) {
    for (const turn of thread.turns) {
      parts.push(turn.text);
    }
  }
  return parts.join(" ").toLowerCase();
}

/**
 * Flatten one forensic entry to text.
 *
 * @param entry - Raw evidence or a nested note.
 * @returns The entry's text.
 */
function forensicText(entry: Forensic): string {
  if (typeof entry === "string") {
    return entry;
  }
  const children = (entry.children ?? []).map(forensicText);
  return [entry.title, entry.body, ...children].join(" ");
}
