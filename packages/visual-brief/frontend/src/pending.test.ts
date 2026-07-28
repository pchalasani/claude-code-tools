import { createRoot, createSignal } from "solid-js";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { BriefDocument, Turn } from "./document";
import {
  STALL_POLLS,
  createPending,
  locateSubmissions,
  type Pending,
} from "./pending";
import {
  readSentRecords,
  saveSentRecords,
  type SentRecord,
} from "./session-store";

const ITEM = "u/l/i";
const SENT_AT = "2026-07-27T09:00:00.000Z";

let disposers: (() => void)[] = [];

/**
 * Build the waiting state inside a root, so its computations are disposable.
 *
 * The page runs this inside a rendered application; a test has to give it the
 * same footing, or the computations that keep it in step with the document are
 * created with nothing to dispose them.
 *
 * @param brief - The document being shown, read live.
 * @returns The live waiting state.
 */
function pendingFor(
  brief: () => BriefDocument | null = () => null,
): Pending {
  return createRoot((dispose) => {
    disposers.push(dispose);
    return createPending(brief);
  });
}

/**
 * Build a document whose one item carries the given conversations.
 *
 * @param threads - Conversation ids and their turns.
 * @returns The document.
 */
function briefWith(threads: [string, Turn[]][]): BriefDocument {
  return {
    title: "Sample",
    summary: "One item, some conversations.",
    updates: [
      {
        id: "u",
        timestamp: "2026-07-27 09:00",
        headline: "An update",
        summary: "A summary.",
        lanes: [
          {
            id: "l",
            name: "A lane",
            items: [
              {
                id: "i",
                glance: "An item",
                explanation: "Some reasoning.",
                trust: "verified-by-me",
                questions: threads.map(([id, turns]) => ({
                  id,
                  anchor: { kind: "element", path: ITEM },
                  turns,
                })),
              },
            ],
          },
        ],
      },
    ],
  };
}

/**
 * Build one human turn.
 *
 * @param text - What was written.
 * @param at - When the queue recorded it.
 * @returns The turn.
 */
function asked(text: string, at: string = SENT_AT): Turn {
  return { author: "human", text, at };
}

/**
 * Build one remembered submission.
 *
 * @param text - What was sent.
 * @param at - The queue timestamp, if the daemon reported one.
 * @returns The record.
 */
function sent(text: string, at: string = SENT_AT): SentRecord {
  return { rowId: ITEM, anchorId: ITEM, text, at };
}

beforeEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState(null, "");
});

afterEach(() => {
  for (const dispose of disposers) {
    dispose();
  }
  disposers = [];
});

describe("finding what a sent message became", () => {
  it("matches the queue line's words and timestamp", () => {
    const brief = briefWith([["q-pending-1", [asked("Why this way?")]]]);

    expect(locateSubmissions(brief, [sent("Why this way?")])).toEqual([
      `${ITEM}#q-pending-1`,
    ]);
  });

  it("tells two identical questions apart by their timestamps", () => {
    const later = "2026-07-27T09:05:00.000Z";
    const brief = briefWith([
      ["q-first", [asked("Same words")]],
      ["q-second", [asked("Same words", later)]],
    ]);

    expect(
      locateSubmissions(brief, [sent("Same words", later)]),
    ).toEqual([`${ITEM}#q-second`]);
  });

  it("gives two identical submissions one conversation each", () => {
    const brief = briefWith([
      ["q-first", [asked("Same words", "")]],
      ["q-second", [asked("Same words", "")]],
    ]);

    expect(
      locateSubmissions(brief, [sent("Same words", ""), sent("Same words", "")]),
    ).toEqual([`${ITEM}#q-first`, `${ITEM}#q-second`]);
  });

  it("retires two submissions the fold put in one conversation", () => {
    // A question and the follow-up written into the same thread come back as
    // two turns of one conversation. Claiming the thread rather than the turn
    // left the follow-up looking unsent for the rest of the session.
    const later = "2026-07-27T09:05:00.000Z";
    const brief = briefWith([
      [
        "q-one-thread",
        [
          asked("Why this way?"),
          {
            author: "agent",
            text: "Because of the parser.",
            at: "2026-07-27T09:02:00.000Z",
          },
          asked("And the other parser?", later),
        ],
      ],
    ]);

    expect(
      locateSubmissions(brief, [
        sent("Why this way?"),
        sent("And the other parser?", later),
      ]),
    ).toEqual([`${ITEM}#q-one-thread`, `${ITEM}#q-one-thread`]);
  });

  it("finds it under whatever id the fold gave it, and however deep", () => {
    // The provisional id a queued question is shown under is not the id it
    // keeps, which is exactly why the match is on the words and the instant.
    const brief = briefWith([
      ["q-renamed-by-the-fold", [asked("Why this way?"), {
        author: "agent",
        text: "Because of the parser.",
        at: "2026-07-27T09:01:00.000Z",
      }]],
    ]);

    expect(locateSubmissions(brief, [sent("Why this way?")])).toEqual([
      `${ITEM}#q-renamed-by-the-fold`,
    ]);
  });

  it("finds nothing when the words are not on the page", () => {
    const brief = briefWith([["q-other", [asked("Something else")]]]);

    expect(locateSubmissions(brief, [sent("Why this way?")])).toEqual([null]);
  });

  it("refuses a match whose timestamp is somebody else's", () => {
    const brief = briefWith([
      ["q-other", [asked("Why this way?", "2026-07-27T08:00:00.000Z")]],
    ]);

    expect(locateSubmissions(brief, [sent("Why this way?")])).toEqual([null]);
  });
});

