import { describe, expect, it, vi } from "vitest";

import { humanStorageKey } from "./human-state";
import {
  explicitSelectionTookOver,
  pointerIsDriving,
  watchPointer,
} from "./pointer";
import {
  click,
  composeAt,
  mount,
  mountLive,
  paintedCursor,
  paintedOpen,
  press,
  pressAt,
  rowNode,
  typeInto,
  unmount,
  useHarness,
} from "../test/harness";
import {
  itemOf,
  laneOf,
  SAMPLE_SUGGESTIONS,
  sampleBrief,
} from "../test/sample-brief";

const ALPHA = "newest/changed/alpha";
const ANSWERED = `${ALPHA}#q-answered`;
const BETA = "newest/changed/beta";
const GAMMA = "newest/next/gamma";
const PATCHED = "newest/patched/arrived";
const PATCHED_CHAT = `${PATCHED}#q-patched`;

useHarness();

function storedHumanState(): Record<string, string | null> {
  return Object.fromEntries(
    (["chosen", "cursor", "drafts", "seen"] as const).map((part) => [
      part,
      window.sessionStorage.getItem(humanStorageKey(part, "")),
    ]),
  );
}

function paintedLayout(): [string, string | null][] {
  return [...document.querySelectorAll("[data-row-id]")].map((row) => [
    row.getAttribute("data-row-id") ?? "",
    row.getAttribute("data-open"),
  ]);
}

function restoreWindowProperty(
  name: string,
  descriptor: PropertyDescriptor | undefined,
): void {
  if (descriptor === undefined) {
    Reflect.deleteProperty(window, name);
  } else {
    Object.defineProperty(window, name, descriptor);
  }
}

describe("non-human events cannot write human state", () => {
  it("preserves all four maps across publish, search, and unmount", () => {
    const { publish } = mountLive();
    click(ANSWERED);
    composeAt(ALPHA);
    typeInto(".composer textarea", "Keep this draft");
    click(ALPHA);
    const before = storedHumanState();

    const next = sampleBrief();
    itemOf(next, BETA).glance = "Published without touching reader state";
    publish(next);
    press("/");
    typeInto("#brief-search", "reference parser");

    expect(storedHumanState()).toEqual(before);
    unmount();
    expect(storedHumanState()).toEqual(before);
  });

  it("makes composing an explicit choice to keep that row open", () => {
    mount();
    composeAt(ANSWERED);

    expect(
      JSON.parse(
        window.sessionStorage.getItem(humanStorageKey("chosen", "")) ?? "{}",
      ),
    ).toMatchObject({ [ANSWERED]: true });
  });
});

