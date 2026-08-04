import { afterEach, describe, expect, it, vi } from "vitest";

import {
  composeAt,
  mount,
  paintedCursor,
  paintedOpen,
  press,
  rowNode,
  useHarness,
} from "../test/harness";
import {
  itemOf,
  SAMPLE_SUGGESTIONS,
  sampleBrief,
} from "../test/sample-brief";
import { signalShortcutHint } from "./compose-view";

const ITEM = "newest/changed/alpha";

useHarness();

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

function click(selector: string): void {
  const target = document.querySelector(selector);
  if (target === null) {
    throw new Error(`nothing to click at ${selector}`);
  }
  target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

describe("item header clicks", () => {
  it("toggles from the glance text and trust chip", () => {
    mount();
    const before = paintedOpen(ITEM);

    click(`[data-row-id="${ITEM}"] .glance .md-paragraph`);
    expect(paintedOpen(ITEM)).not.toBe(before);

    click(`[data-row-id="${ITEM}"] .chip-trust`);
    expect(paintedOpen(ITEM)).toBe(before);
  });

  it("keeps the fold button to exactly one toggle", () => {
    mount();
    const before = paintedOpen(ITEM);

    click(`[data-row-id="${ITEM}"] > .row-head .row-toggle`);
    expect(paintedOpen(ITEM)).not.toBe(before);
  });

  it("leaves a markdown link and chat control in charge of their clicks", () => {
    const brief = sampleBrief();
    const item = brief.updates[1]?.lanes[0]?.items[0];
    if (item === undefined) {
      throw new Error("the sample document lost the item this test changes");
    }
    item.glance = "Alpha has [details](https://example.com)";
    mount(brief);
    const before = paintedOpen(ITEM);

    const link = document.querySelector<HTMLAnchorElement>(
      `[data-row-id="${ITEM}"] .glance a`,
    );
    link?.addEventListener("click", (event) => event.preventDefault());
    click(`[data-row-id="${ITEM}"] .glance a`);
    expect(paintedOpen(ITEM)).toBe(before);

    composeAt(ITEM);
    expect(document.querySelector(".composer")).not.toBeNull();
    expect(paintedOpen(ITEM)).toBe(before);
  });
});

describe("item feedback controls", () => {
  it("starts with the first top-level row genuinely selected", () => {
    mount();

    expect(paintedCursor()).toBe("newest");
  });

  it("advertises the shared number keys in the page guide", () => {
    mount();

    expect(document.querySelector(".key-guide kbd")?.textContent).toBe("1–9");
    expect(document.querySelector(".key-guide")?.textContent).toContain(
      "Numbered choice",
    );
  });

  it("reuses 1–9 at each open level and paints the active number tags", () => {
    mount();
    press("C");

    expect(
      [...document.querySelectorAll(".row-shortcut")].map(
        (tag) => tag.textContent,
      ),
    ).toEqual(["1", "2"]);

    press("1");
    press(" ");
    expect(
      [...rowNode("newest")?.querySelectorAll(
        ":scope > .row-body > .row-lane > .row-head .row-shortcut",
      ) ?? []].map((tag) => tag.textContent),
    ).toEqual(["1", "2"]);

    press("2");
    expect(paintedCursor()).toBe("newest/next");
    press(" ");
    press("1");
    expect(paintedCursor()).toBe("newest/next/gamma");
  });

  it("puts the instruction above the chips with a dynamic number range", () => {
    mount();
    composeAt(ITEM);
    const signals = document.querySelector(
      `[data-row-id="${ITEM}"] > .row-body > .signals`,
    );
    const heading = signals?.querySelector(".signals-heading");
    const choices = signals?.querySelector(".signal-choices");

    expect(heading?.textContent).toContain(
      "Tell the agent(type 2–4 to select, c to chat)",
    );
    expect(choices?.querySelectorAll(".signal"))
      .toHaveLength(SAMPLE_SUGGESTIONS.length);
    expect(heading?.nextElementSibling).toBe(choices);
    expect(signalShortcutHint([3])).toBe("type 3 to select, c to chat");
    expect(signalShortcutHint([3, 4])).toBe(
      "type 3–4 to select, c to chat",
    );
  });

  it("keeps authored labels ordered with visible and accessible keys", () => {
    mount();
    composeAt(ITEM);

    const buttons = [...document.querySelectorAll<HTMLButtonElement>(
      `[data-row-id="${ITEM}"] > .row-body > .signals .signal`,
    )];
    expect(buttons.map((button) => button.querySelector("span")?.textContent))
      .toEqual(SAMPLE_SUGGESTIONS.map((suggestion) => suggestion.label));
    expect(buttons.map((button) => button.querySelector("kbd")?.textContent))
      .toEqual(["2", "3", "4"]);
    expect(buttons.map((button) => button.getAttribute("aria-keyshortcuts")))
      .toEqual(["2", "3", "4"]);
    expect(buttons.map((button) => button.getAttribute("aria-pressed")))
      .toEqual(["false", "false", "false"]);
  });

  it("selects without scrolling, paints immediately, and clears on failure",
    async () => {
      let answer: ((response: Response) => void) | undefined;
      const fetchSpy = vi.fn<typeof globalThis.fetch>(
        () => new Promise<Response>((resolve) => {
          answer = resolve;
        }),
      );
      globalThis.fetch = fetchSpy as typeof globalThis.fetch;
      mount();
      const button = document.querySelector<HTMLButtonElement>(
        `[data-row-id="${ITEM}"] [data-suggestion="${
          SAMPLE_SUGGESTIONS[1]?.message
        }"]`,
      );

      button?.click();

      expect(rowNode(ITEM)?.getAttribute("data-cursor")).toBe("true");
      expect(button?.getAttribute("aria-pressed")).toBe("true");
      expect(fetchSpy).toHaveBeenCalledOnce();
      expect(fetchSpy.mock.calls[0]?.[0]).toBe("signal");
      expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
        anchor_id: ITEM,
        label: SAMPLE_SUGGESTIONS[1]?.label,
        text: SAMPLE_SUGGESTIONS[1]?.message,
      });

      answer?.(new Response("{}", { status: 503 }));
      await vi.waitFor(() => {
        expect(button?.getAttribute("aria-pressed")).toBe("false");
      });
      expect(
        rowNode(ITEM)?.querySelector(":scope > .row-body > .working"),
      ).toBeNull();
  });

  it("shows no reply controls when the agent offers none", () => {
    const brief = sampleBrief();
    itemOf(brief, ITEM).suggestions = undefined;
    mount(brief);

    expect(
      document.querySelector(`[data-row-id="${ITEM}"] .signals`),
    ).toBeNull();
  });
});
