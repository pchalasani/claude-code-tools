import { describe, expect, it } from "vitest";

import type { BriefDocument } from "./document";
import { saveAcceptedSignalWork, saveSentRecords } from "./session-store";
import { mount, mountLive, useHarness } from "../test/harness";
import { SAMPLE_SUGGESTIONS, sampleBrief } from "../test/sample-brief";

const ITEM = "newest/next/gamma";
const SIGNAL_ITEM = "newest/changed/alpha";
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

function signalBeforeTheLoad(): void {
  saveAcceptedSignalWork({
    [SIGNAL_ITEM]: {
      baseline: "newest",
      at: STAMP,
      signal: SAMPLE_SUGGESTIONS[0]?.message,
      text: SAMPLE_SUGGESTIONS[0]?.message,
    },
  });
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

});

describe("the sign after accepted feedback", () => {
  it("paints the existing working sign once on its row after reload", () => {
    signalBeforeTheLoad();

    mount();

    expect(signs().filter((row) => row === SIGNAL_ITEM)).toEqual([
      SIGNAL_ITEM,
    ]);
    expect(
      document.querySelector(
        `[data-row-id="${SIGNAL_ITEM}"] [data-suggestion="${
          SAMPLE_SUGGESTIONS[0]?.message
        }"]`,
      )?.getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("survives a live patch with the same final update", () => {
    signalBeforeTheLoad();
    const { publish } = mountLive();
    const patched = sampleBrief();
    patched.summary = "Changed without appending an update.";

    publish(patched);

    expect(signs().filter((row) => row === SIGNAL_ITEM)).toEqual([
      SIGNAL_ITEM,
    ]);
    expect(
      document.querySelector(
        `[data-row-id="${SIGNAL_ITEM}"] [data-suggestion="${
          SAMPLE_SUGGESTIONS[0]?.message
        }"]`,
      )?.getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("does not duplicate the item sign on its folded conversation", () => {
    signalBeforeTheLoad();
    const brief = sampleBrief();
    const item = brief.updates[1]?.lanes[0]?.items[0];
    if (item === undefined) {
      throw new Error("the sample document lost the suggested-reply item");
    }
    item.questions = [...(item.questions ?? []), {
      id: "q-suggestion-working",
      anchor: { kind: "element", path: SIGNAL_ITEM },
      turns: [{
        author: "human",
        text: SAMPLE_SUGGESTIONS[0]?.message ?? "",
        at: STAMP,
      }],
    }];

    mount(brief);

    expect(signs().filter((row) => row === SIGNAL_ITEM)).toHaveLength(1);
    expect(signs()).not.toContain(`${SIGNAL_ITEM}#q-suggestion-working`);
  });

  it("clears when a different final update is appended", async () => {
    signalBeforeTheLoad();
    const { publish } = mountLive();
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

    expect(signs()).not.toContain(SIGNAL_ITEM);
    expect(
      document.querySelector(
        `[data-row-id="${SIGNAL_ITEM}"] [data-suggestion="${
          SAMPLE_SUGGESTIONS[0]?.message
        }"]`,
      )?.getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("clears when an inline agent answer arrives without a new update", async () => {
    signalBeforeTheLoad();
    const { publish } = mountLive();
    const answered = sampleBrief();
    answered.updates[1]?.lanes[0]?.items[0]?.questions?.push({
      id: "q-suggestion",
      anchor: { kind: "element", path: SIGNAL_ITEM },
      turns: [
        {
          author: "human",
          text: SAMPLE_SUGGESTIONS[0]?.message ?? "",
          at: STAMP,
        },
        {
          author: "agent",
          text: "Here is the additional evidence.",
          at: "2026-07-28T08:00:00Z",
        },
      ],
    });

    publish(answered);
    await Promise.resolve();

    expect(signs()).not.toContain(SIGNAL_ITEM);
    expect(
      document.querySelector(
        `[data-row-id="${SIGNAL_ITEM}"] [data-suggestion="${
          SAMPLE_SUGGESTIONS[0]?.message
        }"]`,
      )?.getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("clears legacy work that cannot identify a later inline answer", () => {
    saveAcceptedSignalWork({ [SIGNAL_ITEM]: "newest" });

    mount();

    expect(signs()).not.toContain(SIGNAL_ITEM);
    expect(
      document.querySelectorAll(
        `[data-row-id="${SIGNAL_ITEM}"] .signal[aria-pressed="true"]`,
      ),
    ).toHaveLength(0);
  });
});