describe("pure views", () => {
  it("does not paint lowest-level ordinal numbers", () => {
    mount();

    expect(document.querySelector(".ordinal")).toBeNull();
  });

  it("marks exactly the owning item while its descendants hold the cursor", () => {
    mount();
    expect(document.querySelector('[data-owning-item="true"]')).toBeNull();

    composeAt(ALPHA);
    expect(document.querySelectorAll('[data-owning-item="true"]'))
      .toHaveLength(1);
    expect(rowNode(ALPHA)?.getAttribute("data-owning-item")).toBe("true");

    click(ANSWERED);
    const boundary = document.querySelector('[data-owning-item="true"]');
    expect(paintedCursor()).toBe(ANSWERED);
    expect(boundary).toBe(rowNode(ALPHA));
    expect(boundary?.contains(rowNode(ANSWERED))).toBe(true);

    click("newest/changed");
    expect(document.querySelector('[data-owning-item="true"]')).toBeNull();
  });

  it("reveals a folded match without changing folds or the active query", () => {
    mount();
    click(ALPHA);
    const chosen = window.sessionStorage.getItem(
      humanStorageKey("chosen", ""),
    );
    press("/");
    typeInto("#brief-search", "reference parser");

    expect(rowNode(ALPHA)?.getAttribute("data-open")).toBe("true");
    expect(
      rowNode(ALPHA)?.querySelector(".explanation")?.textContent,
    ).toContain("reference parser");
    expect(rowNode(ALPHA)?.getAttribute("data-open")).toBe("true");
    expect(
      window.sessionStorage.getItem(humanStorageKey("chosen", "")),
    ).toBe(chosen);
  });

  it("lets a map click drop a filter as one explicit human selection", () => {
    mount();
    press("/");
    typeInto("#brief-search", "reference parser");
    document.querySelector<HTMLButtonElement>(
      '[data-map-lane="newest/next"]',
    )?.click();

    expect(
      document.querySelector<HTMLInputElement>("#brief-search")?.value,
    ).toBe("");
    expect(rowNode("newest/next")?.getAttribute("data-cursor")).toBe("true");

    press("/");
    typeInto("#brief-search", "reference parser");
    document.querySelector<HTMLButtonElement>(
      '[data-map-lane="newest/changed"]',
    )?.click();
    expect(
      document.querySelector<HTMLInputElement>("#brief-search")?.value,
    ).toBe("");
  });

  it("closes a composer hidden by folding and preserves its draft", () => {
    mount();
    composeAt(ALPHA);
    typeInto(".composer textarea", "Keep this folded draft");

    document.querySelector<HTMLButtonElement>(
      `[data-row-id="${ALPHA}"] > .row-head .row-toggle`,
    )?.click();

    expect(document.querySelector(".composer")).toBeNull();
    composeAt(ALPHA);
    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("Keep this folded draft");
  });
});

