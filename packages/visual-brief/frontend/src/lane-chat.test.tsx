import { describe, expect, it } from "vitest";

import type { BriefDocument } from "./document";
import { outline } from "./outline";
import { click, mount, paintedCursor, press, useHarness } from "../test/harness";
import { sampleBrief } from "../test/sample-brief";

const LANE = "newest/changed";
const FIRST = `${LANE}#q-lane-first`;
const SECOND = `${LANE}#q-lane-second`;
const ALPHA = `${LANE}/alpha`;
const BETA = `${LANE}/beta`;

useHarness();

/**
 * Build a page whose busiest lane has been chatted about twice.
 *
 * @returns The document.
 */
function chattedLane(): BriefDocument {
  const brief = sampleBrief();
  const lane = brief.updates
    .find((update) => update.id === "newest")
    ?.lanes.find((one) => one.id === "changed");
  if (lane === undefined) {
    throw new Error("the sample document lost the lane this test writes at");
  }
  lane.questions = [
    {
      id: "q-lane-first",
      anchor: { kind: "element", path: LANE },
      turns: [
        {
          author: "human",
          text: "What is this lane for?",
          at: "2026-07-25T14:00:00Z",
        },
        {
          author: "agent",
          text: "Everything that moved.",
          at: "2026-07-25T14:01:00Z",
        },
      ],
    },
    {
      id: "q-lane-second",
      anchor: { kind: "element", path: LANE },
      turns: [
        {
          author: "human",
          text: "And what is still missing?",
          at: "2026-07-25T15:00:00Z",
        },
      ],
    },
  ];
  return brief;
}

/**
 * Read the rows the page has painted, in the order it painted them.
 *
 * @returns The painted row ids.
 */
function paintedRows(): string[] {
  return [...document.querySelectorAll("[data-row-id]")].map(
    (row) => row.getAttribute("data-row-id") ?? "",
  );
}

describe("where a lane's own conversations sit", () => {
  it("paints them under the lane's head, above its items", () => {
    mount(chattedLane());

    const painted = paintedRows();
    expect(painted.indexOf(LANE)).toBeLessThan(painted.indexOf(FIRST));
    expect(painted.indexOf(FIRST)).toBeLessThan(painted.indexOf(ALPHA));
  });

  it("paints the newest lane conversation first", () => {
    mount(chattedLane());

    const painted = paintedRows();
    expect(painted.indexOf(SECOND)).toBeLessThan(painted.indexOf(FIRST));
  });

  it("leaves the items in the order the document put them", () => {
    mount(chattedLane());

    const painted = paintedRows();
    expect(painted.indexOf(ALPHA)).toBeLessThan(painted.indexOf(BETA));
  });

  it("paints the newest item conversation first", () => {
    const brief = sampleBrief();
    const alpha = brief.updates
      .find((update) => update.id === "newest")
      ?.lanes.find((lane) => lane.id === "changed")
      ?.items.find((item) => item.id === "alpha");
    if (alpha === undefined) {
      throw new Error("the sample document lost alpha");
    }
    alpha.questions?.push({
      id: "q-newest",
      anchor: { kind: "element", path: ALPHA },
      turns: [
        {
          author: "human",
          text: "This was asked most recently.",
          at: "2026-07-25T16:00:00Z",
        },
      ],
    });
    mount(brief);
    press("E");

    const threads = [
      ...document.querySelectorAll(
        `[data-row-id="${ALPHA}"] > .row-body > .row-thread`,
      ),
    ].map((row) => row.getAttribute("data-row-id"));
    expect(threads).toEqual([
      `${ALPHA}#q-newest`,
      `${ALPHA}#q-answered`,
    ]);
  });

  it("paints exactly the list the cursor walks, in exactly that order", () => {
    // The two are one list. If the outline said the conversation came last
    // while the page painted it first, every jump label, every j and k and
    // every restored cursor would be pointing at the wrong row.
    const brief = chattedLane();
    mount(brief);

    press("E");

    expect(paintedRows()).toEqual(outline(brief).map((row) => row.id));
  });

  it("is still reached by a jump label", () => {
    mount(chattedLane());
    press("f");
    const label = document
      .querySelector(`[data-row-id="${SECOND}"] .hint`)
      ?.getAttribute("data-hint");
    expect(label).toBeTruthy();

    for (const key of label ?? "") {
      press(key);
    }

    expect(paintedCursor()).toBe(SECOND);
  });

  it("is still collected by the chats view", () => {
    mount(chattedLane());

    press("m");

    expect(paintedRows()).toContain(FIRST);
    expect(paintedRows()).toContain(SECOND);
  });

  it("opens the chat box under the head, not past every item", () => {
    mount(chattedLane());
    click(LANE);
    expect(paintedCursor()).toBe(LANE);

    press("c");

    const body = document.querySelector(`[data-row-id="${LANE}"] > .row-body`);
    const children = [...(body?.children ?? [])];
    const box = children.findIndex((child) => child.matches("form.composer"));
    const item = children.findIndex(
      (child) => child.getAttribute("data-row-id") === ALPHA,
    );
    expect(box).toBeGreaterThanOrEqual(0);
    expect(box).toBeLessThan(item);
  });
});
