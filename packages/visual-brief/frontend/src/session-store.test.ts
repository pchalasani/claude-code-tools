import { beforeEach, describe, expect, it } from "vitest";

import {
  readHealedGeneration,
  readSentRecords,
  rememberHealedGeneration,
  saveSentRecords,
} from "./session-store";
import { forgetStores, withoutSessionStorage } from "../test/storage";

beforeEach(() => {
  forgetStores();
});

describe("what a tab remembers about reloading itself", () => {
  it("has nothing to say before anything has happened", () => {
    expect(readHealedGeneration()).toBeNull();
  });

  it("reads back the generation it was told to remember", () => {
    rememberHealedGeneration("a".repeat(64));

    expect(readHealedGeneration()).toBe("a".repeat(64));
  });

  it("remembers a page that had no generation of its own", () => {
    // The page most likely to heal is the one served without a generation at
    // all: it cannot be compared with anything, so it reloads. Reading that
    // memory back as "never happened" is what made such a page reload on
    // every load for as long as the tab stayed open.
    rememberHealedGeneration("");

    expect(readHealedGeneration()).toBe("");
  });

  it("keeps remembering when session storage is refused outright", async () => {
    await withoutSessionStorage(() => {
      rememberHealedGeneration("b".repeat(64));

      expect(readHealedGeneration()).toBe("b".repeat(64));
    });
  });

  it("keeps remembering the empty generation without that store too", async () => {
    await withoutSessionStorage(() => {
      rememberHealedGeneration("");

      expect(readHealedGeneration()).toBe("");
    });
  });

  it("still has nothing to say when both stores are empty", async () => {
    await withoutSessionStorage(() => {
      expect(readHealedGeneration()).toBeNull();
    });
  });

  it("leaves whatever else the history entry was carrying alone", async () => {
    window.history.replaceState({ somebodyElse: "keep me" }, "");

    rememberHealedGeneration("c".repeat(64));

    await withoutSessionStorage(() => {
      expect(readHealedGeneration()).toBe("c".repeat(64));
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

  it("survives without session storage, like a healed generation", async () => {
    // A publish the human did not cause reloads the page; a browser that has
    // taken session storage away must not also take the waiting sign.
    saveSentRecords([record]);

    await withoutSessionStorage(() => {
      expect(readSentRecords()).toEqual([record]);
    });
  });

  it("reads back exactly what it stored", () => {
    saveSentRecords([record]);

    expect(readSentRecords()).toEqual([record]);
  });

  it("still remembers through the history entry without a store", async () => {
    // The reviewer's case: a browser that silently takes session storage
    // away must not also take the waiting sign. The history entry carries
    // the records instead, exactly as it does for healed generations.
    saveSentRecords([record]);

    await withoutSessionStorage(() => {
      expect(readSentRecords()).toEqual([record]);
    });
  });
});