describe("human fold actions", () => {
  it("aims collapse-all at the first row so Space can open it", () => {
    mount();
    click(ALPHA);

    press("C");

    expect(paintedCursor()).toBe("newest");
    expect(paintedOpen("newest")).toBe("false");

    press(" ");

    expect(paintedOpen("newest")).toBe("true");
  });

  it("keeps collapsed search matches while adding chat paths in order", () => {
    mount();
    press("C");
    press("/");
    typeInto("#brief-search", "parser");
    const searchedLayout = paintedLayout();
    expect(searchedLayout.map(([id]) => id)).toEqual([
      "newest",
      "newest/changed",
      ALPHA,
      "older",
      "older/history",
      "older/history/one",
    ]);

    press("m");

    expect(paintedLayout().map(([id]) => id)).toEqual([
      "newest",
      "newest/changed",
      ALPHA,
      ANSWERED,
      BETA,
      `${BETA}#q-open`,
      "older",
      "older/history",
      "older/history/one",
    ]);

    press("m");

    expect(paintedLayout()).toEqual(searchedLayout);
  });

  it("keeps normal item counts on folded lane headers", () => {
    mount();
    press("C");
    click("newest");

    expect(
      rowNode("newest/changed")?.querySelector(".row-count")?.textContent,
    ).toBe("2");
    expect(
      rowNode("newest/next")?.querySelector(".row-count")?.textContent,
    ).toBe("1");
    expect(rowNode(ALPHA)).toBeNull();
    expect(rowNode(GAMMA)).toBeNull();
  });

  it("keeps search open when selection enters revealed chat paths", () => {
    mount();
    press("/");
    typeInto("#brief-search", "reader agrees");
    press("m");

    press("j");
    press("j");
    press("j");

    expect(paintedCursor()).toBe(ANSWERED);
    expect(
      document.querySelector<HTMLInputElement>("#brief-search")?.value,
    ).toBe("reader agrees");
    expect(document.querySelector('[role="search"]')).not.toBeNull();

    click(BETA);

    expect(paintedCursor()).toBe(BETA);
    expect(
      document.querySelector<HTMLInputElement>("#brief-search")?.value,
    ).toBe("reader agrees");
    expect(document.querySelector('[role="search"]')).not.toBeNull();
  });

  it("reveals chats, then exactly restores a manually opened lane", async () => {
    mount();
    press("C");
    click("newest");
    click("newest/next");
    composeAt(GAMMA);
    typeInto(".composer textarea", "Keep this draft and composer");
    await Promise.resolve();
    const scrollIntoView = vi.fn();
    const originalScroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrollIntoView;
    const layout = paintedLayout();
    const before = storedHumanState();

    try {
      press("m");

      expect(rowNode(ANSWERED)?.getAttribute("data-open")).toBe("true");
      expect(rowNode(`${BETA}#q-open`)?.getAttribute("data-open")).toBe(
        "true",
      );
      for (const [id] of layout) {
        expect(rowNode(id)).not.toBeNull();
      }
      expect(rowNode("older/history")).toBeNull();
      expect(rowNode("older")?.getAttribute("data-open")).toBe("false");
      expect(rowNode(GAMMA)?.getAttribute("data-open")).toBe("true");
      expect(storedHumanState()).toMatchObject({
        cursor: before.cursor,
        drafts: before.drafts,
        seen: before.seen,
      });
      expect(
        document.querySelector('[data-action="reveal-chats"]')?.getAttribute(
          "aria-pressed",
        ),
      ).toBe("true");

      press("m");

      expect(paintedLayout()).toEqual(layout);
      expect(storedHumanState()).toEqual(before);
      expect(document.querySelector(".composer textarea")).not.toBeNull();
      expect(scrollIntoView).not.toHaveBeenCalled();
      click("older");
      const laterLayout = paintedLayout();
      press("m");
      press("m");
      expect(paintedLayout()).toEqual(laterLayout);
    } finally {
      if (originalScroll === undefined) {
        Reflect.deleteProperty(Element.prototype, "scrollIntoView");
      } else {
        Element.prototype.scrollIntoView = originalScroll;
      }
    }
  });

  it("reveals every chat over search, then resumes the search view", () => {
    mount();
    press("/");
    typeInto("#brief-search", "reference parser");
    const before = storedHumanState();
    const searchedLayout = paintedLayout();
    expect(rowNode(ANSWERED)).toBeNull();
    expect(rowNode(`${BETA}#q-open`)).toBeNull();
    const action = document.querySelector<HTMLButtonElement>(
      '[data-action="reveal-chats"]',
    );

    action?.click();

    expect(rowNode(ANSWERED)?.getAttribute("data-open")).toBe("true");
    expect(rowNode(`${BETA}#q-open`)?.getAttribute("data-open")).toBe(
      "true",
    );
    expect(
      document.querySelector<HTMLInputElement>("#brief-search")?.value,
    ).toBe("reference parser");
    expect(document.querySelector('[role="search"]')).not.toBeNull();
    expect(storedHumanState()).toMatchObject({
      cursor: before.cursor,
      drafts: before.drafts,
      seen: before.seen,
    });

    action?.click();

    expect(paintedLayout()).toEqual(searchedLayout);
    expect(rowNode(ANSWERED)).toBeNull();
    expect(rowNode(`${BETA}#q-open`)).toBeNull();
    expect(
      document.querySelector<HTMLInputElement>("#brief-search")?.value,
    ).toBe("reference parser");
    expect(document.querySelector('[role="search"]')).not.toBeNull();
    expect(storedHumanState()).toEqual(before);
  });

  it("keeps patch-born chats outside the captured reveal", () => {
    const { publish } = mountLive();
    const normalLayout = new Map(paintedLayout());
    press("/");
    typeInto("#brief-search", "reference parser");
    const searchedLayout = paintedLayout();
    press("m");
    const revealedLayout = paintedLayout();

    const next = sampleBrief();
    next.updates.find((update) => update.id === "newest")?.lanes.push({
      id: "patched",
      name: "Arrived in a patch",
      items: [{
        id: "arrived",
        glance: "A patch-born item",
        explanation: "This chat did not exist when reveal began.",
        trust: "reported-by-agent",
        questions: [{
          id: "q-patched",
          anchor: { kind: "element", path: PATCHED },
          turns: [
            {
              author: "human",
              text: "Was this present when reveal began?",
              at: "2026-07-29T13:00:00Z",
            },
            {
              author: "agent",
              text: "No, it arrived in the patch.",
              at: "2026-07-29T13:01:00Z",
            },
          ],
        }],
      }],
    });
    publish(next);

    expect(paintedLayout()).toEqual(revealedLayout);
    expect(rowNode(PATCHED_CHAT)).toBeNull();

    press("m");

    expect(paintedLayout()).toEqual(searchedLayout);
    typeInto("#brief-search", "");
    for (const [id, open] of normalLayout) {
      expect(rowNode(id)?.getAttribute("data-open")).toBe(open);
    }
    expect(rowNode("newest/patched")?.getAttribute("data-open")).toBe(
      "true",
    );
    expect(rowNode(PATCHED)?.getAttribute("data-open")).toBe("true");
    expect(rowNode(PATCHED_CHAT)?.getAttribute("data-open")).toBe("true");
    const chosen = JSON.parse(storedHumanState().chosen ?? "{}");
    expect(chosen).not.toHaveProperty("newest/patched");
    expect(chosen).not.toHaveProperty(PATCHED);
    expect(chosen).not.toHaveProperty(PATCHED_CHAT);
  });

  it("requests the same scroll offset after both reveal transitions", () => {
    mount();
    const scrollX = Object.getOwnPropertyDescriptor(window, "scrollX");
    const scrollY = Object.getOwnPropertyDescriptor(window, "scrollY");
    const animationFrame = Object.getOwnPropertyDescriptor(
      window,
      "requestAnimationFrame",
    );
    Object.defineProperties(window, {
      scrollX: { configurable: true, value: 37 },
      scrollY: { configurable: true, value: 415 },
      requestAnimationFrame: {
        configurable: true,
        value: vi.fn((callback: FrameRequestCallback) => {
          callback(0);
          return 1;
        }),
      },
    });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});

    try {
      press("m");
      press("m");

      expect(window.requestAnimationFrame).toHaveBeenCalledTimes(2);
      expect(scrollTo).toHaveBeenCalledTimes(4);
      for (const call of scrollTo.mock.calls) {
        expect(call).toEqual([37, 415]);
      }
    } finally {
      scrollTo.mockRestore();
      restoreWindowProperty("scrollX", scrollX);
      restoreWindowProperty("scrollY", scrollY);
      restoreWindowProperty("requestAnimationFrame", animationFrame);
    }
  });

  it("keeps a stationary mouse from retaking the cursor after attention", () => {
    mount();
    press("C");
    click("older");
    const before = paintedCursor();
    const stop = watchPointer(document);
    const pointerMove = (target: Element): void => {
      const event = new Event("pointermove", { bubbles: true });
      Object.defineProperties(event, {
        pointerType: { value: "mouse" },
        clientX: { value: 40 },
        clientY: { value: 50 },
      });
      target.dispatchEvent(event);
    };

    try {
      const action = document.querySelector<HTMLElement>(".meta-attention");
      expect(action).not.toBeNull();
      pointerMove(action as HTMLElement);
      expect(pointerIsDriving()).toBe(true);

      action?.click();
      const selectedByAttention = paintedCursor();
      expect(selectedByAttention).toBe(ANSWERED);
      expect(pointerIsDriving()).toBe(false);
      pointerMove(rowNode(ALPHA) as Element);

      expect(paintedCursor()).not.toBe(before);
      expect(paintedCursor()).toBe(selectedByAttention);
    } finally {
      explicitSelectionTookOver();
      stop();
    }
  });
});

