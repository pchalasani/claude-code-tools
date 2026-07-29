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

import { describe, expect, it } from "vitest";

import { saveSentRecords } from "./session-store";
import {
  click,
  composeAt,
  mountLive,
  rowNode,
  typeInto,
  useHarness,
} from "../test/harness";
import { itemOf, laneOf, sampleBrief } from "../test/sample-brief";

const ALPHA = "newest/changed/alpha";
const BETA = "newest/changed/beta";
const OPEN_THREAD = `${BETA}#q-open`;
const NEXT_LANE = "newest/next";
const GAMMA = "newest/next/gamma";

useHarness();

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

  it("closes the chat box when the row it was written at has gone", () => {
    const { publish } = mountLive();
    composeAt(BETA);
    typeInto(".composer textarea", "A question about a row that is leaving");

    const next = sampleBrief();
    const lane = laneOf(next, "newest", "changed");
    lane.items = lane.items.filter((item) => item.id !== "beta");
    publish(next);

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
  it("retires a waiting sign the moment its words arrive", () => {
    saveSentRecords([
      {
        rowId: GAMMA,
        anchorId: GAMMA,
        text: "Did this land?",
        at: "2026-07-25T15:00:00Z",
      },
    ]);
    const { container, publish } = mountLive();
    const shell = container.firstElementChild;
    expect(
      document.querySelectorAll('[data-pending="true"]'),
    ).toHaveLength(1);

    const next = sampleBrief();
    itemOf(next, GAMMA).questions = [
      {
        id: "q-folded",
        anchor: { kind: "element", path: GAMMA },
        turns: [
          {
            author: "human",
            text: "Did this land?",
            at: "2026-07-25T15:00:00Z",
          },
        ],
      },
    ];
    publish(next);

    expect(
      document.querySelectorAll('[data-pending="true"]'),
    ).toHaveLength(0);
    // The conversation the daemon folded it into now carries the sign, and
    // the page it is on is the page it was on: nothing was replaced.
    expect(
      document.querySelectorAll(
        `[data-row-id="${GAMMA}#q-folded"] > .row-body > p.working`,
      ),
    ).toHaveLength(1);
    expect(container.firstElementChild).toBe(shell);
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

  it("follows the new document on counts, on the map and on the title", () => {
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
    // Four items in the sample document, and the one that just arrived.
    expect(
      document.querySelector('[data-count="items"] b')?.textContent,
    ).toBe("5");
    expect(document.querySelector(".brief-title")?.textContent).toBe(
      "A different brief",
    );
    expect(
      document.querySelector(`[data-map-lane="${NEXT_LANE}"] .map-label`)
        ?.textContent,
    ).toBe("What is next");
  });
});
