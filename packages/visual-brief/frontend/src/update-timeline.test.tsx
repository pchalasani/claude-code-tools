import { describe, expect, it, vi } from "vitest";

import { humanAge } from "./age";
import { mount, mountLive, paintedOpen, useHarness } from "../test/harness";
import { itemOf, sampleBrief } from "../test/sample-brief";

useHarness();

/**
 * Read painted update ids from top to bottom.
 *
 * @returns Update ids in visible order.
 */
function paintedUpdates(): string[] {
  return [...document.querySelectorAll('[data-row-kind="update"]')].map(
    (row) => row.getAttribute("data-row-id") ?? "",
  );
}

describe("the append-only update timeline", () => {
  it("treats now as an ordinary id and folds every older update", () => {
    const brief = sampleBrief();
    brief.updates.splice(1, 0, {
      id: "now",
      timestamp: "2026-07-24T18:00:00Z",
      headline: "An update formerly called now",
      summary: "This is an event, not pinned state.",
      lanes: [],
    });
    itemOf(brief, "older/history/one").questions = [
      {
        id: "older-wait",
        anchor: { kind: "element", path: "older/history/one" },
        turns: [
          {
            author: "human",
            text: "An older question remains visible on its containing rail.",
            at: "2026-07-24T19:00:00Z",
          },
        ],
      },
    ];

    mount(brief);

    expect(paintedUpdates()).toEqual(["newest", "now", "older"]);
    expect(paintedOpen("newest")).toBe("true");
    expect(paintedOpen("now")).toBe("false");
    expect(paintedOpen("older")).toBe("false");
    expect(document.querySelector(".earlier-heading")).toBeNull();
    expect(document.querySelector(".now-mark")).toBeNull();
    expect(document.querySelector(".history-heading")?.textContent).toContain(
      "Dated changes",
    );
    expect(
      document.querySelector(".history-entries")?.querySelectorAll(
        ':scope > [data-row-kind="update"]',
      ),
    ).toHaveLength(3);
  });

  it("opens an appended update delivered to the live page", () => {
    const { publish } = mountLive();
    const next = sampleBrief();
    next.updates.push({
      id: "just-published",
      timestamp: "2026-07-25T14:00:00Z",
      headline: "Published while this page was open",
      summary: "The new update arrived at the top.",
      lanes: [],
    });

    publish(next);

    expect(paintedUpdates()[0]).toBe("just-published");
    expect(paintedOpen("just-published")).toBe("true");
  });
});

describe("visible update ages", () => {
  it("puts a readable age beside the original timestamp", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-25T10:04:00Z"));
    try {
      mount();

      const head = document.querySelector(
        '[data-row-id="newest"] > .row-head',
      );
      expect(head?.querySelector(".update-time")?.textContent).toBe(
        "2026-07-25T10:00:00Z",
      );
      expect(head?.querySelector(".update-age")?.textContent).toBe(
        "4 minutes ago",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("uses natural calendar words and degrades legacy prose clearly", () => {
    const now = Date.parse("2026-07-25T10:04:00Z");

    expect(humanAge("2026-07-24T10:04:00Z", now)).toBe("yesterday");
    expect(humanAge("Review round four", now)).toBe("age unavailable");
  });
});