describe("updates log drawer", () => {
  const point = (
    target: Element,
    eventName: "pointerenter" | "pointerleave",
    pointerType: string,
  ): void => {
    const event = new Event(eventName);
    Object.defineProperty(event, "pointerType", { value: pointerType });
    target.dispatchEvent(event);
  };

  it("starts hidden and names every visible control for the updates log", () => {
    mount();
    const trigger = document.querySelector<HTMLButtonElement>(".map-trigger");
    const map = document.querySelector<HTMLElement>("#brief-structure-map");
    const close = document.querySelector<HTMLButtonElement>(".map-close");

    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
    expect(trigger?.getAttribute("aria-label")).toBe("Open briefing ledger");
    expect(trigger?.textContent).toContain("Briefing ledger");
    expect(map?.getAttribute("data-open")).toBe("false");
    expect(map?.getAttribute("aria-hidden")).toBe("true");
    expect(map?.getAttribute("aria-label")).toBe("Briefing ledger");
    expect(close?.getAttribute("aria-label")).toBe(
      "Close briefing ledger",
    );
  });

  it("waits for mouse intent and cancels on leave and cleanup", () => {
    vi.useFakeTimers();
    try {
      mount();
      const trigger = document.querySelector<HTMLButtonElement>(".map-trigger");

      point(trigger as HTMLButtonElement, "pointerenter", "mouse");
      vi.advanceTimersByTime(199);
      expect(trigger?.getAttribute("aria-expanded")).toBe("false");

      point(trigger as HTMLButtonElement, "pointerleave", "mouse");
      vi.advanceTimersByTime(1);
      expect(trigger?.getAttribute("aria-expanded")).toBe("false");

      point(trigger as HTMLButtonElement, "pointerenter", "mouse");
      vi.advanceTimersByTime(200);
      expect(trigger?.getAttribute("aria-expanded")).toBe("true");

      pressAt(trigger as HTMLButtonElement, "Escape");
      point(trigger as HTMLButtonElement, "pointerenter", "mouse");
      expect(vi.getTimerCount()).toBeGreaterThan(0);
      unmount();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores touch and pen hover but opens immediately on focus or click", () => {
    mount();
    const trigger = document.querySelector<HTMLButtonElement>(".map-trigger");

    point(trigger as HTMLButtonElement, "pointerenter", "touch");
    point(trigger as HTMLButtonElement, "pointerenter", "pen");
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");

    trigger?.focus();
    expect(trigger?.getAttribute("aria-expanded")).toBe("true");

    pressAt(trigger as HTMLButtonElement, "Escape");
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(trigger);

    trigger?.click();
    expect(trigger?.getAttribute("aria-expanded")).toBe("true");
  });

  it("restores drawer focus before closing but leaves outside focus alone", () => {
    vi.useFakeTimers();
    mount();
    try {
      const trigger = document.querySelector<HTMLButtonElement>(".map-trigger");
      const close = document.querySelector<HTMLButtonElement>(".map-close");
      trigger?.click();
      close?.focus();
      close?.click();

      expect(trigger?.getAttribute("aria-expanded")).toBe("false");
      expect(document.activeElement).toBe(trigger);

      press("/");
      const search = document.querySelector<HTMLInputElement>("#brief-search");
      search?.focus();
      point(trigger as HTMLButtonElement, "pointerenter", "mouse");
      vi.advanceTimersByTime(200);
      pressAt(search as HTMLInputElement, "Escape");

      expect(trigger?.getAttribute("aria-expanded")).toBe("false");
      expect(document.activeElement).toBe(search);
      expect(document.querySelector("#brief-search")).not.toBeNull();

      pressAt(search as HTMLInputElement, "Escape");
      expect(document.querySelector("#brief-search")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("closes help before closing the updates log", () => {
    mount();
    const trigger = document.querySelector<HTMLButtonElement>(".map-trigger");
    trigger?.click();
    press("?");

    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(trigger?.getAttribute("aria-expanded")).toBe("true");

    press("Escape");

    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(trigger?.getAttribute("aria-expanded")).toBe("true");

    press("Escape");

    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
  });
});

describe("keyboard ownership", () => {
  it("numbers children before the active item's suggested replies", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ timestamp: "queued" }), { status: 200 }),
    );
    mount();

    press("1");
    expect(fetchSpy).not.toHaveBeenCalled();

    click(ALPHA);
    expect(paintedOpen(ALPHA)).toBe("false");
    press("1");
    expect(fetchSpy).not.toHaveBeenCalled();

    composeAt(ALPHA);
    press("1");
    expect(paintedCursor()).toBe(ANSWERED);
    expect(fetchSpy).not.toHaveBeenCalled();

    composeAt(ALPHA);
    press("2");
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(
      rowNode(ALPHA)?.querySelector(
        `[data-suggestion="${SAMPLE_SUGGESTIONS[0]?.message}"]`,
      )
        ?.getAttribute("aria-pressed"),
    ).toBe("true");

    composeAt(ANSWERED);
    press("2");
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(fetchSpy.mock.calls[1]?.[1]?.body))).toEqual({
      anchor_id: ALPHA,
      label: SAMPLE_SUGGESTIONS[1]?.label,
      text: SAMPLE_SUGGESTIONS[1]?.message,
    });

    document.dispatchEvent(new KeyboardEvent("keydown", {
      key: "3",
      shiftKey: true,
      bubbles: true,
    }));
    document.dispatchEvent(new KeyboardEvent("keydown", {
      key: "3",
      ctrlKey: true,
      bubbles: true,
    }));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    fetchSpy.mockRestore();
  });

  it("leaves numeric feedback inert while typing or using owned overlays",
    async () => {
      const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify({ timestamp: "queued" }), { status: 200 }),
      );
      mount();
      composeAt(ALPHA);
      await Promise.resolve();
      const textarea = document.querySelector(".composer textarea");
      expect(textarea).not.toBeNull();

      pressAt(textarea as Element, "1");
      expect(fetchSpy).not.toHaveBeenCalled();

      (textarea as HTMLElement).blur();
      press("?");
      press("2");
      expect(fetchSpy).not.toHaveBeenCalled();
      press("Escape");

      press("f");
      press("3");
      expect(fetchSpy).not.toHaveBeenCalled();
      fetchSpy.mockRestore();
    });

  it("leaves numbers beyond the offered replies available to the page", () => {
    const brief = sampleBrief();
    itemOf(brief, ALPHA).suggestions = SAMPLE_SUGGESTIONS.slice(0, 2);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ timestamp: "queued" }), { status: 200 }),
    );
    mount(brief);
    composeAt(ALPHA);

    press("4");
    expect(fetchSpy).not.toHaveBeenCalled();
    press("3");
    expect(fetchSpy).toHaveBeenCalledOnce();

    fetchSpy.mockRestore();
  });

  it(
    "opens the contextual composer with c on a revealed nonmatching row",
    () => {
      mount();
      press("/");
      typeInto("#brief-search", "reader agrees");
      press("m");
      click(BETA);

      press("c");

      const composer = document.querySelector(".composer");
      expect(composer?.closest("[data-row-id]")?.getAttribute("data-row-id"))
        .toBe(BETA);
      expect(composer?.getAttribute("data-anchor-id")).toBe(BETA);
      expect(paintedCursor()).toBe(BETA);
      expect(
        document.querySelector<HTMLInputElement>("#brief-search")?.value,
      ).toBe("reader agrees");
      expect(document.querySelector('[role="search"]')).not.toBeNull();
      expect(
        document.querySelector('[data-action="reveal-chats"]')?.getAttribute(
          "aria-pressed",
        ),
      ).toBe("true");
    },
  );

  it("removes stale button focus when a movement key moves the cursor", () => {
    mount();
    click(ALPHA);
    const stale = rowNode(ALPHA)?.querySelector(".row-toggle");
    expect(stale).toBeInstanceOf(HTMLElement);
    (stale as HTMLElement).focus();

    pressAt(stale as HTMLElement, "j");
    const moved = paintedCursor();
    const before = paintedOpen(moved ?? "");
    expect(document.activeElement).not.toBe(stale);
    press("Enter");

    expect(paintedOpen(moved ?? "")).not.toBe(before);
  });

  it("reveals the next outstanding chat after collapse-all hid it", () => {
    mount();
    press("C");
    press("n");

    expect(paintedCursor()).not.toBeNull();
    expect(rowNode(paintedCursor() ?? "")).not.toBeNull();
  });
});

