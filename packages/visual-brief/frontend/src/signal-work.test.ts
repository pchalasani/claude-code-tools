import { createRoot, createSignal } from "solid-js";
import { beforeEach, describe, expect, it } from "vitest";

import { createSignalWork, type SignalWork } from "./signal-work";
import { readAcceptedSignalWork } from "./session-store";
import { forgetStores } from "../test/storage";

const ROW = "newest/changed/alpha";
const TEXT = "Show me the concrete evidence behind this claim.";
const STAMP = "2026-08-04T11:12:33.054Z";

interface HeldWork {
  work: SignalWork;
  setAnswered: (answered: boolean) => void;
  setLatest: (updateId: string | undefined) => void;
  setRows: (rows: ReadonlySet<string>) => void;
  dispose: () => void;
}

function heldWork(
  updateId = "newest",
  initialRows: ReadonlySet<string> = new Set([ROW]),
): HeldWork {
  const [latest, setLatest] = createSignal<string | undefined>(updateId);
  const [answered, setAnswered] = createSignal(false);
  const [rows, setRows] = createSignal(initialRows);
  let dispose = (): void => undefined;
  const work = createRoot((stop) => {
    dispose = stop;
    return createSignalWork({
      latestUpdateId: latest,
      rowExists: (rowId) => rows().has(rowId),
      answered: () => answered(),
    });
  });
  return { work, setAnswered, setLatest, setRows, dispose };
}

function accept(
  work: SignalWork,
  signal = "show-evidence",
  baseline = work.baseline(),
  sequence?: number,
): void {
  work.accept(ROW, signal, baseline, sequence, TEXT, STAMP);
}

beforeEach(() => forgetStores());

describe("accepted signal work", () => {
  it("persists one accepted sign per row across a reload", () => {
    const first = heldWork();
    accept(first.work);
    accept(first.work);

    expect(first.work.at(ROW)).toBe(true);
    expect(first.work.selectedAt(ROW)).toBe("show-evidence");
    expect(readAcceptedSignalWork()).toEqual({
      [ROW]: {
        baseline: "newest",
        at: STAMP,
        signal: "show-evidence",
        text: TEXT,
      },
    });
    first.dispose();

    const reloaded = heldWork();
    expect(reloaded.work.at(ROW)).toBe(true);
    expect(reloaded.work.selectedAt(ROW)).toBe("show-evidence");
    reloaded.dispose();
  });

  it("clears on the next appended update and accepts a newer baseline", async () => {
    const held = heldWork();
    accept(held.work);

    held.setLatest("next-update");
    await Promise.resolve();
    expect(held.work.at(ROW)).toBe(false);
    expect(readAcceptedSignalWork()).toEqual({});

    accept(held.work);
    expect(held.work.at(ROW)).toBe(true);
    expect(readAcceptedSignalWork()).toEqual({
      [ROW]: {
        baseline: "next-update",
        at: STAMP,
        signal: "show-evidence",
        text: TEXT,
      },
    });
    held.dispose();
  });

  it("ignores an acceptance sent against an earlier update", async () => {
    const held = heldWork();
    const sentBaseline = held.work.baseline();

    held.setLatest("next-update");
    await Promise.resolve();
    accept(held.work, "too-dense", sentBaseline);

    expect(held.work.at(ROW)).toBe(false);
    expect(held.work.selectedAt(ROW)).toBeNull();
    expect(readAcceptedSignalWork()).toEqual({});
    held.dispose();
  });

  it("keeps the latest accepted request when replies finish out of order", () => {
    const held = heldWork();
    const sentBaseline = held.work.baseline();

    accept(held.work, "go-deeper", sentBaseline, 2);
    accept(held.work, "too-dense", sentBaseline, 1);

    expect(held.work.selectedAt(ROW)).toBe("go-deeper");
    expect(readAcceptedSignalWork()).toEqual({
      [ROW]: {
        baseline: "newest",
        at: STAMP,
        signal: "go-deeper",
        text: TEXT,
      },
    });
    held.dispose();
  });

  it("clears when an agent answer arrives below the signalled row", async () => {
    const held = heldWork();
    accept(held.work);

    held.setAnswered(true);
    await Promise.resolve();

    expect(held.work.at(ROW)).toBe(false);
    expect(held.work.selectedAt(ROW)).toBeNull();
    expect(readAcceptedSignalWork()).toEqual({});
    held.dispose();
  });

  it("clears stored work when its row becomes orphaned", async () => {
    const held = heldWork();
    accept(held.work, "go-deeper");

    held.setRows(new Set());
    await Promise.resolve();

    expect(held.work.at(ROW)).toBe(false);
    expect(held.work.selectedAt(ROW)).toBeNull();
    expect(readAcceptedSignalWork()).toEqual({});
    held.dispose();
  });
});
