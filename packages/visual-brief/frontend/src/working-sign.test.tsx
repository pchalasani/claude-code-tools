import { describe, expect, it } from "vitest";

import type { BriefDocument } from "./document";
import { forgetLoadClassification } from "./pending";
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
const ASKED = "Does the sign survive the reload?";

useHarness();

/**
 * Say that this tab sent a message and has not yet seen it arrive.
 *
 * This is the whole memory a reload carries: it is written the instant the
 * daemon accepts a message, and it is what the very next page load reads
 * before it paints anything.
 *
 * @param loads - How many page loads it has already survived.
 */
function sentBeforeTheReload(loads = 0): void {
  forgetLoadClassification();
  saveSentRecords([
    { rowId: ITEM, anchorId: ITEM, text: ASKED, at: STAMP, loads },
  ]);
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

/**
 * Read the rows wearing an awaiting chip.
 *
 * @returns The row id under each chip.
 */
function chips(): string[] {
  return [...document.querySelectorAll(".row-head .chip-awaiting")].map(
    (chip) => chip.closest("[data-row-id]")?.getAttribute("data-row-id") ?? "",
  );
}

describe("the sign across the reload a send causes", () => {
  it("is up at the first paint of a page that has not caught up yet", () => {
    sentBeforeTheReload();

    mount();

    // No poll has run: this is what the page paints before anything else
    // happens to it.
    expect(signs()).toContain(ITEM);
    expect(
      document.querySelector(`[data-row-id="${ITEM}"] p.pending`)?.textContent,
    ).toContain(ASKED);
  });

  it("is up at the first paint of the page that folded the question in", () => {
    sentBeforeTheReload();

    mount(foldedIn());

    expect(signs()).toEqual([ALREADY_OPEN, FOLDED]);
    // The note has been retired — the words are on the page now — and the
    // sign did not go with it.
    expect(document.querySelectorAll("p.pending")).toHaveLength(0);
  });

  it("stands beside the awaiting chips rather than being replaced by them", () => {
    sentBeforeTheReload();

    mount(foldedIn());

    expect(chips()).toContain(FOLDED);
    expect(chips()).toContain(ITEM);
    expect(chips()).toContain("newest/next");
    expect(chips()).toContain("newest");
    expect(signs()).toEqual([ALREADY_OPEN, FOLDED]);
  });

  it("says it once for one question, not once per source", () => {
    sentBeforeTheReload();

    mount(foldedIn());

    expect(signs().filter((row) => row === FOLDED)).toHaveLength(1);
  });

  it("survives a submission that gave up on ever being found", () => {
    // A message that never appears stops promising progress — but that is a
    // statement about the message. The conversation the document says is
    // unanswered is a different claim, and it used to be silenced by the
    // first one sitting on the same row.
    saveSentRecords([
      {
        rowId: FOLDED,
        anchorId: ITEM,
        text: "A follow-up nobody ever saw",
        at: STAMP,
        loads: 1,
      },
    ]);
    forgetLoadClassification();
    mount(foldedIn());
    for (let poll = 0; poll < 3; poll += 1) {
      announcePoll("same");
    }

    expect(
      document.querySelector(`[data-row-id="${FOLDED}"] p.pending`)
        ?.getAttribute("data-stalled"),
    ).toBe("true");
    expect(
      document.querySelector(`[data-row-id="${FOLDED}"] p.stalled`)
        ?.textContent,
    ).toBe("submitted — refresh if this persists");
    expect(signs()).toEqual([ALREADY_OPEN, FOLDED]);
  });

  it("keeps the words legible rather than painting them through anything", () => {
    sentBeforeTheReload();

    mount();

    const sign = document.querySelector(`[data-row-id="${ITEM}"] p.working`);
    expect(sign?.querySelector(".working-text")?.textContent).toBe(
      "agent is working",
    );
    // The part that moves is a mark of its own, so the words never depend on
    // where an animation happens to be when the page repaints.
    expect(sign?.querySelector(".working-mark")).not.toBeNull();
  });
});
