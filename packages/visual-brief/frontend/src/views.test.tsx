import { describe, expect, it } from "vitest";

import { saveSentRecords } from "./session-store";
import {
  mount,
  paintedCursor,
  paintedOpen,
  press,
  useHarness,
} from "../test/harness";

const ALPHA = "newest/changed/alpha";
const BETA = "newest/changed/beta";
const ANSWERED = `${ALPHA}#q-answered`;
const OPEN_THREAD = `${BETA}#q-open`;

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
 * Read the rows the page has painted as expanded.
 *
 * @returns The expanded row ids.
 */
function openRows(): string[] {
  return [...document.querySelectorAll('[data-open="true"]')].map(
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

describe("opening and folding the whole page", () => {
  it("opens every row on E", () => {
    mount();

    press("E");

    expect(openRows()).toEqual(paintedRows());
    expect(paintedRows()).toContain("older/history/one");
    expect(paintedRows()).toContain(ANSWERED);
  });

  it("folds back to lanes on C, updates still holding them open", () => {
    mount();
    press("E");

    press("C");

    expect(openRows()).toEqual(["newest", "older"]);
    expect(paintedRows()).toEqual([
      "newest",
      "newest/changed",
      "newest/next",
      "older",
      "older/history",
    ]);
  });

  it("takes the cursor up to the nearest row that is still painted", () => {
    mount();
    press("n");
    expect(paintedCursor()).toBe(OPEN_THREAD);

    press("C");

    expect(paintedCursor()).toBe("newest/changed");
  });

  it("leaves the cursor alone when everything opens", () => {
    mount();
    press("j");
    const before = paintedCursor();

    press("E");

    expect(paintedCursor()).toBe(before);
  });
});

describe("the chats view", () => {
  it("shows every conversation the human wrote in, answered or not", () => {
    mount();

    press("m");

    expect(paintedRows()).toEqual([
      "newest",
      "newest/changed",
      ALPHA,
      ANSWERED,
      BETA,
      OPEN_THREAD,
    ]);
  });

  it("finds them again after everything has been folded away", () => {
    mount();
    press("C");
    expect(paintedRows()).not.toContain(ANSWERED);

    press("m");

    expect(paintedRows()).toContain(ANSWERED);
    expect(paintedCursor()).toBe(ANSWERED);
  });

  it("cycles the conversations with j and k", () => {
    mount();
    press("m");
    expect(paintedCursor()).toBe(ANSWERED);

    press("j");
    expect(paintedCursor()).toBe(OPEN_THREAD);

    press("k");
    expect(paintedCursor()).toBe(ANSWERED);
  });

  it("leaves on the same key, and on Escape", () => {
    mount();

    press("m");
    press("m");
    expect(paintedRows()).toContain("newest/next");

    press("m");
    expect(paintedRows()).not.toContain("newest/next");
    press("Escape");
    expect(paintedRows()).toContain("newest/next");
  });

  it("is offered by the masthead, with the count on it", () => {
    mount();
    const button = document.querySelector<HTMLButtonElement>(".meta-chats");

    expect(button?.getAttribute("data-chats-count")).toBe("2");
    expect(button?.getAttribute("aria-pressed")).toBe("false");

    button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(
      document.querySelector(".meta-chats")?.getAttribute("aria-pressed"),
    ).toBe("true");
    expect(paintedCursor()).toBe(ANSWERED);
  });
});

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

describe("chatting wherever the cursor is", () => {
  it("opens a lane's chat from the keyboard exactly as its button does", () => {
    mount();
    press("J");
    expect(paintedCursor()).toBe("newest/next");

    press("c");
    const fromKeys = document.querySelector(".composer");
    const anchor = fromKeys?.getAttribute("data-anchor-id");
    const label = fromKeys?.querySelector(".composer-label")?.textContent;
    press("Escape");

    document
      .querySelector('[data-row-id="newest/next"] > .row-head .chat-button')
      ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    const fromMouse = document.querySelector(".composer");

    expect(anchor).toBe("newest/next");
    expect(fromMouse?.getAttribute("data-anchor-id")).toBe(anchor);
    expect(fromMouse?.querySelector(".composer-label")?.textContent).toBe(
      label,
    );
  });

  it("reaches every granularity the mouse can chat at", () => {
    mount();
    const written: [string, string, string | null][] = [];
    /**
     * Open the chat box where the cursor is and note where it points.
     *
     * @param where - What kind of row the cursor is on.
     */
    const chatHere = (where: string): void => {
      press("c");
      const box = document.querySelector(".composer");
      written.push([
        where,
        box?.getAttribute("data-anchor-id") ?? "",
        box?.getAttribute("data-parent-id") ?? null,
      ]);
      press("Escape");
    };

    press("g");
    chatHere("update");
    press("J");
    chatHere("lane");
    press("j");
    chatHere("item");
    press("n");
    chatHere("conversation");

    // An update has no anchor of its own, so writing at one lands in its
    // first lane; everything else writes exactly where the cursor is, and a
    // conversation continues itself. Writing somewhere also moves the cursor
    // there, which is why the lane reached from the update is the next one.
    expect(written).toEqual([
      ["update", "newest/changed", null],
      ["lane", "newest/next", null],
      ["item", "newest/next/gamma", null],
      ["conversation", BETA, "q-open"],
    ]);
  });
});

describe("after the reload a send causes", () => {
  it("opens on the conversation the human just wrote in", () => {
    saveSentRecords([
      {
        rowId: ALPHA,
        anchorId: ALPHA,
        text: "Is alpha checked?",
        at: "2026-07-25T11:00:00Z",
        loads: 0,
      },
    ]);

    mount();

    expect(paintedCursor()).toBe(ANSWERED);
    expect(paintedOpen(ANSWERED)).toBe("true");
    expect(document.querySelector("p.pending")).toBeNull();
  });

  it("carries the waiting sign over, folded row and all", () => {
    saveSentRecords([
      {
        rowId: "newest/next/gamma",
        anchorId: "newest/next/gamma",
        text: "Will this outlast the reload?",
        at: "2026-07-25T13:00:00Z",
        loads: 0,
      },
    ]);

    mount();

    const note = document.querySelector(
      '[data-row-id="newest/next/gamma"] p.pending',
    );
    expect(note?.textContent).toContain("Will this outlast the reload?");
    expect(note?.getAttribute("data-stalled")).toBe("false");
    expect(
      document.querySelector('[data-row-id="newest/next/gamma"] p.working')
        ?.textContent,
    ).toBe("agent is working");
  });
});
