import { beforeEach, describe, expect, it } from "vitest";

import {
  readHealedStandoff,
  readSentRecords,
  rememberHealedStandoff,
  saveSentRecords,
} from "./session-store";
import { forgetStores, withoutSessionStorage } from "../test/storage";

beforeEach(() => {
  forgetStores();
});

describe("what a tab remembers about reloading itself", () => {
  it("has nothing to say before anything has happened", () => {
    expect(readHealedStandoff()).toBeNull();
  });

  it("reads back the standoff it was told to remember", () => {
    rememberHealedStandoff("a".repeat(64));

    expect(readHealedStandoff()).toBe("a".repeat(64));
  });

  it("remembers a standoff a page with no generation was in", () => {
    // The page most likely to heal is the one served without a generation at
    // all: it cannot be compared with anything, so it reloads. Reading that
    // memory back as "never happened" is what made such a page reload on
    // every load for as long as the tab stayed open.
    rememberHealedStandoff("");

    expect(readHealedStandoff()).toBe("");
  });

  it("keeps remembering when session storage is refused outright", async () => {
    await withoutSessionStorage(() => {
      rememberHealedStandoff("b".repeat(64));

      expect(readHealedStandoff()).toBe("b".repeat(64));
    });
  });

  it("keeps remembering the empty standoff without that store too", async () => {
    await withoutSessionStorage(() => {
      rememberHealedStandoff("");

      expect(readHealedStandoff()).toBe("");
    });
  });

  it("still has nothing to say when both stores are empty", async () => {
    await withoutSessionStorage(() => {
      expect(readHealedStandoff()).toBeNull();
    });
  });

  it("leaves whatever else the history entry was carrying alone", async () => {
    window.history.replaceState({ somebodyElse: "keep me" }, "");

    rememberHealedStandoff("c".repeat(64));

    await withoutSessionStorage(() => {
      expect(readHealedStandoff()).toBe("c".repeat(64));
    });
    expect(window.history.state).toMatchObject({ somebodyElse: "keep me" });
  });
});

describe("what a tab remembers about what it sent", () => {
  const record = {
    rowId: "newest/changed/alpha",
    anchorId: "newest/changed/alpha",
    text: "Did this land?",
    at: "2026-07-27T11:00:00Z",
    loads: 0,
  };

  it("reads back exactly what it stored", () => {
    saveSentRecords([record]);

    expect(readSentRecords()).toEqual([record]);
  });

});
