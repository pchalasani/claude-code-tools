import { beforeEach, describe, expect, it } from "vitest";

import {
  createHumanState,
  humanStorageKey,
} from "./human-state";

const RUN = "run-42";

beforeEach(() => {
  window.sessionStorage.clear();
  document.head.innerHTML = "";
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: memoryStorage(),
  });
});

/** Make the local-storage surface this jsdom build does not provide. */
function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length(): number {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, value);
    },
  };
}

describe("v2 human state", () => {
  it("uses one run namespace for all human state records", () => {
    expect(humanStorageKey("chosen", RUN)).toBe(
      "visual-brief-v2:run-42:chosen",
    );
    expect(humanStorageKey("cursor", RUN)).toBe(
      "visual-brief-v2:run-42:cursor",
    );
    expect(humanStorageKey("drafts", RUN)).toBe(
      "visual-brief-v2:run-42:drafts",
    );
    expect(humanStorageKey("seen", RUN)).toBe(
      "visual-brief-v2:run-42:seen",
    );
    expect(humanStorageKey("latest-briefing", RUN)).toBe(
      "visual-brief-v2:run-42:latest-briefing",
    );
  });

  it("includes the served run instance in persistent keys", () => {
    document.head.innerHTML = (
      '<meta name="visual-brief-run-instance" content="root-and-creation">'
    );

    expect(humanStorageKey("drafts", RUN)).toBe(
      "visual-brief-v2:run-42:root-and-creation:drafts",
    );
  });

  it("does not restore drafts from another run instance", () => {
    document.head.innerHTML = (
      '<meta name="visual-brief-run-instance" content="first-instance">'
    );
    const first = createHumanState(RUN);
    first.writeDraft("u/l", "Belongs only to the first run");
    document.head.innerHTML = (
      '<meta name="visual-brief-run-instance" content="second-instance">'
    );

    expect(createHumanState(RUN).drafts["u/l"])
      .toBeUndefined();
  });

  it("starts empty and changes only through its human-action methods", () => {
    const state = createHumanState(RUN);

    expect({ ...state.chosen }).toEqual({});
    expect(state.cursor()).toBeNull();
    expect({ ...state.drafts }).toEqual({});
    expect({ ...state.seen }).toEqual({});
    expect(state.latestBriefing()).toBeNull();

    state.choose("u/l/i", false);
    state.select("u/l/i");
    state.writeDraft("u/l/i", "Keep these words");
    state.visit("q", "2:answered");
    state.visitBriefing("newest");

    expect(state.chosen["u/l/i"]).toBe(false);
    expect(state.cursor()).toBe("u/l/i");
    expect(state.drafts["u/l/i"]).toBe("Keep these words");
    expect(state.seen.q).toBe("2:answered");
    expect(state.latestBriefing()).toBe("newest");
  });

  it("restores all records and mirrors drafts to local storage", () => {
    const first = createHumanState(RUN);
    first.chooseAll(["u", "u/l"], true);
    first.select("u/l");
    first.writeDraft("u/l", "Restart-safe");
    first.visit("q", "4:answered");
    first.visitBriefing("newest");

    expect(
      window.localStorage.getItem(humanStorageKey("drafts", RUN)),
    ).toContain("Restart-safe");

    const restored = createHumanState(RUN);
    expect({ ...restored.chosen }).toEqual({ u: true, "u/l": true });
    expect(restored.cursor()).toBe("u/l");
    expect(restored.drafts["u/l"]).toBe("Restart-safe");
    expect(restored.seen.q).toBe("4:answered");
    expect(restored.latestBriefing()).toBe("newest");
  });

  it("keeps a thread id separate from the latest briefing marker", () => {
    const first = createHumanState(RUN);
    first.visit("//latest-briefing", "2:answered");
    first.visitBriefing("newest");

    const restored = createHumanState(RUN);
    expect(restored.seen["//latest-briefing"]).toBe("2:answered");
    expect(restored.latestBriefing()).toBe("newest");
  });

  it("uses the local draft only when the session copy is absent", () => {
    const key = humanStorageKey("drafts", RUN);
    window.localStorage.setItem(key, JSON.stringify({ "u/l": "Recovered" }));

    expect(createHumanState(RUN).drafts["u/l"]).toBe("Recovered");
  });

  it("reports when either draft store refuses a write", () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        ...memoryStorage(),
        setItem: () => {
          throw new DOMException("Storage disabled", "SecurityError");
        },
      },
    });
    const state = createHumanState(RUN);

    state.writeDraft("u/l", "Keep this in memory");

    expect(state.draftWarning()).toBe(
      "Draft storage is unavailable. Reloading will lose this text.",
    );
    expect(state.drafts["u/l"]).toBe("Keep this in memory");
  });

  it("destroys a draft only through explicit discard", () => {
    const state = createHumanState(RUN);
    state.writeDraft("u/l", "Unsaved thought");

    state.choose("u/l", false);
    state.select("u");
    expect(state.drafts["u/l"]).toBe("Unsaved thought");

    state.discardDraft("u/l");
    expect(state.drafts["u/l"]).toBeUndefined();
    expect(
      window.localStorage.getItem(humanStorageKey("drafts", RUN)),
    ).toBe("{}");
  });
});
