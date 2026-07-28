import { describe, expect, it } from "vitest";

import type { BriefDocument } from "./document";
import { evidenceRowId, noteRowId } from "./evidence";
import { outline } from "./outline";
import {
  click,
  mount,
  paintedCursor,
  paintedOpen,
  press,
  useHarness,
} from "../test/harness";
import { sampleBrief } from "../test/sample-brief";

const ITEM = "newest/changed/alpha";
const EVIDENCE = evidenceRowId(ITEM);
const NOTE = noteRowId(EVIDENCE, 1);
const DEEPER = noteRowId(NOTE, 0);

useHarness();

/**
 * Build a page whose first item carries evidence that nests.
 *
 * @returns The document.
 */
function withEvidence(): BriefDocument {
  const brief = sampleBrief();
  const item = brief.updates
    .find((update) => update.id === "newest")
    ?.lanes.find((lane) => lane.id === "changed")
    ?.items.find((one) => one.id === "alpha");
  if (item === undefined) {
    throw new Error("the sample document lost the item this test writes at");
  }
  item.forensics = [
    "exit status 0",
    {
      title: "The reference run",
      body: "Ran against the reference parser with `--strict`.",
      children: [
        {
          title: "The one disagreement",
          body: "A nested table cell.",
        },
      ],
    },
  ];
  return brief;
}

/**
 * Read the rows the page has painted, in the order it painted them.
 *
 * @returns The painted row ids.
 */
function paintedRows(): string[] {
  return [...document.querySelectorAll("[data-row-id]")].map(
    (row) => row.getAttribute("data-row-id") ?? "",
  );
}

/**
 * Jump to one row by typing the label it is wearing.
 *
 * @param id - Row to jump to.
 */
function jumpTo(id: string): void {
  press("f");
  const label = document
    .querySelector(`[data-row-id="${id}"] .hint`)
    ?.getAttribute("data-hint");
  expect(label, `no jump label on ${id}`).toBeTruthy();
  for (const key of label ?? "") {
    press(key);
  }
}

describe("evidence as rows the keyboard can reach", () => {
  it("is a row of the outline, under the item and above its chats", () => {
    const brief = withEvidence();
    mount(brief);

    press("E");

    expect(paintedRows()).toEqual(outline(brief).map((row) => row.id));
    const painted = paintedRows();
    expect(painted.indexOf(ITEM)).toBeLessThan(painted.indexOf(EVIDENCE));
    expect(painted.indexOf(EVIDENCE)).toBeLessThan(
      painted.indexOf(`${ITEM}#q-answered`),
    );
  });

  it("starts folded, and opens on the fold key like anything else", () => {
    mount(withEvidence());
    click(ITEM);
    expect(paintedOpen(EVIDENCE)).toBe("false");

    jumpTo(EVIDENCE);
    expect(paintedCursor()).toBe(EVIDENCE);
    press(" ");

    expect(paintedOpen(EVIDENCE)).toBe("true");
    expect(
      document.querySelector(`[data-row-id="${EVIDENCE}"] pre.evidence`)
        ?.textContent,
    ).toBe("exit status 0");
  });

  it("reaches a note nested inside a note, one fold at a time", () => {
    mount(withEvidence());
    click(ITEM);
    jumpTo(EVIDENCE);
    press(" ");

    jumpTo(NOTE);
    expect(paintedCursor()).toBe(NOTE);
    press(" ");

    expect(paintedOpen(NOTE)).toBe("true");
    expect(paintedRows()).toContain(DEEPER);
    jumpTo(DEEPER);
    expect(paintedCursor()).toBe(DEEPER);
  });

  it("opens with everything on E and folds away with everything on C", () => {
    mount(withEvidence());

    press("E");
    expect(paintedOpen(EVIDENCE)).toBe("true");
    expect(paintedOpen(NOTE)).toBe("true");
    expect(paintedRows()).toContain(DEEPER);

    press("C");

    expect(paintedRows()).not.toContain(EVIDENCE);
  });

  it("renders a note's body as prose, not as characters", () => {
    mount(withEvidence());
    press("E");

    expect(
      document.querySelector(`[data-row-id="${NOTE}"] .note-body code`)
        ?.textContent,
    ).toBe("--strict");
  });

  it("leaves the masthead counting what it always counted", () => {
    mount(withEvidence());

    const counts = [...document.querySelectorAll(".meta-count")].map(
      (count) => count.textContent,
    );

    expect(counts).toEqual([
      "2 updates",
      "3 lanes",
      "4 items",
      "1 unanswered",
      "2 my chats",
    ]);
  });

  it("hands a chat written at evidence to the item it is evidence for", () => {
    mount(withEvidence());
    click(ITEM);
    jumpTo(EVIDENCE);
    expect(paintedCursor()).toBe(EVIDENCE);

    press("c");

    const box = document.querySelector(".composer");
    expect(box?.getAttribute("data-anchor-id")).toBe(ITEM);
    expect(
      document.querySelector(`[data-row-id="${ITEM}"] > .row-body .composer`),
    ).not.toBeNull();
  });

  it("keeps evidence with its item through a search", () => {
    mount(withEvidence());
    press("/");
    const field = document.querySelector<HTMLInputElement>("#brief-search");
    if (field === null) {
      throw new Error("no search field");
    }
    field.value = "reference parser";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    press("E");

    expect(paintedRows()).toContain(EVIDENCE);
    expect(paintedRows()).toContain(DEEPER);
    expect(paintedRows()).not.toContain("newest/changed/beta");
  });
});
