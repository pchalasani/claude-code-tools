/**
 * Whether the page owes one row the sign that the agent is working.
 *
 * A waiting human's only question is whether anything is happening, and the
 * answer has to survive everything the agent's next publish changes. It used
 * to be assembled from three places that each painted their own sign and
 * stood one another down to avoid painting two: a request in flight, a
 * message this page sent, and the document's own record of an unanswered
 * question. Standing down is how a sign disappears — one source retires as a
 * new document arrives and another has already been suppressed.
 *
 * So the question is asked once, of all three sources at once, and answered
 * with a single sign. The three cover the whole life of a question with no
 * seam in the middle:
 *
 * - the request is in the air, which is this page load only;
 * - the daemon has it and the document has not caught up, which is a fact
 *   kept in storage and read back on the very next paint, before any poll;
 * - the document says the conversation is still waiting, which survives every
 *   publish for as long as it is true.
 *
 * A slow fold must not open a seam between the second and third sources. The
 * pending note may add a diagnostic after several polls, but it remains a
 * message the daemon accepted and the document has not resolved. Hiding the
 * sign when that note became ``stalled`` was the flap: the sign disappeared
 * at the third poll and returned when the conversation eventually arrived.
 */

import type { Composer } from "./composer";
import type { Row } from "./outline";

/** The part of the page state this question is answered from. */
export interface WorkingSources {
  /** What has been sent from this page, and what is still in the air. */
  composer: Composer;
}

/**
 * Report whether one row should be showing that the agent is working.
 *
 * @param state - The page state, or as much of it as this reads.
 * @param row - The row being painted.
 * @returns True when this row is waiting on the agent for something.
 */
export function isWorking(state: WorkingSources, row: Row): boolean {
  if (state.composer.sendingAt(row.id)) {
    return true;
  }
  // Only conversations carry the document's own sign. Every row above one
  // inherits a quiet containing rail, and painting the sign there too would
  // say "the agent is working" four times for one question.
  if (row.kind === "thread" && row.awaiting) {
    return true;
  }
  return state.composer.pendingAt(row.id).length > 0;
}
