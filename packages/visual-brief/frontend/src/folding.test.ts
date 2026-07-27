import { describe, expect, it } from "vitest";

import { chatRows, countChats } from "./cursor";
import {
  collapseToLaneIds,
  expandAllIds,
  itemOrdinals,
  nearestPainted,
  paintedRows,
} from "./folding";
import { outline } from "./outline";
import { sampleBrief } from "../test/sample-brief";

const rows = outline(sampleBrief());

/**
 * List the ids of some rows.
 *
 * @param some - The rows.
 * @returns Their ids, in order.
 */
function ids(some: { id: string }[]): string[] {
  return some.map((row) => row.id);
}

describe("folding the whole page", () => {
  it("opens every row there is", () => {
    expect(expandAllIds(rows).size).toBe(rows.length);
  });

  it("folds back to lanes, leaving the updates holding them open", () => {
    const open = collapseToLaneIds(rows);

    expect([...open]).toEqual(["newest", "older"]);
    expect(ids(paintedRows(rows, open))).toEqual([
      "newest",
      "newest/changed",
      "newest/next",
      "older",
      "older/history",
    ]);
  });

  it("paints a row only when everything holding it is open", () => {
    const open = new Set(["newest", "newest/changed"]);

    expect(ids(paintedRows(rows, open))).toEqual([
      "newest",
      "newest/changed",
      "newest/changed/alpha",
      "newest/changed/beta",
      "newest/next",
      "older",
    ]);
  });
});

describe("keeping the cursor on the page", () => {
  it("climbs to the innermost container that survives the fold", () => {
    const open = collapseToLaneIds(rows);

    expect(nearestPainted("newest/changed/beta#q-open", open)).toBe(
      "newest/changed",
    );
    expect(nearestPainted("newest/changed", open)).toBe("newest/changed");
    expect(nearestPainted("newest", open)).toBe("newest");
  });

  it("stays where it is when everything is open", () => {
    expect(
      nearestPainted("newest/changed/beta#q-open", expandAllIds(rows)),
    ).toBe("newest/changed/beta#q-open");
  });

  it("has nowhere to go from nowhere", () => {
    expect(nearestPainted(null, expandAllIds(rows))).toBeNull();
  });
});

describe("numbering what can be seen", () => {
  it("numbers the painted items across the whole page", () => {
    const painted = paintedRows(rows, expandAllIds(rows));

    expect([...itemOrdinals(painted).entries()]).toEqual([
      ["newest/changed/alpha", 1],
      ["newest/changed/beta", 2],
      ["newest/next/gamma", 3],
      ["older/history/one", 4],
    ]);
  });

  it("numbers nothing inside a folded lane", () => {
    const painted = paintedRows(rows, collapseToLaneIds(rows));

    expect(itemOrdinals(painted).size).toBe(0);
  });
});

describe("the human's own conversations", () => {
  it("keeps every conversation they wrote in, answered or not", () => {
    expect(ids(chatRows(rows))).toEqual([
      "newest",
      "newest/changed",
      "newest/changed/alpha",
      "newest/changed/alpha#q-answered",
      "newest/changed/beta",
      "newest/changed/beta#q-open",
    ]);
    expect(countChats(rows)).toBe(2);
  });

  it("keeps the rows holding them, so they can be reached", () => {
    const kept = new Set(ids(chatRows(rows)));

    expect(kept.has("newest/changed")).toBe(true);
    expect(kept.has("newest/next")).toBe(false);
    expect(kept.has("older")).toBe(false);
  });
});
