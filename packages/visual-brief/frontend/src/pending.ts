import { createComputed, createMemo, createSignal, type Accessor } from "solid-js";
import {
  isStructuredCurrentState,
  type BriefDocument,
  type Thread,
} from "./document";
import {
  CURRENT_STATE_ROOT_ID,
  ancestorRowIds,
  currentStateItemRowId,
  currentStateLaneRowId,
  itemRowId,
  laneRowId,
  outline,
  threadRowId,
} from "./outline";
import { readSentRecords, saveSentRecords, type SentRecord } from "./session-store";
export const STALL_POLLS = 3;
export interface PendingNote {
  rowId: string;
  text: string;
  at: string;
  stalled: boolean;
}
export interface Pending {
  at: (rowId: string) => PendingNote[];
  within: (rowId: string) => boolean;
  add: (sent: SentRecord) => void;
  begin: (sent: SentRecord) => string;
  stamp: (token: string, at: string) => void;
  fail: (token: string) => void;
  failureAt: (rowId: string) => string | null;
  clearFailure: (rowId: string) => void;
  tick: () => void;
}
interface Located {
  id: string;
  turns: Thread["turns"];
}
interface Waiting {
  token: string;
  record: SentRecord;
  since: number;
}
export function conversations(brief: BriefDocument): Located[] {
  const found: Located[] = [];
  const state = brief.current_state;
  if (state !== undefined && isStructuredCurrentState(state)) {
    for (const thread of state.questions ?? []) {
      found.push({
        id: threadRowId(CURRENT_STATE_ROOT_ID, thread),
        turns: thread.turns,
      });
    }
    for (const lane of state.lanes ?? []) {
      const lanePath = currentStateLaneRowId(lane);
      for (const thread of lane.questions ?? []) {
        found.push({ id: threadRowId(lanePath, thread), turns: thread.turns });
      }
      for (const item of lane.items ?? []) {
        const itemPath = currentStateItemRowId(item);
        for (const thread of item.questions ?? []) {
          found.push({
            id: threadRowId(itemPath, thread),
            turns: thread.turns,
          });
        }
      }
    }
  }
  for (const update of brief.updates ?? []) {
    for (const thread of update.questions ?? []) {
      found.push({
        id: threadRowId(update.id, thread),
        turns: thread.turns,
      });
    }
    for (const lane of update.lanes ?? []) {
      const lanePath = laneRowId(update.id, lane);
      for (const thread of lane.questions ?? []) {
        found.push({ id: threadRowId(lanePath, thread), turns: thread.turns });
      }
      for (const item of lane.items ?? []) {
        const itemPath = itemRowId(lanePath, item);
        for (const thread of item.questions ?? []) {
          found.push({ id: threadRowId(itemPath, thread), turns: thread.turns });
        }
      }
    }
  }
  return found;
}

/** Identify the agent turns currently attached directly below one row. */
export function agentAnswerVersion(
  brief: BriefDocument,
  rowId: string,
): string {
  const turns = conversations(brief)
    .filter((thread) => thread.id.startsWith(`${rowId}#`))
    .flatMap((thread) => thread.turns.flatMap((turn, position) =>
      turn.author === "agent"
        ? [`${thread.id}:${position}:${turn.at}`]
        : []
    ));
  return JSON.stringify(turns);
}

