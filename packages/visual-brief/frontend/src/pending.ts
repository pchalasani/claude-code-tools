/**
 * Messages this tab has sent and not yet seen arrive on the page.
 *
 * Sending is the one moment where the page and the document disagree: the
 * human has written something, the daemon has it, and the delivered document
 * still knows nothing about it. Until the two agree the page owes the human a
 * sign that their words are somewhere.
 *
 * One rule governs a submission, and it is about the document rather than
 * about page loads: it is retired the moment its exact words and the timestamp
 * the daemon stamped them with appear in the document, under whatever id the
 * fold gave them. Nothing else retires it. Nothing deletes it either — after
 * ``STALL_POLLS`` polls without appearing it stops claiming that something is
 * happening and says the smaller true thing instead, and then it stays. A
 * message the human wrote and the page cannot account for is worth showing for
 * as long as the page is open; making it disappear would be the page quietly
 * losing something the human said.
 *
 * There used to be a second rule, counted in page loads and in the human's own
 * refreshes, and a whole apparatus for telling the page's own reloads from
 * theirs. A publish no longer reloads anything, so there is nothing to tell
 * apart and nothing to count.
 */

import {
  createComputed,
  createMemo,
  createSignal,
  type Accessor,
} from "solid-js";

import type { BriefDocument, Thread } from "./document";
import { itemRowId, laneRowId, threadRowId } from "./outline";
import {
  readSentRecords,
  saveSentRecords,
  type SentRecord,
} from "./session-store";

/** Polls a submission survives before it stops claiming progress. */
export const STALL_POLLS = 3;

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
  add: (sent: SentRecord) => void;
  /**
   * The rows carrying a note this load has to show.
   *
   * A note renders inside its row's body, so a row folded shut hides the very
   * reassurance a reload was supposed to carry over. This is what the page
   * opens before it paints.
   */
  waiting: () => string[];
  /**
   * Row the human's newest message was found in on this page load.
   *
   * A reload after a send — the human pressing refresh, or a new bundle
   * arriving — has to come back to the conversation they were writing in
   * rather than to wherever the cursor happened to be.
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

/** One remembered submission and the poll it started waiting at. */
interface Waiting {
  /** What was sent. */
  record: SentRecord;
  /** How many polls this page had seen when it started waiting. */
  since: number;
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
 * Name one turn of one conversation.
 *
 * The separator is a space, and a printable one on purpose: this name never
 * leaves the page, but the source it is written in is read by git, by diff
 * tools and by the bundler, and a control character in a string literal makes
 * the file binary to every one of them. A space is enough to keep the name
 * unique — the position is digits with no space in them, appended last, so
 * exactly one reading of any name is possible.
 *
 * @param threadId - The conversation's row id.
 * @param position - Which of its turns, counting from the oldest.
 * @returns A name no other turn on the page answers to.
 */
function turnKey(threadId: string, position: number): string {
  return `${threadId} ${position}`;
}

/**
 * Find the conversation each remembered submission has become.
 *
 * The match is the queue line itself: the words the human wrote, and the
 * timestamp the daemon stamped them with. Two identical questions asked
 * seconds apart are told apart by that timestamp, and a turn already claimed
 * by one submission cannot answer for another.
 *
 * What is claimed is the turn, not the conversation that holds it: two things
 * the human wrote get folded into one thread all the time — a question and
 * then a follow-up in the same conversation — and claiming the whole thread
 * would leave the second of them looking unsent forever.
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
    for (const thread of found) {
      const position = thread.turns.findIndex(
        (turn, index) =>
          !claimed.has(turnKey(thread.id, index))
          && turn.author === "human"
          && turn.text === record.text
          && (record.at === "" || turn.at === record.at),
      );
      if (position !== -1) {
        claimed.add(turnKey(thread.id, position));
        return thread.id;
      }
    }
    return null;
  });
}

/**
 * Build the waiting state for one open page.
 *
 * @param brief - The document being shown, read live, or one that reads null
 *     when there is no document to match against.
 * @returns The live waiting state.
 */
export function createPending(
  brief: Accessor<BriefDocument | null> = () => null,
): Pending {
  const [held, setHeld] = createSignal<Waiting[]>(
    // Everything already remembered was already waiting when this page
    // started, so all of it is counted from this page's first poll.
    readSentRecords().map((record) => ({ record, since: 0 })),
  );
  const [polls, setPolls] = createSignal(0);
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
  // What survives is written back on every change, so a reload for any other
  // reason — a new bundle, the human refreshing — finds exactly the messages
  // this page is still waiting on.
  createComputed(() => saveSentRecords(live().map((one) => one.record)));
  const landed = [...located()].reverse().find((id) => id !== null) ?? null;
  const opened = live().map((one) => one.record.rowId);

  const note = (one: Waiting): PendingNote => ({
    rowId: one.record.rowId,
    text: one.record.text,
    at: one.record.at,
    stalled: polls() - one.since >= STALL_POLLS,
  });

  return {
    at: (rowId) =>
      live().filter((one) => one.record.rowId === rowId).map(note),
    waiting: () => opened,
    add: (sent) => {
      setHeld((current) => [...current, { record: sent, since: polls() }]);
    },
    landing: () => landed,
    tick: () => setPolls((count) => count + 1),
  };
}
