import { describe, expect, it } from "vitest";

import {
  click,
  deferTransitions,
  flushTransitions,
  mount,
  paintedCursor,
  paintedOpen,
  press,
  useHarness,
} from "../test/harness";

useHarness();

describe("writing where the cursor is", () => {
  it("points the composer at the row the second key saw", () => {
    mount();
    deferTransitions();

    press("j");
    press("c");
    flushTransitions();

    const form = document.querySelector(".composer");

    expect(form?.getAttribute("data-anchor-id")).toBe("newest/changed/beta");
    expect(paintedCursor()).toBe("newest/changed/beta");
  });

  it("focuses the composer opened on a folded update", async () => {
    mount();
    deferTransitions();
    click("older");
    click("older");
    flushTransitions();
    expect(paintedCursor()).toBe("older");
    expect(paintedOpen("older")).toBe("false");

    press("c");
    await Promise.resolve();

    const box = document.querySelector<HTMLTextAreaElement>(
      ".composer textarea",
    );
    expect(box).not.toBeNull();
    expect(document.activeElement).toBe(box);

    flushTransitions();
    const before = paintedCursor();
    box?.dispatchEvent(
      new KeyboardEvent("keydown", { key: "j", bubbles: true }),
    );

    expect(paintedCursor()).toBe(before);
    expect(document.querySelector(".composer")).not.toBeNull();
  });

  it("folds back the row it expanded when the composer is closed again", () => {
    mount();
    click("older");
    click("older");
    expect(paintedOpen("older")).toBe("false");

    press("c");
    expect(paintedOpen("older/history")).toBe("true");
    expect(document.querySelector(".composer")).not.toBeNull();

    press("c");

    expect(document.querySelector(".composer")).toBeNull();
    expect(paintedOpen("older/history")).toBe("false");
    expect(paintedOpen("older")).toBe("true");
    expect(paintedCursor()).toBe("older/history");
  });

  it("folds back the row it borrowed when Escape dismisses the box", () => {
    mount();
    click("older");
    click("older");
    expect(paintedOpen("older")).toBe("false");

    press("c");
    expect(paintedOpen("older/history")).toBe("true");

    press("Escape");

    expect(document.querySelector(".composer")).toBeNull();
    expect(paintedOpen("older/history")).toBe("false");
    expect(paintedOpen("older")).toBe("true");
  });

  it("lets go of the composer when its own row is folded away", () => {
    mount();
    click("newest/next");
    press("c");
    expect(document.querySelector(".composer")).not.toBeNull();

    click("newest/next");

    expect(document.querySelector(".composer")).toBeNull();
    expect(
      document
        .querySelector('[data-row-id="newest/next"] .chat-button')
        ?.getAttribute("aria-expanded"),
    ).toBe("false");

    press("c");

    expect(document.querySelector(".composer")).not.toBeNull();
  });

  it("says the agent is working under every unanswered conversation", () => {
    mount();

    const signs = [...document.querySelectorAll("p.working")];
    const rows = signs.map((sign) =>
      sign.closest("[data-row-id]")?.getAttribute("data-row-id"),
    );

    expect(rows).toEqual(["newest/changed/beta#q-open"]);
    expect(signs[0]?.textContent).toBe("agent is working");
  });

  it("leaves a row the human opened alone when the composer closes", () => {
    mount();
    click("newest/next");
    expect(paintedOpen("newest/next")).toBe("true");

    press("c");
    press("c");

    expect(document.querySelector(".composer")).toBeNull();
    expect(paintedOpen("newest/next")).toBe("true");
  });
});
