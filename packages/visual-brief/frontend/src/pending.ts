/**
 * Messages this tab has sent and not yet seen arrive on the page.
 *
 * Sending is the one moment where the page and the document disagree: the
 * human has written something, the daemon has it, and the delivered document
 * still knows nothing about it. Until the two agree the page owes the human a
 * sign that their words are somewhere, and that sign has to survive the reload
 * the daemon triggers seconds later.
 *
 * So a submission is remembered by what the queue line says — its verbatim
 * text and the timestamp the daemon wrote — and is retired the moment those
 * exact words appear anywhere on the page, under whatever id the fold gave
 * them. A submission that never appears stops pretending: after a page load
 * and a few polls it degrades to a plain statement that it was submitted,
 * rather than an animation that never ends.
 */

import { createSignal } from "solid-js";

import type { BriefDocument, Thread } from "./document";
import { itemRowId, laneRowId, threadRowId } from "./outline";
import {
  readSentRecords,
  saveSentRecords,
  type SentRecord,
} from "./session-store";

/** Polls a submission survives after a page load before it stops spinning. */
export const STALL_POLLS = 3;

/** Page loads a submission survives unfound before it is forgotten. */
export const MAX_LOADS = 3;

/** What the page says about one message it is still waiting on. */
export interface PendingNote {
  /** Row the note belongs under. */
  rowId: string;
  /** What was sent. */
  text: string;
  /** Timestamp the daemon recorded, or empty when it did not say. */
  at: string;
  /** Whether the page has waited long enough to stop claiming progress. */
  stalled: boolean;
}

/** What this page is still waiting to see land. */
export interface Pending {
  /** The notes belonging under one row. */
  at: (rowId: string) => PendingNote[];
  /** Remember one message that was just accepted by the daemon. */
  add: (sent: Omit<SentRecord, "loads">) => void;
  /**
   * The rows carrying a note this load has to show.
   *
   * A note renders inside its row's body, so a row folded shut hides the very
   * reassurance the reload was supposed to carry over. This is what the page
   * opens before it paints.
   */
  waiting: () => string[];
  /**
   * Row the human's newest message was found in on this page load.
   *
   * This is where the viewport belongs after the reload a send causes: the
   * conversation they just wrote in, not wherever the cursor happened to be.
   */
  landing: () => string | null;
  /** Note that one poll cycle finished without bringing new content. */
  tick: () => void;
}

/** One conversation on the page, with the id the cursor knows it by. */
interface Located {
  /** The conversation's row id. */
  id: string;
  /** Its turns, oldest first. */
  turns: Thread["turns"];
}

/**
 * List every conversation in a document with its row id.
 *
 * @param brief - The delivered document.
 * @returns Each conversation, in document order.
 */
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
          found.push({
            id: threadRowId(itemPath, thread),
            turns: thread.turns,
          });
        }
      }
    }
  }
  return found;
}

/**
 * Find the conversation each remembered submission has become.
 *
 * The match is the queue line itself: the words the human wrote, and the
 * timestamp the daemon stamped them with. Two identical questions asked
 * seconds apart are told apart by that timestamp, and a conversation already
 * claimed by one submission cannot answer for another.
 *
 * @param brief - The delivered document.
 * @param records - The remembered submissions, oldest first.
 * @returns The row id each submission landed in, or null where none did.
 */
export function locateSubmissions(
  brief: BriefDocument,
  records: SentRecord[],
): (string | null)[] {
  const found = conversations(brief);
  const claimed = new Set<string>();
  return records.map((record) => {
    const match = found.find(
      (thread) =>
        !claimed.has(thread.id)
        && thread.turns.some(
          (turn) =>
            turn.author === "human"
            && turn.text === record.text
            && (record.at === "" || turn.at === record.at),
        ),
    );
    if (match === undefined) {
      return null;
    }
    claimed.add(match.id);
    return match.id;
  });
}

/**
 * Build the waiting state for one page load.
 *
 * @param brief - The delivered document, when there is one to match against.
 * @returns The live waiting state.
 */
export function createPending(brief: BriefDocument | null = null): Pending {
  const stored = readSentRecords();
  const located = brief === null
    ? stored.map(() => null)
    : locateSubmissions(brief, stored);
  const survivors = stored.filter(
    (record, index) => located[index] === null && record.loads < MAX_LOADS,
  );
  const carried = survivors.map((record) => ({
    ...record,
    loads: record.loads + 1,
  }));
  saveSentRecords(carried);
  const [live, setLive] = createSignal<SentRecord[]>(carried);
  const [polls, setPolls] = createSignal(0);
  const landed = [...located].reverse().find((id) => id !== null) ?? null;

  const note = (record: SentRecord): PendingNote => ({
    rowId: record.rowId,
    text: record.text,
    at: record.at,
    // A message sent in this page load has not yet had its reload, so it is
    // still early; one that came through a reload without being found has had
    // its chance, and the polls since then are what run out.
    stalled: record.loads > 0 && polls() >= STALL_POLLS,
  });

  return {
    at: (rowId) => live().filter((r) => r.rowId === rowId).map(note),
    waiting: () => carried.map((record) => record.rowId),
    add: (sent) => {
      const next = [...live(), { ...sent, loads: 0 }];
      saveSentRecords(next);
      setLive(next);
    },
    landing: () => landed,
    tick: () => setPolls((count) => count + 1),
  };
}
