/**
 * What a publish is allowed to do to a page somebody is reading.
 *
 * Every assertion is made by publishing into a mounted application and then
 * reading the page, because that is the whole of what a publish now is: the
 * poller fetches a document and hands it over, and nothing else is supposed to
 * notice. What must survive is not a list of internals but a list of places a
 * human has been thrown out of — their cursor, their folds, what they were in
 * the middle of writing.
 */

import { describe, expect, it } from "vitest";

import type { BriefDocument, Item, Lane, Update } from "./document";
import { saveSentRecords } from "./session-store";
import {
  click,
  mountLive,
  paintedCursor,
  paintedOpen,
  press,
  useHarness,
} from "../test/harness";
import { sampleBrief } from "../test/sample-brief";

const ALPHA = "newest/changed/alpha";
const BETA = "newest/changed/beta";
const OPEN_THREAD = `${BETA}#q-open`;
const NEXT_LANE = "newest/next";
const GAMMA = "newest/next/gamma";

useHarness();

/**
 * Find one lane of the sample document.
 *
 * @param brief - The document to look in.
 * @param updateId - Id of the update holding it.
 * @param laneId - Id of the lane.
 * @returns The lane.
 */
function laneOf(brief: BriefDocument, updateId: string, laneId: string): Lane {
  const update = brief.updates.find((one: Update) => one.id === updateId);
  const lane = update?.lanes.find((one: Lane) => one.id === laneId);
  if (lane === undefined) {
    throw new Error(`the sample document lost the lane ${laneId}`);
  }
  return lane;
}

/**
 * Find one item of the sample document.
 *
 * @param brief - The document to look in.
 * @param path - The item's row id.
 * @returns The item.
 */
function itemOf(brief: BriefDocument, path: string): Item {
  const [updateId = "", laneId = "", itemId = ""] = path.split("/");
  const item = laneOf(brief, updateId, laneId).items.find(
    (one: Item) => one.id === itemId,
  );
  if (item === undefined) {
    throw new Error(`the sample document lost the item ${path}`);
  }
  return item;
}

/**
 * Read one row's article element.
 *
 * @param id - Row id to look for.
 * @returns The element, or null when the page is not painting it.
 */
function rowNode(id: string): Element | null {
  return document.querySelector(`[data-row-id="${id}"]`);
}

/**
 * Read the jump labels the page is painting.
 *
 * @returns Each label, keyed by the row wearing it.
 */
function paintedHints(): Record<string, string> {
  const hints: Record<string, string> = {};
  for (const row of document.querySelectorAll("[data-row-id]")) {
    const hint = row.querySelector(":scope > .row-head > .hint");
    if (hint !== null) {
      hints[row.getAttribute("data-row-id") ?? ""] =
        hint.getAttribute("data-hint") ?? "";
    }
  }
  return hints;
}

/**
 * Open the chat box at one row through the affordance a hand would use.
 *
 * @param id - Row to write against.
 */
