import { describe, expect, it, vi } from "vitest";

import { humanStorageKey } from "./human-state";
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
import { itemOf, sampleBrief } from "../test/sample-brief";

const ALPHA = "newest/changed/alpha";
const ANSWERED = `${ALPHA}#q-answered`;
const BETA = "newest/changed/beta";
const GAMMA = "newest/next/gamma";

useHarness();

function storedHumanState(): Record<string, string | null> {
  return Object.fromEntries(
    (["chosen", "cursor", "drafts", "seen"] as const).map((part) => [
      part,
      window.sessionStorage.getItem(humanStorageKey(part, "")),
    ]),
  );
}

describe("non-human events cannot write human state", () => {
  it("preserves all four maps across publish, filters, views, and unmount", () => {
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
    document.querySelector<HTMLButtonElement>(".meta-chats")?.click();
    document.querySelector<HTMLButtonElement>(".meta-chats")?.click();

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
    document.querySelector<HTMLButtonElement>(".meta-chats")?.click();
    expect(
      document.querySelector<HTMLInputElement>("#brief-search")?.value,
    ).toBe("reference parser");
    document.querySelector<HTMLButtonElement>(".meta-chats")?.click();
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

  it("leaves My Chats before touching a composer hidden by the view", () => {
    mount();
    composeAt(GAMMA);
    typeInto(".composer textarea", "Keep this hidden draft");
    press("m");
    expect(document.querySelector(".composer")).toBeNull();

    press("Escape");

    expect(document.querySelector(".meta-chats")?.getAttribute("aria-pressed"))
      .toBe("false");
    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("Keep this hidden draft");
  });
});

describe("keyboard ownership", () => {
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

  it("counts unseen and awaiting chats, then removes only a visited answer", () => {
    mount();
    const badge = document.querySelector<HTMLElement>(".meta-chats");
    expect(badge?.dataset.chatsCount).toBe("2");

    click(ANSWERED);

    expect(badge?.dataset.chatsCount).toBe("1");
    expect(
      JSON.parse(
        window.sessionStorage.getItem(humanStorageKey("seen", "")) ?? "{}",
      ),
    ).toMatchObject({ "q-answered": "2:answered" });
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
        expect(pending?.querySelector("time")?.textContent).toBe(
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
