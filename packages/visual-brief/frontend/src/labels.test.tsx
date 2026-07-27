import { describe, expect, it } from "vitest";

import {
  click,
  mount,
  paintedCursor,
  press,
  useHarness,
} from "../test/harness";

const ALPHA = "newest/changed/alpha";
const BETA = "newest/changed/beta";

useHarness();

/**
 * Read the rows the page has painted, in order.
 *
 * @returns The painted row ids.
 */
function paintedRows(): string[] {
  return [...document.querySelectorAll("[data-row-id]")].map(
    (row) => row.getAttribute("data-row-id") ?? "",
  );
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
 * Read the citation numbers the page is painting.
 *
 * @returns Each number, keyed by the row wearing it.
 */
function paintedOrdinals(): Record<string, string> {
  const ordinals: Record<string, string> = {};
  for (const row of document.querySelectorAll("[data-row-id]")) {
    const mark = row.querySelector(":scope > .row-head > .ordinal");
    if (mark !== null) {
      ordinals[row.getAttribute("data-row-id") ?? ""] = mark.textContent ?? "";
    }
  }
  return ordinals;
}

describe("jumping by label", () => {
  it("labels every painted row, at one fixed length", () => {
    mount();

    press("f");

    const labels = paintedHints();
    expect(Object.keys(labels)).toEqual(paintedRows());
    expect(new Set(Object.values(labels).map((label) => label.length))).toEqual(
      new Set([1]),
    );
  });

  it("goes where the typed label is, and puts the labels away", () => {
    mount();
    press("f");
    const target = Object.entries(paintedHints()).find(
      ([id]) => id === "newest/next",
    );

    press(target?.[1] ?? "");

    expect(paintedCursor()).toBe("newest/next");
    expect(paintedHints()).toEqual({});
  });

  it("keeps its keys to itself while the labels are up", () => {
    mount();
    const before = paintedCursor();

    press("f");
    press("q");

    expect(paintedCursor()).toBe(before);
    expect(Object.keys(paintedHints()).length).toBeGreaterThan(0);

    press("Escape");

    expect(paintedHints()).toEqual({});
    expect(paintedCursor()).toBe(before);
  });

  it("gives the keyboard back for good once the page has been touched", () => {
    // Opening a row with the mouse takes the labels away. Shutting it again
    // paints exactly the list they were taken for — and if that brought them
    // back, every key would be swallowed by a mode with nothing on screen,
    // including the one key that would have explained the way out.
    mount();
    press("f");
    expect(Object.keys(paintedHints()).length).toBeGreaterThan(0);

    click("newest/next");
    expect(paintedHints()).toEqual({});
    click("newest/next");

    expect(paintedHints()).toEqual({});
    press("?");

    expect(document.querySelector(".help")).not.toBeNull();
  });

  it("grows to two keys when one is not enough", () => {
    mount();
    press("E");

    press("f");

    const labels = Object.values(paintedHints());
    expect(labels.length).toBeGreaterThan(9);
    expect(new Set(labels.map((label) => label.length))).toEqual(new Set([2]));
  });
});

describe("numbers to cite by", () => {
  it("numbers the items on the page, and nothing else", () => {
    mount();

    expect(paintedOrdinals()).toEqual({ [ALPHA]: "1", [BETA]: "2" });
  });

  it("renumbers across the whole page when everything opens", () => {
    mount();

    press("E");

    expect(paintedOrdinals()).toEqual({
      [ALPHA]: "1",
      [BETA]: "2",
      "newest/next/gamma": "3",
      "older/history/one": "4",
    });
  });

  it("shows no number on content that is folded away", () => {
    mount();

    press("C");

    expect(paintedOrdinals()).toEqual({});
  });
});
