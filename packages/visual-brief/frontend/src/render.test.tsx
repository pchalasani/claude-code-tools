import { describe, expect, it } from "vitest";

import { cursorStorageKey } from "./session-store";
import { outline } from "./outline";
import { sampleBrief } from "../test/sample-brief";
import {
  click,
  deferTransitions,
  flushTransitions,
  mount,
  paintedCursor,
  paintedOpen,
  press,
  pressAt,
  unmount,
  useHarness,
} from "../test/harness";

useHarness();

describe("the painted page", () => {
  it("lays rows out in the order the cursor walks them", () => {
    mount();
    for (const id of ["newest/changed/alpha", "newest/next"]) {
      document
        .querySelector(`[data-row-id="${id}"] .row-toggle`)
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    }
    const painted = [...document.querySelectorAll("[data-row-id]")].map(
      (row) => row.getAttribute("data-row-id"),
    );
    const shown = new Set(painted);
    const expected = outline(sampleBrief())
      .map((row) => row.id)
      .filter((id) => shown.has(id));

    expect(painted).toEqual(expected);
    expect(painted).toContain("newest/changed/alpha#q-answered");
    expect(painted).toContain("newest/next/gamma");
  });

  it("marks exactly one row as the cursor, starting on the first item", () => {
    mount();

    expect(paintedCursor()).toBe("newest/changed/alpha");
  });

  it("moves the mark, not the browser's focus, when j is pressed", () => {
    mount();
    const before = document.activeElement;

    press("j");

    expect(paintedCursor()).toBe("newest/changed/beta");
    expect(document.activeElement).toBe(before);
  });

  it("answers the shifted keys as well as the lowercase ones", () => {
    mount();

    press("J");
    expect(paintedCursor()).toBe("newest/next");

    press("K");
    expect(paintedCursor()).toBe("newest/changed");

    press("G");
    expect(paintedCursor()).toBe("older/history/one");

    press("?");
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();

    press("Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it("expands and folds the cursor row with space", () => {
    mount();
    const row = () => document.querySelector('[data-row-id="newest/changed/alpha"]');

    expect(row()?.getAttribute("data-open")).toBe("false");

    press(" ");
    expect(row()?.getAttribute("data-open")).toBe("true");
    expect(row()?.querySelector(".explanation")).not.toBeNull();

    press(" ");
    expect(row()?.getAttribute("data-open")).toBe("false");
  });

  it("sends n to the question that is still waiting", () => {
    mount();

    press("n");

    expect(paintedCursor()).toBe("newest/changed/beta#q-open");
  });

  it("gives the mouse and the keyboard the same cursor", () => {
    mount();

    click("newest/next");
    expect(paintedCursor()).toBe("newest/next");

    click("newest/next/gamma");
    expect(paintedCursor()).toBe("newest/next/gamma");

    press("k");
    expect(paintedCursor()).toBe("newest/changed/beta");
  });

  it("stops answering keys while a question is being typed", () => {
    mount();
    press("a");
    const box = document.querySelector<HTMLTextAreaElement>(".composer textarea");
    expect(box).not.toBeNull();

    box?.dispatchEvent(
      new KeyboardEvent("keydown", { key: "j", bubbles: true }),
    );

    expect(paintedCursor()).toBe("newest/changed/alpha");
    expect(document.querySelector(".composer")).not.toBeNull();

    box?.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );

    expect(document.querySelector(".composer")).toBeNull();
  });

  it("points composition at the thread when the cursor is on one", () => {
    mount();
    press("n");
    press("a");

    const form = document.querySelector(".composer");

    expect(form?.getAttribute("data-anchor-id")).toBe("newest/changed/beta");
    expect(form?.getAttribute("data-parent-id")).toBe("q-open");
  });

  it("filters items down to the search, and back again", () => {
    mount();
    press("/");
    const box = document.querySelector<HTMLInputElement>("#brief-search");
    if (box === null) {
      throw new Error("no search box");
    }
    box.value = "edge cases";
    box.dispatchEvent(new Event("input", { bubbles: true }));

    const items = () =>
      document.querySelectorAll('[data-row-kind="item"]').length;
    expect(items()).toBe(1);

    box.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );

    expect(document.querySelector("#brief-search")).toBeNull();
    expect(items()).toBeGreaterThan(1);
  });

  it("puts the cursor back on the same row when the page reloads itself", () => {
    mount();
    press("j");
    press("j");
    const before = paintedCursor();

    unmount();
    mount();

    expect(before).toBe("newest/next/gamma");
    expect(paintedCursor()).toBe(before);
  });

  it("falls back to the nearest survivor when the row is gone", () => {
    window.sessionStorage.setItem(
      cursorStorageKey(),
      "newest/changed/alpha#q-removed",
    );

    mount();

    expect(paintedCursor()).toBe("newest/changed/alpha");
  });

  it("opens everything holding a question that awaits an answer", () => {
    mount();

    expect(paintedOpen("newest")).toBe("true");
    expect(paintedOpen("newest/changed")).toBe("true");
    expect(paintedOpen("newest/changed/beta")).toBe("true");
    expect(paintedOpen("newest/changed/beta#q-open")).toBe("true");
    expect(paintedOpen("newest/changed/alpha")).toBe("false");
  });

  it("keeps both presses when two land inside one animation frame", () => {
    mount();
    deferTransitions();

    press("j");
    press("j");
    flushTransitions();

    expect(paintedCursor()).toBe("newest/next/gamma");
  });

  it("folds the row the second key saw, not the one it left", () => {
    mount();
    deferTransitions();

    press("j");
    press(" ");
    flushTransitions();

    expect(paintedCursor()).toBe("newest/changed/beta");
    expect(paintedOpen("newest/changed/beta")).toBe("false");
    expect(paintedOpen("newest/changed/alpha")).toBe("false");
  });

  it("keeps Space on the cursor row after the mouse focused another", () => {
    mount();
    click("newest/next");
    const head = document.querySelector(
      '[data-row-id="newest/next"] .row-toggle',
    );
    if (head === null) {
      throw new Error("no row head");
    }

    pressAt(head, "j");
    expect(paintedCursor()).toBe("newest/next/gamma");

    pressAt(head, " ");

    expect(paintedOpen("newest/next/gamma")).toBe("true");
    expect(paintedOpen("newest/next")).toBe("true");
    expect(paintedCursor()).toBe("newest/next/gamma");
  });

  it("keeps a row marked when the map reaches a filtered-away lane", () => {
    mount();
    press("/");
    const box = document.querySelector<HTMLInputElement>("#brief-search");
    if (box === null) {
      throw new Error("no search box");
    }
    box.value = "parser was replaced";
    box.dispatchEvent(new Event("input", { bubbles: true }));
    expect(paintedCursor()).toBe("older/history/one");

    document
      .querySelector('[data-map-lane="newest/next"]')
      ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    // paintedCursor fails unless exactly one row on the page carries the mark.
    expect(paintedCursor()).toBe("newest/next");
    expect(document.querySelector('[data-row-id="newest/next"]')).not.toBeNull();
    expect(box.value).toBe("");

    press("j");
    expect(paintedCursor()).toBe("newest/next/gamma");
  });

  it("searches into a folded update and marks what it found", () => {
    mount();
    press("/");
    const box = document.querySelector<HTMLInputElement>("#brief-search");
    if (box === null) {
      throw new Error("no search box");
    }

    box.value = "parser was replaced";
    box.dispatchEvent(new Event("input", { bubbles: true }));

    expect(
      document.querySelector('[data-row-id="older/history/one"]'),
    ).not.toBeNull();
    expect(paintedCursor()).toBe("older/history/one");
  });
});

