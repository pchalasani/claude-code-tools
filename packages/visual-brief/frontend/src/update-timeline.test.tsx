import { describe, expect, it, vi } from "vitest";

import { formatTimestamp, humanAge } from "./age";
import {
  mount,
  mountLive,
  paintedOpen,
  rowNode,
  useHarness,
} from "../test/harness";
import { itemOf, sampleBrief } from "../test/sample-brief";

useHarness();

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

function expectedLocalTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  const hour = date.getHours();
  const minute = String(date.getMinutes()).padStart(2, "0");
  const year = String(date.getFullYear() % 100).padStart(2, "0");
  const datePart = `${date.getDate()}-${MONTHS[date.getMonth()]}-${year}`;
  const clockHour = hour % 12 || 12;
  return `${datePart} ${clockHour}:${minute} ${hour < 12 ? "AM" : "PM"}`;
}

function expectMinutePrecision(value: string): void {
  expect(value).not.toMatch(/:\d{2}:\d{2}/);
  expect(value).not.toMatch(/\.\d{3}/);
}

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
  it("puts a local minute-precision timestamp beside the relative age", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-25T10:04:00Z"));
    try {
      const brief = sampleBrief();
      const stamp = "2026-07-25T09:59:42.987Z";
      const newest = brief.updates.find((update) => update.id === "newest");
      if (newest === undefined) {
        throw new Error("sample lost its newest update");
      }
      newest.timestamp = stamp;
      mount(brief);

      const head = document.querySelector(
        '[data-row-id="newest"] > .row-head',
      );
      const updateTime = head?.querySelector(".update-time");
      expect(updateTime?.getAttribute("datetime")).toBe(stamp);
      expect(updateTime?.textContent).toBe(expectedLocalTimestamp(stamp));
      expectMinutePrecision(updateTime?.textContent ?? "");
      expect(head?.querySelector(".update-age")?.textContent).toBe(
        "4min",
      );
      const mapTime = document.querySelector(".map-update-head time");
      expect(mapTime?.getAttribute("datetime")).toBe(stamp);
      expect(mapTime?.textContent).toBe(expectedLocalTimestamp(stamp));
    } finally {
      vi.useRealTimers();
    }
  });

  it("formats chat turn metadata in local time without losing dateTime", () => {
    const brief = sampleBrief();
    const stamp = "2026-08-02T19:22:51.456Z";
    const turn = itemOf(brief, "newest/changed/alpha")
      .questions?.[0]?.turns[0];
    if (turn === undefined) {
      throw new Error("sample lost its first chat turn");
    }
    turn.at = stamp;
    mount(brief);

    const time = rowNode("newest/changed/alpha#q-answered")
      ?.querySelector("time");
    expect(time?.getAttribute("datetime")).toBe(stamp);
    expect(time?.textContent).toBe(expectedLocalTimestamp(stamp));
    expectMinutePrecision(time?.textContent ?? "");
  });

  it("uses natural calendar words and degrades legacy prose clearly", () => {
    const now = Date.parse("2026-07-25T10:04:00Z");

    expect(humanAge("2026-07-24T10:04:00Z", now)).toBe("1d");
    expect(humanAge("2026-07-25T10:02:00Z", now)).toBe("2min");
    expect(humanAge("2026-07-25T17:04:00Z", now)).toBe("7h");
    expect(humanAge("2026-05-25T10:04:00Z", now)).toBe("2mos");
    expect(humanAge("Review round four", now)).toBe("age unavailable");
    expect(formatTimestamp("Review round four")).toBe("Review round four");
    expect(formatTimestamp("Review 4")).toBe("Review 4");
    expect(formatTimestamp("May 2025")).toBe("May 2025");
    expect(humanAge("May 2025", now)).toBe("age unavailable");
    expect(formatTimestamp("2026-07-25 10:04")).toMatch(
      /^25-Jul-26 10:04 [AP]M$/,
    );
  });
});