describe("live document derivations", () => {
  it("keeps unchanged row nodes across apply", () => {
    const { publish } = mountLive();
    const ids = ["newest", "newest/changed", ALPHA, ANSWERED];
    const before = ids.map((id) => rowNode(id));
    const next = sampleBrief();
    itemOf(next, GAMMA).glance = "Only gamma changed";

    publish(next);

    ids.forEach((id, index) => expect(rowNode(id)).toBe(before[index]));
  });

  it("scopes masthead attention to the latest briefing and cycles its chats",
    () => {
    const brief = sampleBrief();
    const older = laneOf(brief, "older", "history").items[0];
    if (older === undefined) {
      throw new Error("sample lost the older item");
    }
    older.questions = [{
      id: "q-old-open",
      anchor: { kind: "element", path: "older/history/one" },
      turns: [{
        author: "human",
        text: "Does the older update still need work?",
        at: "2026-07-24T11:00:00Z",
      }],
    }];
    mount(brief);
    const attention = document.querySelector<HTMLButtonElement>(
      ".meta-attention",
    );

    expect(attention?.dataset.attentionCount).toBe("2");
    expect(attention?.textContent).toBe(
      "2 need attention in latest briefing",
    );
    expect(document.querySelector("[data-count]")).toBeNull();
    expect(document.querySelector("[data-awaiting-count]")).toBeNull();

    attention?.click();

    expect(paintedCursor()).toBe(ANSWERED);
    expect(attention?.dataset.attentionCount).toBe("1");
    expect(attention?.textContent).toBe(
      "1 needs attention in latest briefing",
    );
    expect(
      JSON.parse(
        window.sessionStorage.getItem(humanStorageKey("seen", "")) ?? "{}",
      ),
    ).toMatchObject({ "q-answered": "2:answered" });

    press("Escape");
    expect(paintedCursor()).toBe(ANSWERED);
    expect(paintedOpen(ANSWERED)).toBe("false");

    attention?.click();

    expect(paintedCursor()).toBe(`${BETA}#q-open`);
    expect(rowNode(`${BETA}#q-open`)).not.toBeNull();
  });

  it("omits latest attention while global n still finds an older chat", () => {
    const brief = sampleBrief();
    itemOf(brief, ALPHA).questions = [];
    itemOf(brief, BETA).questions = [];
    const older = laneOf(brief, "older", "history").items[0];
    if (older === undefined) {
      throw new Error("sample lost the older item");
    }
    older.questions = [{
      id: "q-old-open",
      anchor: { kind: "element", path: "older/history/one" },
      turns: [{
        author: "human",
        text: "Only the older update needs attention.",
        at: "2026-07-24T11:00:00Z",
      }],
    }];
    mount(brief);

    expect(document.querySelector(".meta-attention")).toBeNull();

    press("C");
    press("n");

    expect(paintedCursor()).toBe("older/history/one#q-old-open");
    expect(rowNode("older/history/one#q-old-open")).not.toBeNull();
  });

  it("keeps a readable label beside every always-visible shortcut", () => {
    mount();
    const controls = [...document.querySelectorAll(".key-control")];
    const labels = controls.map((control) =>
      control.querySelector("span")?.textContent?.trim() ?? ""
    );

    expect(controls.length).toBeGreaterThan(0);
    expect(labels.every((label) => label.length > 0)).toBe(true);
    expect(document.querySelector(".keybar-title")?.textContent).toBe(
      "Shortcuts",
    );
    expect(controls[0]?.textContent).toContain("↑↓ · j/kRows");
    expect(controls[1]?.textContent).toContain("⇧↑↓ · J/KLanes");
    expect(document.querySelector(".keybar")?.textContent).toContain(
      "fJump to row",
    );
  });

  it("restores surviving folds across a patch and defaults new rows", () => {
    const { publish } = mountLive();
    press("C");
    click("newest");
    click("newest/next");
    const before = new Map(paintedLayout());

    press("m");
    const next = sampleBrief();
    laneOf(next, "newest", "changed").items = laneOf(
      next,
      "newest",
      "changed",
    ).items.filter((item) => item.id !== "alpha");
    next.updates.find((update) => update.id === "newest")?.lanes.push({
      id: "patched",
      name: "Arrived in a patch",
      items: [],
    });
    publish(next);

    press("m");

    for (const [id, open] of before) {
      if (id !== ALPHA && id !== ANSWERED) {
        expect(rowNode(id)?.getAttribute("data-open")).toBe(open);
      }
    }
    expect(rowNode("newest/patched")?.getAttribute("data-open")).toBe("true");
    const chosen = JSON.parse(storedHumanState().chosen ?? "{}");
    expect(chosen).not.toHaveProperty(ALPHA);
    expect(chosen).not.toHaveProperty(ANSWERED);
  });
});

