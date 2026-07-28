import { describe, expect, it } from "vitest";

import {
  composeRow,
  countItems,
  edgeRow,
  filterRows,
  moveByKind,
  nextAwaiting,
  restoreCursor,
} from "./cursor";
import { ancestorIds, outline, type Row } from "./outline";
import { sampleBrief } from "../test/sample-brief";

const ROWS = outline(sampleBrief());

const ITEMS = [
  "newest/changed/alpha",
  "newest/changed/beta",
  "newest/next/gamma",
  "older/history/one",
];

/**
 * Walk the cursor with one key, collecting where it lands.
 *
 * @param rows - Rows to move over.
 * @param start - Where the cursor starts.
 * @param kind - Kind of row to move between.
 * @param delta - Direction of travel.
 * @param steps - How many times to press.
 * @returns Each landing place in order.
 */
function walk(
  rows: Row[],
  start: string | null,
  kind: "item" | "lane",
  delta: number,
  steps: number,
): (string | null)[] {
  const seen: (string | null)[] = [];
  let at = start;
  for (let step = 0; step < steps; step += 1) {
    at = moveByKind(rows, at, kind, delta);
    seen.push(at);
  }
  return seen;
}

describe("the outline the cursor moves over", () => {
  it("lists every navigable row newest update first", () => {
    expect(ROWS.map((row) => row.id)).toEqual([
      "newest",
      "newest/changed",
      "newest/changed/alpha",
      "newest/changed/alpha#q-answered",
      "newest/changed/beta",
      "newest/changed/beta#q-open",
      "newest/next",
      "newest/next/gamma",
      "older",
      "older/history",
      "older/history/one",
      // Evidence is a row too, so the keyboard reaches it. It comes after
      // the item it belongs to and before that item's conversations, which
      // is where the page paints it.
      "older/history/one#~evidence",
    ]);
  });

  it("marks the rows that hold an unanswered question", () => {
    const awaiting = ROWS.filter((row) => row.awaiting).map((row) => row.id);

    expect(awaiting).toEqual([
      "newest",
      "newest/changed",
      "newest/changed/beta",
      "newest/changed/beta#q-open",
    ]);
  });

  it("names each row's containers, nearest first", () => {
    expect(ancestorIds("newest/changed/beta#q-open")).toEqual([
      "newest/changed/beta",
      "newest/changed",
      "newest",
    ]);
  });
});

describe("moving between items", () => {
  it("steps forward through every item in document order", () => {
    expect(walk(ROWS, ITEMS[0] ?? null, "item", 1, 3)).toEqual(ITEMS.slice(1));
  });

  it("steps back through them again", () => {
    expect(walk(ROWS, ITEMS[3] ?? null, "item", -1, 3)).toEqual(
      [...ITEMS].reverse().slice(1),
    );
  });

  it("stays put at the last item instead of wrapping to the first", () => {
    // Wrapping threw the reader from the newest entry to the oldest one at the
    // very bottom of the page, which reads as losing your place.
    expect(moveByKind(ROWS, "older/history/one", "item", 1)).toBe(
      "older/history/one",
    );
  });

  it("stays put at the first item instead of wrapping to the last", () => {
    expect(moveByKind(ROWS, "newest/changed/alpha", "item", -1)).toBe(
      "newest/changed/alpha",
    );
  });

  it("starts at the first item when the cursor is nowhere", () => {
    expect(moveByKind(ROWS, null, "item", 1)).toBe(ITEMS[0]);
  });

  it("leaves a thread for the next item after it, not the one it hangs on", () => {
    expect(
      moveByKind(ROWS, "newest/changed/alpha#q-answered", "item", 1),
    ).toBe("newest/changed/beta");
  });

  it("leaves a thread for the item it hangs on when moving back", () => {
    expect(
      moveByKind(ROWS, "newest/changed/alpha#q-answered", "item", -1),
    ).toBe("newest/changed/alpha");
  });
});

