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
  consumeSelfReload,
  readSentRecords,
  saveSentRecords,
  type SentRecord,
} from "./session-store";

/** Polls a submission survives after a page load before it stops spinning. */
export const STALL_POLLS = 3;

/** Manual refreshes an unfound submission survives before it is let go. */
export const MAX_REFRESHES = 3;

/**
 * Report whether this page load was caused by the human refreshing.
 *
 * A publish also reloads the page, and a publish the human did not cause
 * must never age their waiting sign. The browser records which kind of
 * navigation this load was; anything unreadable counts as not-a-refresh,
 * which errs toward keeping the sign.
 *
 * @returns True when the human reloaded this page themselves.
 */
/**
 * What this page load was, classified exactly once.
 *
 * The marker is consumed on first classification; a second look within the
 * same load would find the marker gone and the navigation entry still
 * saying "reload", and would misread the page's own reload as the human's.
 */
let loadWasHumanRefresh: boolean | null = null;

export function isHumanRefresh(): boolean {
  // The page's own publish-reload uses location.reload() and the browser
  // reports it identically to a manual refresh, so the page announces its
  // own reloads just before making them; an announced reload is not the
  // human's. Classification happens once per load and is remembered.
  if (loadWasHumanRefresh !== null) {
    return loadWasHumanRefresh;
  }
  const selfCaused = consumeSelfReload();
  try {
    const [entry] = performance.getEntriesByType("navigation");
    loadWasHumanRefresh = !selfCaused
      && (entry as PerformanceNavigationTiming | undefined)?.type
        === "reload";
  } catch {
    loadWasHumanRefresh = false;
  }
  return loadWasHumanRefresh;
}

/**
 * Forget the load classification, so a test can simulate a fresh load.
 */
export function forgetLoadClassification(): void {
  loadWasHumanRefresh = null;
}


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
  // Publishes the human did not cause never age a record: only their own
  // refreshes do, so the degraded advice — "refresh if this persists" — is
  // the genuine way out, and a sign can neither be deleted by the agent's
  // publishing nor hold its row open for the life of the tab.
  const refreshed = isHumanRefresh();
  const aged = stored.map((record) => ({
    ...record,
    refreshes: (record.refreshes ?? 0) + (refreshed ? 1 : 0),
  }));
  // The count is aged BEFORE the survivor check, so the third manual
  // refresh is the one that lets go — as the degraded advice promises.
  const survivors = aged.filter(
    (record, index) =>
      located[index] === null && record.refreshes < MAX_REFRESHES,
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
