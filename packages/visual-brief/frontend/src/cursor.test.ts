import { describe, expect, it } from "vitest";

import {
  composeRow,
  effectiveCursor,
  filterRows,
  moveByKind,
  moveByRow,
} from "./cursor";
import { outline } from "./outline";
import { sampleBrief } from "../test/sample-brief";

const rows = outline(sampleBrief());

describe("v2 cursor arithmetic", () => {
  it("walks every painted row, including update and lane headers", () => {
    expect(moveByRow(rows, null, 1)).toBe("newest");
    expect(moveByRow(rows, "newest", 1)).toBe("newest/changed");
    expect(moveByRow(rows, "newest/changed", -1)).toBe("newest");
  });

  it("jumps between lanes without wrapping", () => {
    expect(moveByKind(rows, null, "lane", 1)).toBe("newest/changed");
    expect(moveByKind(rows, "newest/changed", "lane", 1)).toBe(
      "newest/next",
    );
    expect(moveByKind(rows, "older/history", "lane", 1)).toBe(
      "older/history",
    );
  });

  it("does not invent or repair a human cursor for paint", () => {
    expect(effectiveCursor(rows, null)).toBeNull();
    expect(effectiveCursor(rows, "removed")).toBeNull();
    expect(effectiveCursor(rows, "newest")).toBe("newest");
  });
});

describe("pure views", () => {
  it("search keeps a matching item and only its ancestors", () => {
    const filtered = filterRows(rows, "Four edge cases");
    expect(filtered.map((row) => row.id)).toEqual([
      "newest",
      "newest/changed",
      "newest/changed/beta",
    ]);
  });

  it("search paints matching evidence and conversations with ancestors", () => {
    expect(filterRows(rows, "exit status 0").map((row) => row.id)).toEqual([
      "older",
      "older/history",
      "older/history/one",
      "older/history/one#~evidence",
    ]);
    expect(filterRows(rows, "Is alpha checked").map((row) => row.id)).toEqual([
      "newest",
      "newest/changed",
      "newest/changed/alpha",
      "newest/changed/alpha#q-answered",
    ]);
  });

  it("routes composition at evidence and briefing root rows", () => {
    expect(composeRow(rows, "newest")?.id).toBe("newest");
    expect(composeRow(rows, "newest/changed/alpha")?.id).toBe(
      "newest/changed/alpha",
    );
  });
});