describe("moving between lanes", () => {
  it("is a different motion from moving between items", () => {
    expect(moveByKind(ROWS, "newest/changed", "lane", 1)).toBe("newest/next");
    expect(moveByKind(ROWS, "newest/changed", "item", 1)).toBe(
      "newest/changed/alpha",
    );
  });

  it("carries an item to the next lane below it", () => {
    expect(moveByKind(ROWS, "newest/changed/alpha", "lane", 1)).toBe(
      "newest/next",
    );
  });

  it("carries an item back to the lane holding it", () => {
    expect(moveByKind(ROWS, "newest/next/gamma", "lane", -1)).toBe(
      "newest/next",
    );
  });

  it("stops at the ends of the document rather than wrapping", () => {
    expect(moveByKind(ROWS, "older/history", "lane", 1)).toBe("older/history");
    expect(moveByKind(ROWS, "newest/changed", "lane", -1)).toBe(
      "newest/changed",
    );
  });
});

describe("jumping", () => {
  it("goes to the first and last row of the document", () => {
    expect(edgeRow(ROWS, "top")).toBe("newest");
    expect(edgeRow(ROWS, "bottom")).toBe("older/history/one#~evidence");
  });

  it("walks every unanswered question and then returns to the first", () => {
    const first = nextAwaiting(ROWS, null);

    expect(first).toBe("newest/changed/beta#q-open");
    expect(nextAwaiting(ROWS, first)).toBe("newest/changed/beta#q-open");
  });

  it("stays put when nothing awaits an answer", () => {
    const answered = ROWS.filter((row) => !row.awaiting);

    expect(nextAwaiting(answered, "newest/changed/alpha")).toBe(
      "newest/changed/alpha",
    );
  });
});

describe("restoring the cursor after the page reloads itself", () => {
  it("returns to the same row when it survived", () => {
    expect(restoreCursor(ROWS, "newest/next/gamma")).toBe("newest/next/gamma");
  });

  it("falls back to the nearest surviving container", () => {
    expect(restoreCursor(ROWS, "newest/changed/alpha#q-gone")).toBe(
      "newest/changed/alpha",
    );
    expect(restoreCursor(ROWS, "newest/changed/vanished")).toBe(
      "newest/changed",
    );
  });

  it("falls back to the first item when nothing is recognised", () => {
    expect(restoreCursor(ROWS, "some-other-run/lane/item")).toBe(ITEMS[0]);
    expect(restoreCursor(ROWS, null)).toBe(ITEMS[0]);
  });
});

describe("searching", () => {
  it("keeps matching items with the lanes that hold them", () => {
    const kept = filterRows(ROWS, "edge cases");

    expect(kept.map((row) => row.id)).toEqual([
      "newest",
      "newest/changed",
      "newest/changed/beta",
      "newest/changed/beta#q-open",
    ]);
    expect(countItems(kept)).toBe(1);
  });

  it("reaches evidence, tables and conversations", () => {
    expect(countItems(filterRows(ROWS, "exit status"))).toBe(1);
    expect(countItems(filterRows(ROWS, "WRONG"))).toBe(1);
    expect(countItems(filterRows(ROWS, "reference"))).toBe(1);
  });

  it("keeps everything for an empty query", () => {
    expect(filterRows(ROWS, "   ")).toHaveLength(ROWS.length);
  });

  it("moves the cursor only between rows the search left behind", () => {
    const kept = filterRows(ROWS, "still open");

    expect(moveByKind(kept, "newest/changed/beta", "item", 1)).toBe(
      "newest/changed/beta",
    );
  });
});

describe("where composition points", () => {
  it("targets an item, a lane and a thread as themselves", () => {
    expect(composeRow(ROWS, "newest/changed/alpha")?.anchorId).toBe(
      "newest/changed/alpha",
    );
    expect(composeRow(ROWS, "newest/next")?.anchorId).toBe("newest/next");
    const thread = composeRow(ROWS, "newest/changed/beta#q-open");
    expect(thread?.anchorId).toBe("newest/changed/beta");
    expect(thread?.parentThreadId).toBe("q-open");
  });

  it("hands an update to the first lane it holds", () => {
    expect(composeRow(ROWS, "newest")?.id).toBe("newest/changed");
  });
});