describe("pending presentation", () => {
  it("shows one real turn and one working sign until the folded turn arrives",
    async () => {
      let accept: ((response: Response) => void) | undefined;
      const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(
        () => new Promise<Response>((resolve) => {
          accept = resolve;
        }),
      );
      const { publish } = mountLive();
      composeAt(GAMMA);
      typeInto(".composer textarea", "Does this stay visible?");
      document.querySelector<HTMLButtonElement>(".composer .submit")?.click();

      const pending = rowNode(GAMMA)?.querySelector('[data-pending="true"]');
      expect(pending?.textContent).toContain("Does this stay visible?");
      expect(rowNode(GAMMA)?.querySelectorAll(":scope > .row-body > .working"))
        .toHaveLength(1);
      expect(rowNode(GAMMA)?.getAttribute("data-waiting")).toBe("direct");
      expect(rowNode("newest/next")?.getAttribute("data-waiting")).toBe(
        "contained",
      );
      expect(rowNode("newest")?.getAttribute("data-waiting")).toBe("contained");

      accept?.(new Response(JSON.stringify({
        timestamp: "2026-07-29T12:00:00Z",
      }), { status: 200 }));
      await vi.waitFor(() => {
        expect(pending?.querySelector("time")?.getAttribute("datetime")).toBe(
          "2026-07-29T12:00:00Z",
        );
      });
      expect(rowNode(GAMMA)?.querySelector('[data-pending="true"]')).toBe(
        pending,
      );

      const next = sampleBrief();
      itemOf(next, GAMMA).questions = [{
        id: "q-continuous",
        anchor: { kind: "element", path: GAMMA },
        turns: [{
          author: "human",
          text: "Does this stay visible?",
          at: "2026-07-29T12:00:00Z",
        }],
      }];
      publish(next);

      expect(rowNode(GAMMA)?.querySelector('[data-pending="true"]')).toBeNull();
      expect(
        rowNode(`${GAMMA}#q-continuous`)?.textContent,
      ).toContain("Does this stay visible?");
      expect(
        rowNode(`${GAMMA}#q-continuous`)
          ?.querySelectorAll(":scope > .row-body > .working"),
      ).toHaveLength(1);
      fetchSpy.mockRestore();
  });
});

describe("prose rendering", () => {
  it("renders the page summary through the audited Markdown component", () => {
    const brief = sampleBrief();
    brief.summary = "A **strong** summary.";
    mount(brief);

    expect(document.querySelector(".brief-summary strong")?.textContent).toBe(
      "strong",
    );
  });

});