describe("the Now panel", () => {
  /** A brief whose middle update is the Now panel. */
  function briefWithNow() {
    const brief = sampleBrief();
    brief.updates.splice(1, 0, {
      id: "now",
      timestamp: "2026-07-27 14:00",
      headline: "Where things stand",
      summary: "Current state, rewritten in place.",
      lanes: [
        {
          id: "working",
          name: "Working now",
          items: [
            {
              id: "cursor",
              glance: "The cursor is visible",
              explanation: "Selection is painted by the application.",
              trust: "verified-by-me",
            },
          ],
        },
      ],
    });
    return brief;
  }

  it("pins the Now update above history wherever it sits in the file", () => {
    mount(briefWithNow());

    const painted = [...document.querySelectorAll("[data-row-kind='update']")]
      .map((row) => row.getAttribute("data-row-id"));
    expect(painted[0]).toBe("now");
  });

  it("labels the history below it as earlier updates", () => {
    mount(briefWithNow());

    const heading = document.querySelector(".earlier-heading");
    expect(heading?.textContent).toBe("Earlier updates");
    expect(
      heading?.nextElementSibling?.getAttribute("data-row-id"),
    ).toBe("newest");
  });

  it("shows no earlier-updates label without a Now panel", () => {
    mount();

    expect(document.querySelector(".earlier-heading")).toBeNull();
  });

  it("drops the label when search leaves no earlier update visible", () => {
    mount(briefWithNow());
    press("/");
    const box = document.querySelector<HTMLInputElement>("#brief-search");
    if (box === null) {
      throw new Error("no search box");
    }

    // Matches only the Now panel's item, so no history survives the filter.
    box.value = "painted by the application";
    box.dispatchEvent(new Event("input", { bubbles: true }));
    expect(document.querySelector(".earlier-heading")).toBeNull();

    // Matches nothing at all: still no orphaned divider.
    box.value = "matches nothing anywhere";
    box.dispatchEvent(new Event("input", { bubbles: true }));
    expect(document.querySelector(".earlier-heading")).toBeNull();

    // Clearing the search brings the divider back.
    box.value = "";
    box.dispatchEvent(new Event("input", { bubbles: true }));
    expect(document.querySelector(".earlier-heading")).not.toBeNull();
  });

  it("opens the Now panel and its lanes so state reads without a click", () => {
    mount(briefWithNow());

    expect(paintedOpen("now")).toBe("true");
    expect(paintedOpen("now/working")).toBe("true");
    // History without an awaiting question starts folded; "newest" still
    // opens itself because it holds an unanswered thread, which wins.
    expect(paintedOpen("older")).toBe("false");
    expect(paintedOpen("newest")).toBe("true");
  });

  it("reads as current state, not as a dated event", () => {
    mount(briefWithNow());
    const head = document.querySelector('[data-row-id="now"] .row-head');

    expect(head?.querySelector(".now-mark")?.textContent).toBe("Now");
    expect(head?.querySelector(".update-time")?.textContent).toBe(
      "as of 2026-07-27 14:00",
    );
  });
});