describe("the sign a page load carries over", () => {
  it("retires a submission the page is now showing, and lands on it", () => {
    saveSentRecords([sent("Why this way?")]);
    const brief = briefWith([["q-pending-1", [asked("Why this way?")]]]);

    const pending = pendingFor(() => brief);

    expect(pending.at(ITEM)).toEqual([]);
    expect(pending.landing()).toBe(`${ITEM}#q-pending-1`);
  });

  it("keeps the sign up for a submission that has not appeared", () => {
    saveSentRecords([sent("Why this way?")]);

    const pending = pendingFor(() => briefWith([]));

    expect(pending.at(ITEM)).toEqual([
      { rowId: ITEM, text: "Why this way?", at: SENT_AT, stalled: false },
    ]);
    expect(pending.landing()).toBeNull();
  });

  it("has nothing to say when there is no document to match against", () => {
    saveSentRecords([sent("Why this way?")]);

    const pending = pendingFor();

    expect(pending.at(ITEM)).toHaveLength(1);
    expect(pending.landing()).toBeNull();
  });

  it("writes back exactly what it is still waiting on", () => {
    saveSentRecords([sent("Why this way?"), sent("And this one?")]);

    pendingFor(() => briefWith([["q-late", [asked("Why this way?")]]]));

    expect(readSentRecords().map((record) => record.text)).toEqual([
      "And this one?",
    ]);
  });
});

describe("a submission that keeps not appearing", () => {
  it("stops promising progress once enough polls have gone by", () => {
    saveSentRecords([sent("Why this way?")]);
    const pending = pendingFor(() => briefWith([]));

    for (let poll = 0; poll < STALL_POLLS - 1; poll += 1) {
      pending.tick();
    }
    expect(pending.at(ITEM)[0]?.stalled).toBe(false);

    pending.tick();

    expect(pending.at(ITEM)[0]?.stalled).toBe(true);
  });

  it("counts a message's polls from when it was sent, not from the load", () => {
    // The page no longer reloads when the agent publishes, so a message sent
    // an hour into a session must be given the same few polls as one sent at
    // the first paint rather than being declared stalled on arrival.
    const pending = pendingFor(() => briefWith([]));
    for (let poll = 0; poll < 20; poll += 1) {
      pending.tick();
    }

    pending.add({ rowId: ITEM, anchorId: ITEM, text: "Just now", at: SENT_AT });

    expect(pending.at(ITEM)[0]?.stalled).toBe(false);
    for (let poll = 0; poll < STALL_POLLS; poll += 1) {
      pending.tick();
    }
    expect(pending.at(ITEM)[0]?.stalled).toBe(true);
  });

  it("is never let go of, however long it goes unfound", () => {
    // A message the human wrote and the page cannot account for is worth
    // showing for as long as the page is open. Deleting it would be the page
    // quietly losing something they said.
    saveSentRecords([sent("Where did this go?")]);
    const pending = pendingFor(() => briefWith([]));

    for (let poll = 0; poll < 200; poll += 1) {
      pending.tick();
    }

    expect(pending.at(ITEM)).toHaveLength(1);
    expect(readSentRecords()).toHaveLength(1);
  });
});

describe("a submission that arrives without a page load", () => {
  it("retires the moment its words appear in the document", () => {
    saveSentRecords([sent("Why this way?")]);
    const [brief, publish] = createSignal<BriefDocument>(briefWith([]));
    const pending = pendingFor(brief);
    expect(pending.at(ITEM)).toHaveLength(1);

    publish(briefWith([["q-late", [asked("Why this way?")]]]));

    expect(pending.at(ITEM)).toHaveLength(0);
    expect(readSentRecords()).toHaveLength(0);
  });

  it("keeps waiting when the publish carried something else", () => {
    saveSentRecords([sent("Why this way?")]);
    const [brief, publish] = createSignal<BriefDocument>(briefWith([]));
    const pending = pendingFor(brief);

    publish(briefWith([["q-other", [asked("Somebody else's question")]]]));

    expect(pending.at(ITEM)).toHaveLength(1);
    expect(readSentRecords()).toHaveLength(1);
  });
});