function composeAt(id: string): void {
  document
    .querySelector(`[data-row-id="${id}"] > .row-head .chat-button`)
    ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

/**
 * Write into whatever text box is open, the way a keyboard does.
 *
 * @param selector - The box to write into.
 * @param text - What to write.
 */
function type(selector: string, text: string): void {
  const box = document.querySelector<HTMLTextAreaElement | HTMLInputElement>(
    selector,
  );
  if (box === null) {
    throw new Error(`nothing to write into at ${selector}`);
  }
  box.value = text;
  box.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("what a publish leaves exactly where it was", () => {
  it("keeps the DOM of a row it did not change", () => {
    // The anti-flicker proof. Re-parsing the document produces all-new
    // objects, and a page that swapped them wholesale would discard and
    // rebuild every row on the screen — the reload's flicker without the
    // reload. What is asserted is the node itself, because that is what the
    // human's scroll position, text selection and focus all hang off.
    const { publish } = mountLive();
    const heldThread = rowNode(OPEN_THREAD);
    const heldItem = rowNode(BETA);
    const heldAlpha = rowNode(ALPHA);
    expect(heldThread).not.toBeNull();

    const next = sampleBrief();
    itemOf(next, ALPHA).glance = "Alpha moved further forward";
    publish(next);

    expect(rowNode(OPEN_THREAD)).toBe(heldThread);
    expect(rowNode(BETA)).toBe(heldItem);
    // Even the row that changed keeps its node: what changed is its words.
    expect(rowNode(ALPHA)).toBe(heldAlpha);
    expect(
      document.querySelector(`[data-row-id="${ALPHA}"] .glance`)?.textContent,
    ).toBe("Alpha moved further forward");
  });

  it("keeps the cursor on the row the human left it on", () => {
    const { publish } = mountLive();
    press("j");
    const before = paintedCursor();

    const next = sampleBrief();
    next.title = "Published while reading";
    publish(next);

    expect(paintedCursor()).toBe(before);
  });

  it("moves a vanished cursor no further than its nearest container", () => {
    const { publish } = mountLive();
    expect(paintedCursor()).toBe(ALPHA);

    const next = sampleBrief();
    const lane = laneOf(next, "newest", "changed");
    lane.items = lane.items.filter((item: Item) => item.id !== "alpha");
    publish(next);

    // Not the top of the page, and not nowhere: the lane that held it.
    expect(paintedCursor()).toBe("newest/changed");
  });

  it("keeps every fold the human chose, and opens only what is new", () => {
    const { publish } = mountLive();
    click(NEXT_LANE);
    click(BETA);
    expect(paintedOpen(NEXT_LANE)).toBe("true");
    expect(paintedOpen(BETA)).toBe("false");

    const next = sampleBrief();
    laneOf(next, "newest", "next").items.push({
      id: "delta",
      glance: "Delta needs an answer",
      explanation: "Something new arrived.",
      trust: "unverified",
      questions: [
        {
          id: "q-delta",
          anchor: { kind: "element", path: "newest/next/delta" },
          turns: [
            {
              author: "human",
              text: "What about delta?",
              at: "2026-07-25T14:00:00Z",
            },
          ],
        },
      ],
    });
    publish(next);

    expect(paintedOpen(NEXT_LANE)).toBe("true");
    expect(paintedOpen(BETA)).toBe("false");
    // New material follows the ordinary rules rather than arriving hidden:
    // an unanswered question opens itself.
    expect(paintedOpen("newest/next/delta")).toBe("true");
    expect(rowNode("newest/next/delta#q-delta")).not.toBeNull();
  });

  it("keeps the search text and the rows it is showing", () => {
    const { publish } = mountLive();
    press("/");
    type("#brief-search", "parity");
    const showing = [...document.querySelectorAll('[data-row-kind="item"]')]
      .map((row) => row.getAttribute("data-row-id"));

    const next = sampleBrief();
    next.title = "Published while searching";
    publish(next);

    expect(
      document.querySelector<HTMLInputElement>("#brief-search")?.value,
    ).toBe("parity");
    expect(
      [...document.querySelectorAll('[data-row-kind="item"]')].map((row) =>
        row.getAttribute("data-row-id"),
      ),
    ).toEqual(showing);
  });

  it("takes the jump labels away when they stop describing the page", () => {
    // The labels are a snapshot of one painted page, and typing one is a jump
    // to whatever wore it. A publish that changes which rows are painted must
    // take them away rather than leave a label pointing at a row that is gone.
    const { publish } = mountLive();
    press("f");
    const labelled = paintedHints();
    expect(Object.keys(labelled).length).toBeGreaterThan(0);

    const next = sampleBrief();
    const lane = laneOf(next, "newest", "changed");
    lane.items = lane.items.filter((item: Item) => item.id !== "beta");
    publish(next);

    expect(paintedHints()).toEqual({});
  });

  it("keeps the labels while the page is painting the same rows", () => {
    const { publish } = mountLive();
    press("f");
    const labelled = paintedHints();

    const next = sampleBrief();
    itemOf(next, ALPHA).glance = "Alpha, relabelled";
    publish(next);

    expect(paintedHints()).toEqual(labelled);
  });

  it("keeps the my-chats view and the help overlay up", () => {
    const { publish } = mountLive();
    press("m");
    press("?");
    expect(document.querySelector(".help")).not.toBeNull();

    publish(sampleBrief());

    expect(document.querySelector(".help")).not.toBeNull();
    expect(
      document
        .querySelector(".meta-chats")
        ?.getAttribute("aria-pressed"),
    ).toBe("true");
  });
});

describe("what a publish does to something half-written", () => {
  it("leaves an open chat box open, with the words still in it", () => {
    const { publish } = mountLive();
    composeAt(BETA);
    type(".composer textarea", "Half a question about beta");

    const next = sampleBrief();
    itemOf(next, ALPHA).glance = "Something else entirely";
    publish(next);

    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("Half a question about beta");
    expect(
      document.querySelector(".composer")?.getAttribute("data-anchor-id"),
    ).toBe(BETA);
  });

  it("closes the chat box when the row it was written at has gone", () => {
    const { publish } = mountLive();
    composeAt(BETA);
    type(".composer textarea", "A question about a row that is leaving");

    const next = sampleBrief();
    const lane = laneOf(next, "newest", "changed");
    lane.items = lane.items.filter((item: Item) => item.id !== "beta");
    publish(next);

    expect(document.querySelector(".composer")).toBeNull();
  });
});

describe("what a publish tells the human has changed", () => {
  it("retires a waiting sign the moment its words arrive", () => {
    saveSentRecords([
      {
        rowId: GAMMA,
        anchorId: GAMMA,
        text: "Did this land?",
        at: "2026-07-25T15:00:00Z",
      },
    ]);
    const { container, publish } = mountLive();
    const shell = container.firstElementChild;
    expect(document.querySelectorAll("p.pending")).toHaveLength(1);

    const next = sampleBrief();
    itemOf(next, GAMMA).questions = [
      {
        id: "q-folded",
        anchor: { kind: "element", path: GAMMA },
        turns: [
          {
            author: "human",
            text: "Did this land?",
            at: "2026-07-25T15:00:00Z",
          },
        ],
      },
    ];
    publish(next);

    expect(document.querySelectorAll("p.pending")).toHaveLength(0);
    // The conversation the daemon folded it into now carries the sign, and
    // the page it is on is the page it was on: nothing was replaced.
    expect(
      document.querySelectorAll(
        `[data-row-id="${GAMMA}#q-folded"] > .row-body > p.working`,
      ),
    ).toHaveLength(1);
    expect(container.firstElementChild).toBe(shell);
  });

  it("marks an answer that arrived as new, until it is visited", () => {
    const { publish } = mountLive();
    expect(rowNode(OPEN_THREAD)?.getAttribute("data-fresh")).toBe("false");

    const next = sampleBrief();
    const beta = itemOf(next, BETA);
    beta.questions?.[0]?.turns.push({
      author: "agent",
      text: "These four, with a case each.",
      at: "2026-07-25T13:00:00Z",
    });
    publish(next);

    expect(rowNode(OPEN_THREAD)?.getAttribute("data-fresh")).toBe("true");
    expect(
      rowNode(OPEN_THREAD)?.querySelector(".chip-new")?.textContent,
    ).toContain("New answer");
    // Only going to it clears the mark; nothing else does.
    click(OPEN_THREAD);
    expect(rowNode(OPEN_THREAD)?.getAttribute("data-fresh")).toBe("false");
  });

  it("follows the new document on counts, on the map and on the title", () => {
    const { publish } = mountLive();

    const next = sampleBrief();
    next.title = "A different brief";
    laneOf(next, "newest", "next").items.push({
      id: "delta",
      glance: "One more thing",
      explanation: "Added since.",
      trust: "unverified",
    });
    publish(next);

    expect(document.title).toBe("A different brief");
    // Four items in the sample document, and the one that just arrived.
    expect(
      document.querySelector('[data-count="items"] b')?.textContent,
    ).toBe("5");
    expect(document.querySelector(".brief-title")?.textContent).toBe(
      "A different brief",
    );
    expect(
      document.querySelector(`[data-map-lane="${NEXT_LANE}"] .map-label`)
        ?.textContent,
    ).toBe("What is next");
  });
});
