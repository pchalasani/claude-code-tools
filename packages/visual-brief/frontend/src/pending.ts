import { createComputed, createMemo, createSignal, type Accessor } from "solid-js";
import type { BriefDocument, Thread } from "./document";
import { ancestorIds, itemRowId, laneRowId, threadRowId } from "./outline";
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
  for (const update of brief.updates ?? []) {
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
    let earlier = record.at === "" ? record.after ?? 0 : 0;
    for (const thread of found) {
      if (!thread.id.startsWith(`${record.anchorId}#`)) continue;
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
        && ancestorIds(one.record.rowId).includes(rowId)),
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
