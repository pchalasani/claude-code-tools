import { describe, expect, it } from "vitest";

import { createHints, hintLabels, labelRows } from "./hints";
import { outline, type Row } from "./outline";
import { sampleBrief } from "../test/sample-brief";

/**
 * Take the first rows of the sample document.
 *
 * @param count - How many rows to take.
 * @returns The rows.
 */
function someRows(count: number): Row[] {
  return outline(sampleBrief()).slice(0, count);
}

describe("the labels", () => {
  it("uses one key each while single keys last", () => {
    expect(hintLabels(3, "asd")).toEqual(["a", "s", "d"]);
  });

  it("grows every label at once rather than mixing lengths", () => {
    // "a" and "as" cannot both be labels: typing "a" would have to wait to
    // see whether an "s" was coming, and a jump that waits is not a jump.
    const labels = hintLabels(4, "asd");

    expect(labels).toEqual(["aa", "as", "ad", "sa"]);
    expect(new Set(labels.map((label) => label.length))).toEqual(new Set([2]));
  });

  it("keeps going once two keys run out", () => {
    const labels = hintLabels(10, "asd");

    expect(labels).toHaveLength(10);
    expect(labels.every((label) => label.length === 3)).toBe(true);
    expect(new Set(labels).size).toBe(10);
  });

  it("labels nothing when there is nothing to label", () => {
    expect(hintLabels(0)).toEqual([]);
  });

  it("hands the labels out in the order the page paints", () => {
    const rows = someRows(3);

    expect([...labelRows(rows, "asd").entries()]).toEqual([
      [rows[0]?.id, "a"],
      [rows[1]?.id, "s"],
      [rows[2]?.id, "d"],
    ]);
  });
});

describe("jumping by label", () => {
  /**
   * Build a hint layer over the first rows of the sample document.
   *
   * @param count - How many rows carry labels.
   * @returns The layer and where it sent the cursor.
   */
  function layer(count: number) {
    const rows = someRows(count);
    const went: string[] = [];
    const hints = createHints({
      rows: () => rows,
      select: (id) => went.push(id),
      keys: "asd",
    });
    return { rows, hints, went };
  }

  it("is inert until it is asked for, and then labels every row", () => {
    const { rows, hints } = layer(3);

    expect(hints.active()).toBe(false);
    expect(hints.handleKey("a")).toBe(false);

    hints.enter();

    expect(hints.active()).toBe(true);
    expect(hints.labelFor(rows[2]?.id ?? "")).toBe("d");
  });

  it("goes to the row whose label was typed, and puts the labels away", () => {
    const { rows, hints, went } = layer(3);
    hints.enter();

    expect(hints.handleKey("s")).toBe(true);

    expect(went).toEqual([rows[1]?.id]);
    expect(hints.active()).toBe(false);
  });

  it("shows how far a two-key label has been typed", () => {
    const { rows, hints, went } = layer(5);
    hints.enter();

    hints.handleKey("a");

    expect(hints.typed()).toBe("a");
    expect(hints.labelFor(rows[1]?.id ?? "")).toBe("as");
    expect(went).toEqual([]);

    hints.handleKey("s");

    expect(went).toEqual([rows[1]?.id]);
  });

  it("ignores a key that no label starts with", () => {
    const { hints, went } = layer(5);
    hints.enter();

    expect(hints.handleKey("q")).toBe(true);
    expect(hints.handleKey("z")).toBe(true);

    expect(hints.typed()).toBe("");
    expect(hints.active()).toBe(true);
    expect(went).toEqual([]);
  });

  it("swallows every other key rather than moving underneath them", () => {
    const { hints, went } = layer(3);
    hints.enter();

    expect(hints.handleKey("j")).toBe(true);
    expect(hints.handleKey(" ")).toBe(true);

    expect(went).toEqual([]);
    expect(hints.active()).toBe(true);
  });

  it("leaves on Escape without moving the cursor", () => {
    const { hints, went } = layer(3);
    hints.enter();

    expect(hints.handleKey("Escape")).toBe(true);

    expect(hints.active()).toBe(false);
    expect(went).toEqual([]);
  });
});
