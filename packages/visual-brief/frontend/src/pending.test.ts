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

  it("matches only conversations at the submission anchor", () => {
    const brief = briefWith([["q-right", [asked("Same words")]]]);
    const lane = brief.updates[0]?.lanes[0];
    lane?.items.push({
      id: "other",
      glance: "Another item",
      explanation: "The same question can be asked here.",
      trust: "verified-by-me",
      questions: [{
        id: "q-wrong",
        anchor: { kind: "element", path: "u/l/other" },
        turns: [asked("Same words")],
      }],
    });
    lane?.items.reverse();
    expect(locateSubmissions(brief, [sent("Same words")])).toEqual([
      `${ITEM}#q-right`,
    ]);
  });

  it("refuses a match whose timestamp is somebody else's", () => {
    const brief = briefWith([
      ["q-other", [asked("Why this way?", "2026-07-27T08:00:00.000Z")]],
    ]);

    expect(locateSubmissions(brief, [sent("Why this way?")])).toEqual([null]);
  });
});

describe("the sign a page load carries over", () => {
  it("updates the same pending turn when the queue stamps it", () => {
    const pending = pendingFor(() => briefWith([]));
    const token = pending.begin(sent("Why this way?", ""));
    const note = pending.at(ITEM)[0];

    pending.stamp(token, SENT_AT);

    expect(pending.at(ITEM)[0]).toBe(note);
    expect(note?.at).toBe(SENT_AT);
  });

  it("keeps display time separate when the daemon reports no timestamp", () => {
    const shownAt = "2026-07-27T08:59:59.000Z";
    const pending = pendingFor(() => briefWith([]));
    const token = pending.begin(
      { ...sent("Why this way?", ""), displayAt: shownAt },
    );
    pending.stamp(token, "");
    expect(pending.at(ITEM)[0]?.at).toBe(shownAt);
    expect(readSentRecords()[0]).toMatchObject({ at: "", displayAt: shownAt });
    expect(
      locateSubmissions(
        briefWith([["q-published", [asked("Why this way?", SENT_AT)]]]),
        readSentRecords(),
      ),
    ).toEqual([`${ITEM}#q-published`]);
  });

  it("retires a submission the page is now showing", () => {
    saveSentRecords([sent("Why this way?")]);
    const brief = briefWith([["q-pending-1", [asked("Why this way?")]]]);

    const pending = pendingFor(() => brief);

    expect(pending.at(ITEM)).toEqual([]);
  });

  it("keeps the sign up for a submission that has not appeared", () => {
    saveSentRecords([sent("Why this way?")]);

    const pending = pendingFor(() => briefWith([]));

    expect(pending.at(ITEM)).toEqual([
      { rowId: ITEM, text: "Why this way?", at: SENT_AT, stalled: false },
    ]);
  });

  it("has nothing to say when there is no document to match against", () => {
    saveSentRecords([sent("Why this way?")]);

    const pending = pendingFor();

    expect(pending.at(ITEM)).toHaveLength(1);
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
  it("does not mistake an older identical turn for the new submission", () => {
    const old = ["q-old", [asked("Same words")]] as [string, Turn[]];
    const [brief, publish] = createSignal<BriefDocument>(briefWith([old]));
    const pending = pendingFor(brief);
    const token = pending.begin(
      { ...sent("Same words", ""), displayAt: SENT_AT },
    );
    pending.stamp(token, "");
    expect(pending.at(ITEM)).toHaveLength(1);
    publish(briefWith([
      old,
      ["q-new", [asked("Same words", "2026-07-27T09:05:00.000Z")]],
    ]));
    expect(pending.at(ITEM)).toHaveLength(0);
  });

  it("keeps the later of two identical unstamped submissions after reload", () => {
    const [brief, publish] = createSignal<BriefDocument>(briefWith([]));
    const pending = pendingFor(brief);
    const record = { ...sent("Same words", ""), displayAt: SENT_AT };
    const first = pending.begin(record);
    pending.stamp(first, "");
    const second = pending.begin(record);
    pending.stamp(second, "");
    publish(briefWith([["q-first", [asked("Same words")]]]));
    expect(pending.at(ITEM)).toHaveLength(1);
    expect(readSentRecords()[0]?.after).toBe(1);
    const restored = pendingFor(brief);
    expect(restored.at(ITEM)).toHaveLength(1);
  });

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
