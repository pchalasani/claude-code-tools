import { describe, expect, it } from "vitest";

import type { BriefDocument, Forensic } from "./document";
import { evidenceRowId, noteRowId } from "./evidence";
import { outline } from "./outline";
import {
  click,
  mount,
  paintedCursor,
  paintedOpen,
  press,
  unmount,
  useHarness,
} from "../test/harness";
import { sampleBrief } from "../test/sample-brief";

const ITEM = "newest/changed/alpha";
const EVIDENCE = evidenceRowId(ITEM);
const NOTE = noteRowId(EVIDENCE, "~the-reference-run");
const DEEPER = noteRowId(NOTE, "~the-one-disagreement");

useHarness();

/**
 * Build a page whose first item carries the evidence given to it.
 *
 * @param entries - What the item's forensics hold.
 * @returns The document.
 */
function withForensics(entries: Forensic[]): BriefDocument {
  const brief = sampleBrief();
  const item = brief.updates
    .find((update) => update.id === "newest")
    ?.lanes.find((lane) => lane.id === "changed")
    ?.items.find((one) => one.id === "alpha");
  if (item === undefined) {
    throw new Error("the sample document lost the item this test writes at");
  }
  item.forensics = entries;
  return brief;
}

/**
 * Build a page whose first item carries evidence that nests.
 *
 * @returns The document.
 */
function withEvidence(): BriefDocument {
  return withForensics([
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
  ]);
}

/**
 * Read the head line one row is painting.
 *
 * The row is found by comparing the painted attribute, not by handing the id
 * to a selector: jsdom's selector engine matches these attribute values
 * without regard to case, which would quietly conflate two rows named ``Aa``
 * and ``aa`` — a distinction the document is allowed to make.
 *
 * @param id - Row id to read.
 * @returns The head's text, or null when the row is not on the page.
 */
