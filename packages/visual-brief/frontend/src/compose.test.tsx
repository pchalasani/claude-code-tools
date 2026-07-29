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

function writeDraft(text: string): void {
  const box = document.querySelector<HTMLTextAreaElement>(".composer textarea");
  if (box === null) {
    throw new Error("no chat box");
  }
  box.value = text;
  box.dispatchEvent(new Event("input", { bubbles: true }));
}

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

  it("restores each row's draft after opening another chat", () => {
    mount();
    press("c");
    writeDraft("Alpha draft");
    document
      .querySelector<HTMLElement>(
        '[data-row-id="newest/changed/beta"] .chat-button',
      )
      ?.click();
    writeDraft("Beta draft");

    document
      .querySelector<HTMLElement>(
        '[data-row-id="newest/changed/alpha"] .chat-button',
      )
      ?.click();
    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("Alpha draft");

    document
      .querySelector<HTMLElement>(
        '[data-row-id="newest/changed/beta"] .chat-button',
      )
      ?.click();
    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("Beta draft");
  });

  it("keeps a draft through folding and collapsing the page", () => {
    mount();
    press("c");
    writeDraft("Keep this through every fold");
    click("newest/changed/alpha");
    expect(document.querySelector(".composer")).toBeNull();

    press("C");
    click("newest/changed");
    click("newest/changed/alpha");
    document
      .querySelector<HTMLElement>(
        '[data-row-id="newest/changed/alpha"] .chat-button',
      )
      ?.click();

    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("Keep this through every fold");
  });

  it("requires a second Escape before discarding a non-empty draft", () => {
    mount();
    press("c");
    writeDraft("These words belong to the human");

    press("Escape");
    expect(document.querySelector(".composer")).not.toBeNull();
    expect(document.querySelector(".composer .status")?.textContent).toContain(
      "again",
    );

    press("Escape");
    expect(document.querySelector(".composer")).toBeNull();
    press("c");
    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("");
  });

  it("says the agent is working under every unanswered conversation", () => {
    mount();

    const signs = [...document.querySelectorAll("p.working")];
    const rows = signs.map((sign) =>
      sign.closest("[data-row-id]")?.getAttribute("data-row-id"),
    );

    expect(rows).toEqual(["newest/changed/beta#q-open"]);
    expect(signs[0]?.querySelector(".working-text")?.textContent).toBe(
      "agent is working",
    );
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

  it("brings back the whole way to a note that landed on a folded page", async () => {
    // A send takes seconds and the page stays live throughout, so the human
    // can fold everything while their words are still in the air. Opening
    // only the row they wrote in would leave the note they are waiting for
    // inside a container nobody can see through.
    const realFetch = globalThis.fetch;
    let accept = (): void => undefined;
    globalThis.fetch = (() =>
      new Promise<Response>((resolve) => {
        accept = () =>
          resolve({
            ok: true,
            text: async () => '{"status": "queued", "timestamp": "21:00"}',
          } as unknown as Response);
      })) as unknown as typeof globalThis.fetch;
    try {
      mount();
      press("c");
      const box = document.querySelector<HTMLTextAreaElement>(
        ".composer textarea",
      );
      if (box === null) {
        throw new Error("no chat box");
      }
      box.value = "Does this survive a fold?";
      box.dispatchEvent(new Event("input", { bubbles: true }));
      document
        .querySelector(".composer")
        ?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));

      press("C");
      expect(paintedOpen("newest/changed")).toBe("false");

      accept();
      await new Promise((settled) => setTimeout(settled, 0));

      expect(paintedOpen("newest/changed")).toBe("true");
      expect(paintedOpen("newest/changed/alpha")).toBe("true");
      expect(
        document.querySelector('[data-row-id="newest/changed/alpha"] p.pending')
          ?.textContent,
      ).toContain("Does this survive a fold?");
    } finally {
      globalThis.fetch = realFetch;
    }
  });
});
