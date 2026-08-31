import { createRoot, createSignal } from "solid-js";
import { describe, expect, it } from "vitest";

import type { ChosenMap, SeenMap } from "./human-state";
import {
  bornDefault,
  createOpenness,
  foldChoiceIds,
} from "./open";
import { outline, type Row } from "./outline";
import { itemOf, sampleBrief } from "../test/sample-brief";

const ANSWERED = "newest/changed/alpha#q-answered";
const AWAITING = "newest/changed/beta#q-open";

/** Find one row or fail with a useful message. */
function row(rows: Row[], id: string): Row {
  const found = rows.find((candidate) => candidate.id === id);
  if (found === undefined) {
    throw new Error(`missing test row ${id}`);
  }
  return found;
}

describe("row birth defaults", () => {
  it("opens exactly the row kinds and outstanding paths in the contract", () => {
    const rows = outline(sampleBrief());
    const seen = { "q-answered": row(rows, ANSWERED).answerState ?? "" };

    expect(bornDefault(row(rows, "newest"), rows, seen)).toBe(true);
    expect(bornDefault(row(rows, "older"), rows, seen)).toBe(false);
    expect(bornDefault(row(rows, "newest/changed"), rows, seen)).toBe(true);
    expect(bornDefault(row(rows, "newest/next"), rows, seen)).toBe(true);
    expect(bornDefault(row(rows, "newest/changed/alpha"), rows, seen)).toBe(
      false,
    );
    expect(bornDefault(row(rows, "newest/changed/beta"), rows, seen)).toBe(
      true,
    );
    expect(bornDefault(row(rows, ANSWERED), rows, seen)).toBe(false);
    expect(bornDefault(row(rows, AWAITING), rows, seen)).toBe(true);
  });

  it("does not give a hidden row a birth value before it is painted", () => {
    createRoot((dispose) => {
      const rows = outline(sampleBrief());
      const seen = { "q-answered": row(rows, ANSWERED).answerState ?? "" };
      const [chosen, setChosen] = createSignal<ChosenMap>({});
      const openness = createOpenness(() => rows, chosen, () => seen);

      openness.painted(rows);
      expect(openness.born(ANSWERED)).toBeUndefined();

      setChosen({ "newest/changed/alpha": true });
      openness.painted(rows);
      expect(openness.born(ANSWERED)).toBe(false);
      dispose();
    });
  });
});

describe("tab-lifetime openness", () => {
  it("collapses the former latest briefing when a new one arrives", () => {
    createRoot((dispose) => {
      const [rows, setRows] = createSignal(outline(sampleBrief()));
      const openness = createOpenness(rows, () => ({}), () => ({}));

      expect(openness.isOpen(row(rows(), "newest"))).toBe(true);
      expect(openness.isOpen(row(rows(), "older"))).toBe(false);

      const appended = sampleBrief();
      appended.updates.push({
        id: "after-work",
        timestamp: "2026-07-28T09:00:00Z",
        headline: "The requested work is ready",
        summary: "A new briefing arrived while the page stayed open.",
        lanes: [],
      });
      setRows(outline(appended));

      expect(openness.isOpen(row(rows(), "after-work"))).toBe(true);
      expect(openness.isOpen(row(rows(), "newest"))).toBe(false);
      expect(openness.isOpen(row(rows(), "older"))).toBe(false);
      dispose();
    });
  });

  it("keeps an archived briefing open only while it has active work", () => {
    createRoot((dispose) => {
      const [rows, setRows] = createSignal(outline(sampleBrief()));
      const [active, setActive] = createSignal(true);
      const openness = createOpenness(
        rows,
        () => ({}),
        () => ({}),
        (rowId) => rowId === "newest" && active(),
      );
      const appended = sampleBrief();
      appended.updates.push({
        id: "after-work",
        timestamp: "2026-07-28T09:00:00Z",
        headline: "The requested work is ready",
        summary: "A new briefing arrived while the page stayed open.",
        lanes: [],
      });
      setRows(outline(appended));

      expect(openness.isOpen(row(rows(), "newest"))).toBe(true);
      setActive(false);
      expect(openness.isOpen(row(rows(), "newest"))).toBe(false);
      dispose();
    });
  });

  it("keeps an awaiting thread open when its answer arrives", () => {
    createRoot((dispose) => {
      const [rows, setRows] = createSignal(outline(sampleBrief()));
      const openness = createOpenness(rows, () => ({}), () => ({}));

      expect(openness.isOpen(row(rows(), AWAITING))).toBe(true);

      const answered = sampleBrief();
      itemOf(answered, "newest/changed/beta").questions?.[0]?.turns.push({
        author: "agent",
        text: "All four are now covered.",
        at: "2026-07-25T12:01:00Z",
      });
      setRows(outline(answered));

      expect(row(rows(), AWAITING).awaiting).toBe(false);
      expect(openness.isOpen(row(rows(), AWAITING))).toBe(true);
      dispose();
    });
  });

  it("does not reopen an existing row when a publish changes its contents", () => {
    createRoot((dispose) => {
      const initial = outline(sampleBrief());
      const initialSeen: SeenMap = {
        "q-answered": row(initial, ANSWERED).answerState ?? "",
      };
      const [rows, setRows] = createSignal(initial);
      const openness = createOpenness(rows, () => ({}), () => initialSeen);
      const alpha = "newest/changed/alpha";

      expect(openness.isOpen(row(rows(), alpha))).toBe(false);

      const next = sampleBrief();
      itemOf(next, alpha).questions?.push({
        id: "q-new",
        anchor: { kind: "element", path: alpha },
        turns: [
          {
            author: "human",
            text: "What changed?",
            at: "2026-07-25T13:00:00Z",
          },
        ],
      });
      setRows(outline(next));

      expect(openness.isOutstanding(row(rows(), alpha), rows())).toBe(true);
      expect(openness.isOpen(row(rows(), alpha))).toBe(false);
      dispose();
    });
  });

  it("lets explicit human choices override immutable defaults", () => {
    createRoot((dispose) => {
      const rows = outline(sampleBrief());
      const [chosen, setChosen] = createSignal<ChosenMap>({});
      const openness = createOpenness(() => rows, chosen, () => ({}));
      const awaiting = row(rows, AWAITING);

      expect(openness.isOpen(awaiting)).toBe(true);
      setChosen({ [AWAITING]: false });
      expect(openness.isOpen(awaiting)).toBe(false);
      expect(openness.born(AWAITING)).toBe(true);
      expect(foldChoiceIds(rows)).toEqual(rows.map((one) => one.id));
      dispose();
    });
  });

  it("derives outstanding chats from awaiting and visited answer states", () => {
    createRoot((dispose) => {
      const rows = outline(sampleBrief());
      const [seen, setSeen] = createSignal<SeenMap>({});
      const openness = createOpenness(() => rows, () => ({}), seen);

      expect(openness.outstanding(rows).map((one) => one.id)).toEqual([
        ANSWERED,
        AWAITING,
      ]);

      setSeen({ "q-answered": row(rows, ANSWERED).answerState ?? "" });
      expect(openness.outstanding(rows).map((one) => one.id)).toEqual([
        AWAITING,
      ]);
      dispose();
    });
  });
});