/** Report whether the exact suggested-reply turn has an agent answer. */
export function suggestedReplyAnswered(
  brief: BriefDocument,
  rowId: string,
  text: string,
  at: string,
): boolean {
  for (const thread of conversations(brief)) {
    if (!thread.id.startsWith(`${rowId}#`)) continue;
    let found = false;
    for (const turn of thread.turns) {
      if (!found) {
        found = turn.author === "human" && turn.text === text && turn.at === at;
      } else if (turn.author === "agent") {
        return true;
      }
    }
  }
  return false;
}
function turnKey(threadId: string, position: number): string {
  return `${threadId} ${position}`;
}
export function locateSubmissions(
  brief: BriefDocument,
  records: SentRecord[],
): (string | null)[] {
  const found = conversations(brief);
  const claimed = new Set<string>();
  return records.map((record) => {
    if (record.failed === true) return null;
    const anchorId = brief.legacy_anchor_aliases?.[record.anchorId]
      ?? record.anchorId;
    let earlier = record.at === "" ? record.after ?? 0 : 0;
    for (const thread of found) {
      if (!thread.id.startsWith(`${anchorId}#`)) continue;
      const position = thread.turns.findIndex((turn, index) => {
        if (turn.author !== "human" || turn.text !== record.text) return false;
        if (record.at !== "" && turn.at !== record.at) return false;
        if (earlier-- > 0) return false;
        return !claimed.has(turnKey(thread.id, index));
      });
      if (position !== -1) {
        claimed.add(turnKey(thread.id, position));
        return thread.id;
      }
    }
    return null;
  });
}
export function createPending(
  brief: Accessor<BriefDocument | null> = () => null,
): Pending {
  const [held, setHeld] = createSignal<Waiting[]>(
    readSentRecords().map((record, index) => ({
      token: `restored-${index}`,
      record,
      since: 0,
    })),
  );
  const [polls, setPolls] = createSignal(0);
  let sequence = 0;
  const located = createMemo(() => {
    const document = brief();
    const waiting = held();
    return document === null
      ? waiting.map(() => null)
      : locateSubmissions(document, waiting.map((one) => one.record));
  });
  const live = createMemo(() =>
    held().filter((_, index) => located()[index] === null),
  );
  const rows = createMemo(() => {
    const document = brief();
    return document === null ? [] : outline(document);
  });
  createComputed(() => saveSentRecords(live().map((one) => one.record)));
  const views = new WeakMap<Waiting, PendingNote>();
  const note = (one: Waiting): PendingNote => {
    const shown = views.get(one);
    if (shown !== undefined) {
      return shown;
    }
    const view: PendingNote = {
      rowId: one.record.rowId,
      text: one.record.text,
      get at(): string { held(); return one.record.displayAt ?? one.record.at; },
      get stalled(): boolean {
        return polls() - one.since >= STALL_POLLS;
      },
    };
    views.set(one, view);
    return view;
  };
  const begin = (sent: SentRecord): string => {
    const token = `sent-${sequence += 1}`;
    const document = brief();
    const turns = document === null ? [] : conversations(document)
      .filter((thread) => thread.id.startsWith(`${sent.anchorId}#`))
      .flatMap((thread) => thread.turns);
    const after = turns.filter(
      (turn) => turn.author === "human" && turn.text === sent.text,
    ).length + live().filter(
      (one) => one.record.anchorId === sent.anchorId
        && one.record.text === sent.text,
    ).length;
    setHeld((current) => [...current, {
      token, record: { ...sent, after }, since: polls(),
    }]);
    return token;
  };
  return {
    at: (rowId) =>
      live().filter(
        (one) => one.record.rowId === rowId && one.record.failed !== true,
      ).map(note),
    within: (rowId) =>
      live().some((one) => one.record.failed !== true
        && ancestorRowIds(rows(), one.record.rowId).includes(rowId)),
    add: (sent) => { begin(sent); },
    begin,
    stamp: (token, at) => {
      const waiting = held().find((one) => one.token === token);
      if (waiting === undefined) {
        return;
      }
      waiting.record.at = at;
      if (at !== "") waiting.record.displayAt = at;
      setHeld((current) => [...current]);
    },
    fail: (token) => {
      const waiting = held().find((one) => one.token === token);
      if (waiting !== undefined) {
        waiting.record.failed = true;
        setHeld((current) => [...current]);
      }
    },
    failureAt: (rowId) =>
      live().find(
        (one) => one.record.rowId === rowId && one.record.failed === true,
      )?.record.text ?? null,
    clearFailure: (rowId) =>
      setHeld((current) => current.filter(
        (one) => one.record.rowId !== rowId || one.record.failed !== true,
      )),
    tick: () => setPolls((count) => count + 1),
  };
}
