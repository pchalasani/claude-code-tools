/**
 * What a publish is allowed to do to a page somebody is reading.
 *
 * Every assertion is made by publishing into a mounted application and then
 * reading the page, because that is the whole of what a publish now is: the
 * poller fetches a document and hands it over, and nothing else is supposed to
 * notice. What must survive is not a list of internals but a list of places a
 * human has been thrown out of — their cursor, their folds, what they were in
 * the middle of writing.
 *
 * This half is about the places that must not move. What a publish is allowed
 * to change, and what it does to something half-written, is next door in
 * ``live-publish.test.tsx``.
 */

import { describe, expect, it } from "vitest";

import type { Item } from "./document";
import {
  click,
  mountLive,
  paintedCursor,
  paintedOpen,
  press,
  rowNode,
  typeInto,
  useHarness,
} from "../test/harness";
import { itemOf, laneOf, sampleBrief } from "../test/sample-brief";

const ALPHA = "newest/changed/alpha";
const BETA = "newest/changed/beta";
const OPEN_THREAD = `${BETA}#q-open`;
const NEXT_LANE = "newest/next";

useHarness();

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

  it("lands a vanished cursor somewhere the search still paints", () => {
    // Alpha and gamma both match; the cursor is on alpha, and alpha goes. Its
    // lane survives the publish but not the search, so landing there would
    // leave the human looking at a page with nothing marked on it while gamma
    // is still in front of them. The filter is theirs and stays.
    const { publish } = mountLive();
    press("/");
    typeInto("#brief-search", "parity");
    expect(paintedCursor()).toBe(ALPHA);

    const next = sampleBrief();
    const lane = laneOf(next, "newest", "changed");
    lane.items = lane.items.filter((item: Item) => item.id !== "alpha");
    publish(next);

    expect(paintedCursor()).toBe("newest");
    expect(
      document.querySelector<HTMLInputElement>("#brief-search")?.value,
    ).toBe("parity");
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

  it("opens what holds a new question, rather than hiding it", () => {
    // A fold is a decision about material the human has already seen. A
    // question arriving underneath one is material they have not seen, and a
    // question the page paints nowhere has not arrived at all.
    const { publish } = mountLive();
    click(BETA);
    expect(paintedOpen(BETA)).toBe("false");

    const next = sampleBrief();
    itemOf(next, BETA).questions?.push({
      id: "q-later",
      anchor: { kind: "element", path: BETA },
      turns: [
        {
          author: "human",
          text: "And the fifth case?",
          at: "2026-07-25T16:00:00Z",
        },
      ],
    });
    publish(next);

    expect(paintedOpen(BETA)).toBe("true");
    expect(rowNode(`${BETA}#q-later`)).not.toBeNull();
  });

  it("keeps the search text and the rows it is showing", () => {
    const { publish } = mountLive();
    press("/");
    typeInto("#brief-search", "parity");
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
