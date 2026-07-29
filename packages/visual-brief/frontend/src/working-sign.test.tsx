import { describe, expect, it } from "vitest";

import type { BriefDocument } from "./document";
import { STALL_POLLS } from "./pending";
import { announcePoll } from "./reload";
import { saveSentRecords } from "./session-store";
import { mount, useHarness } from "../test/harness";
import { sampleBrief } from "../test/sample-brief";

const ITEM = "newest/next/gamma";
const FOLDED = `${ITEM}#q-pending-9f2`;
// The sample page already carries one unanswered conversation of its own,
// which wears its own sign throughout. Its presence in these readings is the
// point: one question's sign is not the page's only sign.
const ALREADY_OPEN = "newest/changed/beta#q-open";
const STAMP = "2026-07-27T09:00:00.000Z";
const ASKED = "Does the sign survive a page load?";

useHarness();

/**
 * Say that this tab sent a message and has not yet seen it arrive.
 *
 * This is the whole memory a page load carries: it is written the instant the
 * daemon accepts a message, and it is what the very next page load reads
 * before it paints anything.
 */
function sentBeforeTheLoad(): void {
  saveSentRecords([{ rowId: ITEM, anchorId: ITEM, text: ASKED, at: STAMP }]);
}

/**
 * Build the page the daemon serves once it has folded the question in.
 *
 * @returns The document, with the human's question now a conversation of its
 *     own that nobody has answered yet.
 */
function foldedIn(): BriefDocument {
  const brief = sampleBrief();
  const item = brief.updates
    .find((update) => update.id === "newest")
    ?.lanes.find((lane) => lane.id === "next")
    ?.items.find((one) => one.id === "gamma");
  if (item === undefined) {
    throw new Error("the sample document lost the item this test writes at");
  }
  item.questions = [
    {
      id: "q-pending-9f2",
      anchor: { kind: "element", path: ITEM },
      turns: [{ author: "human", text: ASKED, at: STAMP }],
    },
  ];
  return brief;
}

/**
 * Read every waiting sign the page is painting, with the row wearing it.
 *
 * @returns The row id under each sign, in painted order.
 */
function signs(): string[] {
  return [...document.querySelectorAll("p.working")].map(
    (sign) => sign.closest("[data-row-id]")?.getAttribute("data-row-id") ?? "",
  );
}

/** Read the waiting rail strength painted on every marked row. */
function rails(): Record<string, string> {
  return Object.fromEntries(
    [...document.querySelectorAll<HTMLElement>("[data-waiting]")].map(
      (row) => [row.dataset.rowId ?? "", row.dataset.waiting ?? ""],
    ),
  );
}

describe("the sign across a page load that followed a send", () => {
  it("is up at the first paint of a page that has not caught up yet", () => {
    sentBeforeTheLoad();

    mount();

    // No poll has run: this is what the page paints before anything else
    // happens to it.
    expect(signs()).toContain(ITEM);
    expect(
      document.querySelector(
        `[data-row-id="${ITEM}"] [data-pending="true"]`,
      )?.textContent,
    ).toContain(ASKED);
  });

  it("is up at the first paint of the page that folded the question in", () => {
    sentBeforeTheLoad();

    mount(foldedIn());

    expect(signs()).toEqual([ALREADY_OPEN, FOLDED]);
    // The note has been retired — the words are on the page now — and the
    // sign did not go with it.
    expect(
      document.querySelectorAll('[data-pending="true"]'),
    ).toHaveLength(0);
  });

  it("keeps one direct rail and quiet rails on its containers", () => {
    sentBeforeTheLoad();

    mount(foldedIn());

    expect(rails()[FOLDED]).toBe("direct");
    expect(rails()[ITEM]).toBe("contained");
    expect(rails()["newest/next"]).toBe("contained");
    expect(rails().newest).toBe("contained");
    expect(signs()).toEqual([ALREADY_OPEN, FOLDED]);
  });

  it("says it once for one question, not once per source", () => {
    sentBeforeTheLoad();

    mount(foldedIn());

    expect(signs().filter((row) => row === FOLDED)).toHaveLength(1);
  });

  it("stays continuous when a pending submission reaches its diagnostic", () => {
    // The third poll used to remove the sign while leaving the pending note in
    // place. The sign returned only when a later publish made the message an
    // awaiting conversation: the exact flap reported from the live page.
    sentBeforeTheLoad();
    mount();
    for (let poll = 0; poll < STALL_POLLS; poll += 1) {
      announcePoll("same");
    }

    expect(
      document.querySelector(
        `[data-row-id="${ITEM}"] [data-pending="true"]`,
      )
        ?.getAttribute("data-stalled"),
    ).toBe("true");
    expect(
      document.querySelector(`[data-row-id="${ITEM}"] p.stalled`)
        ?.textContent,
    ).toBe("submitted — refresh if this persists");
    expect(signs()).toEqual([ALREADY_OPEN, ITEM]);
  });

  it("keeps the words legible rather than painting them through anything", () => {
    sentBeforeTheLoad();

    mount();

    const sign = document.querySelector(`[data-row-id="${ITEM}"] p.working`);
    expect(sign?.querySelector(".working-text")?.textContent).toBe(
      "agent is working",
    );
    // The part that moves is a mark of its own, so the words never depend on
    // where an animation happens to be when the page repaints.
    expect(sign?.querySelector(".working-mark")).not.toBeNull();
  });

  it("holds the note still while the polls go by", () => {
    // The note is painted inside a ``For``, which pairs elements with values
    // by identity, so a note rebuilt on every poll is torn out of the page and
    // an identical one put back — fading itself in again every few seconds,
    // for as long as the message goes unanswered. The element is what is
    // asserted, because a blink is a new element rather than new words.
    sentBeforeTheLoad();
    mount();
    const held = document.querySelector(
      `[data-row-id="${ITEM}"] [data-pending="true"]`,
    );
    expect(held).not.toBeNull();

    for (let poll = 0; poll < STALL_POLLS; poll += 1) {
      announcePoll("same");
    }

    expect(
      document.querySelector(
        `[data-row-id="${ITEM}"] [data-pending="true"]`,
      ),
    ).toBe(held);
    // The same element throughout, and still keeping up with how long the
    // human has been waiting.
    expect(held?.getAttribute("data-stalled")).toBe("true");
  });
});
