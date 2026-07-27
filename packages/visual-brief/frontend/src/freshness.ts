/**
 * What changed since the human last looked.
 *
 * The page reloads itself the moment the agent publishes, and a reload rebuilds
 * every row from scratch. Rows hold themselves open while they are *awaiting*
 * an answer, which means the one row the human is waiting on is the one row
 * that folds shut the instant its answer arrives. That is the moment this
 * module exists for: an answer that landed since the last look is new, it opens
 * itself, and it stays marked until the human goes to it.
 *
 * Everything here is a pure function over the rows and one remembered record,
 * so freshness can be tested without a browser, a clock or a timer.
 */

import { createSignal } from "solid-js";

import {
  readSeenAnswers,
  saveSeenAnswers,
  type SeenAnswers,
} from "./session-store";
import type { Row } from "./outline";

/** Mark a conversation carries once an agent has answered it. */
export const ANSWERED = "answered";

/**
 * Describe one conversation as a value two loads of the page can compare.
 *
 * @param turns - How many turns the conversation holds.
 * @param awaiting - Whether its newest turn is still the human's.
 * @returns The conversation's state.
 */
export function conversationState(turns: number, awaiting: boolean): string {
  return `${turns}:${awaiting ? "asked" : ANSWERED}`;
}

/**
 * Describe every conversation on the page as it stands right now.
 *
 * @param rows - Every row of the document.
 * @returns Each conversation row's state, keyed by row id.
 */
export function answerStates(rows: Row[]): SeenAnswers {
  const states: SeenAnswers = {};
  for (const row of rows) {
    if (row.kind === "thread" && row.answerState !== undefined) {
      states[row.id] = row.answerState;
    }
  }
  return states;
}

/**
 * Name the conversations whose answer arrived since the last look.
 *
 * A conversation counts as new when it is answered and either the record has
 * never seen it — a pending question folded into a saved conversation changes
 * identity, so this is the ordinary case — or has seen it in a different
 * state. A page with no record at all is a first look: nothing on it is new,
 * because the human has not yet been shown anything to have missed.
 *
 * @param states - Every conversation's current state.
 * @param seen - What was remembered from the last look, or null for none.
 * @returns Row ids of the conversations that are new.
 */
export function freshAnswers(
  states: SeenAnswers,
  seen: SeenAnswers | null,
): Set<string> {
  const fresh = new Set<string>();
  if (seen === null) {
    return fresh;
  }
  for (const [id, state] of Object.entries(states)) {
    if (state.endsWith(`:${ANSWERED}`) && seen[id] !== state) {
      fresh.add(id);
    }
  }
  return fresh;
}

/**
 * Build the record to carry into the next reload.
 *
 * Everything the human can be said to have seen is recorded; what is still
 * marked new is deliberately left out, so it survives further reloads and
 * stays marked until it is visited rather than until it is republished.
 *
 * @param states - Every conversation's current state.
 * @param fresh - Row ids still marked new.
 * @returns The record to remember.
 */
export function rememberSeen(
  states: SeenAnswers,
  fresh: ReadonlySet<string>,
): SeenAnswers {
  const remembered: SeenAnswers = {};
  for (const [id, state] of Object.entries(states)) {
    if (!fresh.has(id)) {
      remembered[id] = state;
    }
  }
  return remembered;
}

/** What one page load knows about answers it has not been shown before. */
export interface Freshness {
  /** Whether one conversation's answer arrived since the last look. */
  isFresh: (id: string) => boolean;
  /** Note that the human has now been shown one conversation. */
  visit: (id: string) => void;
  /** The conversations still marked new. */
  ids: () => ReadonlySet<string>;
}

/**
 * Build the freshness state for one page load.
 *
 * What the human has seen is written now rather than on the way out: this page
 * is closed by being replaced, so there is no later moment to write in. What
 * is still marked new is deliberately left out of that record, which is what
 * keeps it marked across further reloads until it is visited.
 *
 * @param rows - Every row of the document.
 * @returns The live freshness state.
 */
export function createFreshness(rows: Row[]): Freshness {
  const states = answerStates(rows);
  const [fresh, setFresh] = createSignal<ReadonlySet<string>>(
    freshAnswers(states, readSeenAnswers()),
  );
  let seen: SeenAnswers = rememberSeen(states, fresh());
  saveSeenAnswers(seen);
  return {
    isFresh: (id) => fresh().has(id),
    ids: fresh,
    visit: (id) => {
      if (!fresh().has(id)) {
        return;
      }
      setFresh((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
      seen = { ...seen, [id]: states[id] ?? "" };
      saveSeenAnswers(seen);
    },
  };
}