function paintedHead(id: string): string | null {
  const row = [...document.querySelectorAll("[data-row-id]")].find(
    (candidate) => candidate.getAttribute("data-row-id") === id,
  );
  return row?.querySelector(":scope > .row-head")?.textContent ?? null;
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

describe("evidence rows are named, not numbered", () => {
  const FIRST = { title: "The first finding", body: "One." };
  const SECOND = { title: "The second finding", body: "Two." };
  const PREPENDED = { title: "What landed first", body: "Zero." };

  /**
   * Put the cursor on a note by clicking its head, the way a hand does.
   *
   * @param title - Head text of the note to land on.
   * @returns The row id the page says the cursor is on.
   */
  function landOn(title: string): string {
    const rows = [...document.querySelectorAll('[data-row-kind="evidence"]')];
    const head = rows.find((row) =>
      (row.querySelector(".row-head")?.textContent ?? "").includes(title),
    );
    const id = head?.getAttribute("data-row-id") ?? "";
    expect(id, `no evidence row headed ${title}`).not.toBe("");
    click(id);
    return id;
  }

  it("puts a reloaded cursor back on the note, not on its neighbour", () => {
    mount(withForensics([{ ...FIRST }, { ...SECOND }]));
    press("E");
    const before = landOn("The second finding");
    expect(paintedCursor()).toBe(before);

    // The agent publishes again with one more note, written above the two
    // that were already there. Everything after it shifts down a place.
    unmount();
    mount(withForensics([{ ...PREPENDED }, { ...FIRST }, { ...SECOND }]));
    press("E");

    expect(paintedCursor()).toBe(before);
    expect(paintedHead(before)).toContain("The second finding");
  });

  it("takes the name the document declares over the note's title", () => {
    const brief = withForensics([
      { id: "reference-run", title: "The reference run", body: "One." },
    ]);
    mount(brief);
    press("E");

    const id = noteRowId(EVIDENCE, "reference-run");
    expect(paintedRows()).toContain(id);
    expect(paintedHead(id)).toContain("The reference run");
    expect(outline(brief).map((row) => row.id)).toContain(id);
  });

  it("keeps a declared name and a derived one out of each other's way", () => {
    const declared = {
      id: "reference-run",
      title: "The note that says what it is called",
      body: "Two.",
    };
    // This one's title slugs to exactly the name the other one declares.
    const titled = { title: "Reference run", body: "One." };
    const byName = noteRowId(EVIDENCE, "reference-run");
    const byTitle = noteRowId(EVIDENCE, "~reference-run");

    mount(withForensics([{ ...titled }]));
    press("E");
    expect(paintedHead(byTitle)).toContain("Reference run");

    // A later publish gives a new note the name the first one's title reads
    // like. Neither note may be renamed by the other's arrival.
    unmount();
    mount(withForensics([{ ...titled }, { ...declared }]));
    press("E");

    expect(paintedHead(byTitle)).toContain("Reference run");
    expect(paintedHead(byName)).toContain("says what it is called");
  });

  it("spells a declared name the way the document spelled it", () => {
    const brief = withForensics([
      { id: "Aa", title: "Upper", body: "One." },
      { id: "aa", title: "Lower", body: "Two." },
    ]);
    mount(brief);
    press("E");

    // Two names the renderer accepts as different stay different here: folded
    // together, one of them would be settled by its position in the list.
    expect(paintedHead(noteRowId(EVIDENCE, "Aa"))).toContain("Upper");
    expect(paintedHead(noteRowId(EVIDENCE, "aa"))).toContain("Lower");
    expect(paintedRows()).toEqual(outline(brief).map((row) => row.id));
  });

  it("refuses a declared name that would break the id it lands in", () => {
    // The renderer never publishes this, so what is being pinned down is what
    // the page does with a document that reached it anyway: fall back to the
    // title rather than paint a row id no toggle can point at.
    const brief = withForensics([
      { id: "two words", title: "The reference run", body: "One." },
    ]);
    mount(brief);
    press("E");

    const id = noteRowId(EVIDENCE, "~the-reference-run");
    expect(paintedHead(id)).toContain("The reference run");
    for (const painted of paintedRows()) {
      expect(painted).not.toMatch(/[ \t\n\r\f]/u);
    }
  });

  it("hands a name two siblings answer to to neither of them", () => {
    // The renderer refuses to publish this. What is pinned down is what the
    // page does with a document that reached it anyway: it invents no
    // identity, because the only one left is the note's place in the list,
    // and that moves the moment a note is written above it. Nothing is hidden
    // either — evidence with no name of its own is still evidence.
    const brief = withForensics([
      { title: "Log", body: "One.", children: [{ title: "D", body: "Deep." }] },
      { title: "Log", body: "Two." },
      { title: "The other finding", body: "Three." },
    ]);
    mount(brief);
    press("E");

    const ids = outline(brief)
      .filter((row) => row.parentId === EVIDENCE)
      .map((row) => row.id);
    expect(ids).toEqual([noteRowId(EVIDENCE, "~the-other-finding")]);
    expect(paintedRows()).toEqual(outline(brief).map((row) => row.id));
    expect(new Set(paintedRows()).size).toBe(paintedRows().length);
    expect(paintedRows()).not.toContain(noteRowId(EVIDENCE, "~log"));
    expect(paintedRows()).not.toContain(noteRowId(EVIDENCE, "~log-2"));
    const body =
      document.querySelector(`[data-row-id="${EVIDENCE}"] > .row-body`)
        ?.textContent ?? "";
    for (const written of ["Log", "One.", "Two.", "Deep."]) {
      expect(body, `${written} fell off the page`).toContain(written);
    }
  });

  it("names every row's body with one id reference, never a list", () => {
    mount(withEvidence());
    press("E");

    const toggles = [...document.querySelectorAll("[aria-controls]")];
    expect(toggles.length).toBeGreaterThan(0);
    for (const toggle of toggles) {
      const named = toggle.getAttribute("aria-controls") ?? "";
      // `aria-controls` is a whitespace-separated list of id references, so a
      // space in it names several elements that do not exist.
      expect(named, "an id reference cannot hold whitespace").not.toMatch(
        /\s/u,
      );
      expect(
        document.getElementById(named),
        `nothing on the page has the id ${named}`,
      ).not.toBeNull();
    }
    for (const id of paintedRows()) {
      expect(id, "a row id becomes an id reference").not.toMatch(/\s/u);
    }
  });
});
