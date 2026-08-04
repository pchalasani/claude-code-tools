/**
 * What a publish is allowed to change on a page somebody is reading.
 *
 * The other half of the live-patch acceptance criteria: what a newly delivered
 * document does to something half-written, and what it is supposed to tell the
 * human has changed. Where ``live-patch.test.tsx`` asserts that a publish moves
 * nothing, this asserts that it does arrive — the waiting sign retires, the new
 * answer is marked, the counts and the title follow the document — and that it
 * arrives without replacing the page it lands on.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  click,
  composeAt,
  mountLive,
  rowNode,
  typeInto,
  useHarness,
} from "../test/harness";
import {
  itemOf,
  laneOf,
  SAMPLE_SUGGESTIONS,
  sampleBrief,
} from "../test/sample-brief";

const ALPHA = "newest/changed/alpha";
const BETA = "newest/changed/beta";
const OPEN_THREAD = `${BETA}#q-open`;
const NEXT_LANE = "newest/next";

useHarness();

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

describe("what a publish does to something half-written", () => {
  it("leaves an open chat box open, with the words still in it", () => {
    const { publish } = mountLive();
    composeAt(BETA);
    typeInto(".composer textarea", "Half a question about beta");

    const next = sampleBrief();
    itemOf(next, ALPHA).glance = "Something else entirely";
    publish(next);

    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("Half a question about beta");
    expect(
      document.querySelector(".composer")?.getAttribute("data-anchor-id"),
    ).toBe(BETA);
  });

  it("does not restore selection when the focused composer survives", () => {
    const { publish } = mountLive();
    composeAt(BETA);
    typeInto(".composer textarea", "Half a question about beta");
    const textarea = document.querySelector<HTMLTextAreaElement>(
      ".composer textarea",
    );
    if (textarea === null) {
      throw new Error("composer textarea was not rendered");
    }
    textarea.focus();
    textarea.setSelectionRange(5, 20, "backward");
    const focus = vi.spyOn(textarea, "focus");
    const setSelectionRange = vi.spyOn(textarea, "setSelectionRange");

    const next = sampleBrief();
    itemOf(next, ALPHA).glance = "Something else entirely";
    publish(next);

    expect(document.querySelector(".composer textarea")).toBe(textarea);
    expect(document.activeElement).toBe(textarea);
    expect(textarea.selectionStart).toBe(5);
    expect(textarea.selectionEnd).toBe(20);
    expect(textarea.selectionDirection).toBe("backward");
    expect(focus).not.toHaveBeenCalled();
    expect(setSelectionRange).not.toHaveBeenCalled();
  });

  it("closes the chat box when the row it was written at has gone", async () => {
    const { publish } = mountLive();
    composeAt(BETA);
    typeInto(".composer textarea", "A question about a row that is leaving");

    const next = sampleBrief();
    const lane = laneOf(next, "newest", "changed");
    lane.items = lane.items.filter((item) => item.id !== "beta");
    publish(next);
    await Promise.resolve();

    expect(document.querySelector(".composer")).toBeNull();
    publish(sampleBrief());
    expect(document.querySelector(".composer")).toBeNull();
  });

  it("restores a removed row's draft if its id returns", () => {
    const { publish } = mountLive();
    composeAt(BETA);
    typeInto(".composer textarea", "Words tied to the old beta row");
    composeAt(ALPHA);
    typeInto(".composer textarea", "Words tied to surviving alpha");

    const withoutBeta = sampleBrief();
    const lane = laneOf(withoutBeta, "newest", "changed");
    lane.items = lane.items.filter((item) => item.id !== "beta");
    publish(withoutBeta);
    publish(sampleBrief());

    composeAt(BETA);
    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("Words tied to the old beta row");
    composeAt(ALPHA);
    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("Words tied to surviving alpha");
  });
});

describe("what a publish tells the human has changed", () => {
  it("does not attach pending feedback to an update appended later", async () => {
    let answer: ((response: Response) => void) | undefined;
    globalThis.fetch = (() => new Promise<Response>((resolve) => {
      answer = resolve;
    })) as typeof globalThis.fetch;
    const { publish } = mountLive();
    const button = document.querySelector<HTMLButtonElement>(
      `[data-row-id="${ALPHA}"] [data-suggestion="${
        SAMPLE_SUGGESTIONS[0]?.message
      }"]`,
    );

    button?.click();
    expect(button?.getAttribute("aria-pressed")).toBe("true");

    const appended = sampleBrief();
    appended.updates.push({
      id: "after-feedback",
      timestamp: "2026-07-28T09:00:00Z",
      headline: "The agent completed more work",
      summary: "A new update is now final.",
      lanes: [],
    });
    publish(appended);
    await Promise.resolve();

    answer?.(new Response("{}", { status: 200 }));
    await vi.waitFor(() => {
      expect(button?.getAttribute("aria-pressed")).toBe("false");
    });
    expect(
      rowNode(ALPHA)?.querySelector(":scope > .row-body > .working"),
    ).toBeNull();
  });

  it("marks an answer that arrived as new, until it is visited", () => {
    const { publish } = mountLive();
    expect(rowNode(OPEN_THREAD)?.getAttribute("data-fresh")).toBe("false");

    const next = sampleBrief();
    const beta = itemOf(next, BETA);
    beta.questions?.[0]?.turns.push({
      author: "agent",
      text: "These four, with a case each.",
      at: "2026-07-25T13:00:00Z",
    });
    publish(next);

    expect(rowNode(OPEN_THREAD)?.getAttribute("data-fresh")).toBe("true");
    expect(
      rowNode(OPEN_THREAD)?.querySelector(".chip-new")?.textContent,
    ).toContain("New answer");
    // Only going to it clears the mark; nothing else does.
    click(OPEN_THREAD);
    expect(rowNode(OPEN_THREAD)?.getAttribute("data-fresh")).toBe("false");
  });

  it("follows the new document on the map and on the title", () => {
    const { publish } = mountLive();

    const next = sampleBrief();
    next.title = "A different brief";
    laneOf(next, "newest", "next").items.push({
      id: "delta",
      glance: "One more thing",
      explanation: "Added since.",
      trust: "unverified",
    });
    publish(next);

    expect(document.title).toBe("A different brief");
    expect(document.querySelector("[data-count]")).toBeNull();
    expect(document.querySelector(".brief-title")?.textContent).toBe(
      "A different brief",
    );
    expect(
      document.querySelector(`[data-map-lane="${NEXT_LANE}"] .map-label`)
        ?.textContent,
    ).toBe("What is next");
  });
});
