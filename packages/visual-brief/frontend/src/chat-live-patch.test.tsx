import { describe, expect, it } from "vitest";

import type { BriefDocument, Turn } from "./document";
import {
  mount,
  mountLive,
  paintedCursor,
  press,
  typeInto,
  useHarness,
} from "../test/harness";
import { sampleBrief } from "../test/sample-brief";

const ANSWERED = "newest/changed/alpha#q-answered";
const LIVE_THREAD = "patched-update/later/item#q-live";

useHarness();

/**
 * Add a historical thread whose opening author can change in a later publish.
 *
 * @param author - Author of the opening turn.
 * @returns A document carrying the thread.
 */
function withPatchedThread(author: Turn["author"]): BriefDocument {
  const brief = sampleBrief();
  brief.updates.unshift({
    id: "patched-update",
    timestamp: "2026-07-20T10:00:00Z",
    headline: "Historical update",
    summary: "A later patch added a human turn here.",
    lanes: [
      {
        id: "later",
        name: "Later",
        items: [
          {
            id: "item",
            glance: "A conversation arrived",
            explanation: "It must paint in the active view.",
            trust: "verified-by-me",
            questions: [
              {
                id: "q-live",
                anchor: {
                  kind: "element",
                  path: "patched-update/later/item",
                },
                turns: [
                  {
                    author,
                    text: "Can I see the conversation?",
                    at: "2026-07-29T15:00:00Z",
                  },
                  {
                    author: "agent",
                    text: "Yes, it arrived in the live document.",
                    at: "2026-07-29T15:01:00Z",
                  },
                ],
              },
            ],
          },
        ],
      },
    ],
  });
  return brief;
}

/** Read every row the page has painted, in order. */
function paintedRows(): string[] {
  return [...document.querySelectorAll("[data-row-id]")].map(
    (row) => row.getAttribute("data-row-id") ?? "",
  );
}

describe("live changes while My chats is open", () => {
  it("paints a conversation the live document starts collecting", () => {
    const { publish } = mountLive(withPatchedThread("agent"));
    press("m");
    expect(paintedRows()).not.toContain(LIVE_THREAD);

    publish(withPatchedThread("human"));

    expect(paintedRows()).toContain(LIVE_THREAD);
  });

  it("keeps a search while chats are showing and reapplies it on exit", () => {
    mount();
    press("/");
    typeInto("#brief-search", "parser");

    document.querySelector<HTMLButtonElement>(".meta-chats")?.click();

    expect(paintedRows()).toContain(ANSWERED);
    expect(
      document.querySelector<HTMLInputElement>("#brief-search")?.value,
    ).toBe("parser");
    press("j");
    expect(paintedCursor()).toBe("newest/changed/beta#q-open");

    document.querySelector<HTMLButtonElement>(".meta-chats")?.click();

    expect(
      document.querySelector<HTMLInputElement>("#brief-search")?.value,
    ).toBe("parser");
    expect(paintedRows()).toContain("newest/changed/alpha");
    expect(paintedRows()).not.toContain("newest/changed/beta#q-open");
    expect(paintedCursor()).toBe("newest/changed/alpha");
  });
});
