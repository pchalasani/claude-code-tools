import { describe, expect, it } from "vitest";

import { conversationState } from "./freshness";
import { seenStorageKey } from "./session-store";
import {
  click,
  mount,
  paintedCursor,
  paintedOpen,
  press,
  unmount,
  useHarness,
} from "../test/harness";

const ANSWERED = "newest/changed/alpha#q-answered";
const ITEM = "newest/changed/alpha";

useHarness();

/**
 * Say what the human saw the last time they looked at this page.
 *
 * @param seen - Each conversation's state as of that look.
 */
function lastLook(seen: Record<string, string>): void {
  window.sessionStorage.setItem(seenStorageKey(), JSON.stringify(seen));
}

/**
 * Read whether a row is painted as newly answered.
 *
 * @param id - Row id to look at.
 * @returns The row's painted freshness.
 */
function paintedFresh(id: string): string | null {
  return (
    document
      .querySelector(`[data-row-id="${id}"]`)
      ?.getAttribute("data-fresh") ?? null
  );
}

describe("an answer that arrived since the last look", () => {
  it("marks nothing on a page the human has never looked at", () => {
    mount();

    // Nothing draws the item open, so its conversation is not even rendered.
    expect(paintedOpen(ITEM)).toBe("false");
    expect(document.querySelector(`[data-row-id="${ANSWERED}"]`)).toBeNull();
  });

  it("marks nothing when the page has not moved since the last look", () => {
    lastLook({ [ANSWERED]: conversationState(2, false) });

    mount();

    expect(paintedOpen(ITEM)).toBe("false");
    expect(document.querySelector(`[data-row-id="${ANSWERED}"]`)).toBeNull();
  });

  it("opens itself and everything holding it, and says it is new", () => {
    lastLook({ [ANSWERED]: conversationState(1, true) });

    mount();

    expect(paintedFresh(ANSWERED)).toBe("true");
    expect(paintedOpen(ANSWERED)).toBe("true");
    expect(paintedOpen(ITEM)).toBe("true");
    expect(paintedOpen("newest/changed")).toBe("true");
    expect(
      document.querySelector(`[data-row-id="${ANSWERED}"] .chip-new`)
        ?.textContent,
    ).toContain("New answer");
  });

  it("stays marked across another reload until it is visited", () => {
    lastLook({ [ANSWERED]: conversationState(1, true) });
    mount();
    expect(paintedFresh(ANSWERED)).toBe("true");

    unmount();
    mount();

    expect(paintedFresh(ANSWERED)).toBe("true");
  });

  it("clears on a visit, and stays clear through the next reload", () => {
    lastLook({ [ANSWERED]: conversationState(1, true) });
    mount();

    // One visit: the cursor lands on the row and the row is toggled.
    click(ANSWERED);

    expect(paintedCursor()).toBe(ANSWERED);
    expect(paintedFresh(ANSWERED)).toBe("false");
    expect(document.querySelector(`[data-row-id="${ANSWERED}"] .chip-new`))
      .toBeNull();

    unmount();
    mount();

    expect(paintedFresh(ANSWERED)).toBe("false");
  });

  it("is not cleared by looking at something else on the page", () => {
    lastLook({ [ANSWERED]: conversationState(1, true) });
    mount();

    press("J");

    expect(paintedCursor()).not.toBe(ANSWERED);
    expect(paintedFresh(ANSWERED)).toBe("true");
  });
});
