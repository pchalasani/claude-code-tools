import { describe, expect, it } from "vitest";

import { cursorStorageKey, saveSentRecords } from "./session-store";
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

  it("lets go of a chat box it is about to hide", () => {
    // The box only renders inside a painted row. Entering the view over the
    // top of one pointed somewhere the view does not show would leave the
    // human with an invisible box holding their words, and the next Escape
    // would throw those words away instead of leaving the view.
    mount();
    press("J");
    expect(paintedCursor()).toBe("newest/next");
    press("c");
    const box = document.querySelector<HTMLTextAreaElement>(
      ".composer textarea",
    );
    expect(box).not.toBeNull();
    if (box !== null) {
      box.value = "half a thought";
    }
    box?.dispatchEvent(new Event("input", { bubbles: true }));

    document
      .querySelector(".meta-chats")
      ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(paintedRows()).not.toContain("newest/next");
    expect(document.querySelector(".composer")).toBeNull();

    press("Escape");

    expect(paintedRows()).toContain("newest/next");
    expect(
      document.querySelector(".meta-chats")?.getAttribute("aria-pressed"),
    ).toBe("false");
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

describe("after a page load that followed a send", () => {
  it("opens on the conversation the human just wrote in", () => {
    saveSentRecords([
      {
        rowId: ALPHA,
        anchorId: ALPHA,
        text: "Is alpha checked?",
        at: "2026-07-25T11:00:00Z",
      },
    ]);

    mount();

    expect(paintedCursor()).toBe(ANSWERED);
    expect(paintedOpen(ANSWERED)).toBe("true");
    expect(document.querySelector("p.pending")).toBeNull();
    // The anchored landing must be written into the real cursor store, not
    // just painted: the NEXT reload (the agent's answer arriving) restores
    // from storage, and forgetting to write meant that second reload threw
    // the human back to wherever they were before they wrote.
    expect(window.sessionStorage.getItem(cursorStorageKey())).toBe(ANSWERED);
  });

  it("carries the waiting sign over, folded row and all", () => {
    saveSentRecords([
      {
        rowId: "newest/next/gamma",
        anchorId: "newest/next/gamma",
        text: "Will this outlast the reload?",
        at: "2026-07-25T13:00:00Z",
      },
    ]);

    mount();

    const note = document.querySelector(
      '[data-row-id="newest/next/gamma"] p.pending',
    );
    expect(note?.textContent).toContain("Will this outlast the reload?");
    expect(note?.getAttribute("data-stalled")).toBe("false");
    expect(
      document.querySelector(
        '[data-row-id="newest/next/gamma"] p.working .working-text',
      )?.textContent,
    ).toBe("agent is working");
  });
});
