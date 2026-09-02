import { afterEach, describe, expect, it, vi } from "vitest";

import {
  press,
  typeInto,
  useHarness,
  mount,
  mountLive,
} from "../test/harness";
import { sampleBrief } from "../test/sample-brief";

useHarness();

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

describe("the page-level agent message", () => {
  it("opens above the briefings from both its button and the a key", () => {
    mount();
    const control = document.querySelector<HTMLButtonElement>(
      ".global-message-button",
    );
    const latest = document.querySelector(".latest-briefing");

    expect(control?.textContent).toContain("Message agent");
    expect(
      control?.compareDocumentPosition(latest ?? document.body)
        ?? Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);

    press("a");
    expect(document.querySelector(".global-message .composer-label")
      ?.textContent).toBe("Message the agent");
    expect(document.querySelector(".global-message .composer")
      ?.getAttribute("data-anchor-id")).toBe("newest");

    press("Escape");
    expect(document.querySelector(".global-message .composer")).toBeNull();
    control?.click();
    expect(document.querySelector(".global-message .composer")).not.toBeNull();
  });

  it("sends against the latest briefing and keeps the working sign visible", async () => {
    let payload: unknown;
    globalThis.fetch = vi.fn(async (_path, init) => {
      payload = JSON.parse(String(init?.body)) as unknown;
      return new Response(
        JSON.stringify({ timestamp: "2026-08-05T20:00:00Z" }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof globalThis.fetch;
    mount();
    document.querySelector<HTMLButtonElement>(".global-message-button")
      ?.click();
    typeInto(
      ".global-message .composer textarea",
      "Please investigate the failing build",
    );

    document.querySelector<HTMLFormElement>(".global-message .composer")
      ?.dispatchEvent(new SubmitEvent("submit", { bubbles: true }));

    await vi.waitFor(() => {
      expect(payload).toEqual({
        anchor_id: "newest",
        text: "Please investigate the failing build",
      });
    });
    expect(document.querySelector(".global-message .turn-human")
      ?.textContent).toContain("Please investigate the failing build");
    expect(document.querySelector(".global-message .working")
      ?.textContent).toContain("agent is working");
    expect(document.querySelector(".global-message .composer")).not.toBeNull();
    expect(document.querySelector<HTMLTextAreaElement>(
      ".global-message .composer textarea",
    )?.value).toBe("");
    expect(document.querySelector(".global-message-button")
      ?.getAttribute("aria-expanded")).toBe("true");
  });

  it("keeps the answered conversation in the global message area", async () => {
    globalThis.fetch = vi.fn(async () => new Response(
      JSON.stringify({ timestamp: "2026-08-05T20:00:00Z" }),
      { status: 202, headers: { "Content-Type": "application/json" } },
    )) as typeof globalThis.fetch;
    const initial = sampleBrief();
    const initialLatest = initial.updates.at(-1);
    if (initialLatest === undefined) {
      throw new Error("the sample brief lost its latest update");
    }
    initialLatest.questions = [{
      id: "q-older-global",
      anchor: { kind: "element", path: initialLatest.id },
      turns: [
        {
          author: "human",
          text: "An older direct message",
          at: "2026-08-05T19:00:00Z",
        },
        {
          author: "agent",
          text: "An older direct answer",
          at: "2026-08-05T19:00:01Z",
        },
      ],
    }];
    const { publish } = mountLive(initial);
    const briefingWorkingBefore = document.querySelectorAll(
      ".latest-briefing .working",
    ).length;
    const olderThread = document.querySelector(
      '[data-row-id="newest#q-older-global"]',
    );
    press("a");
    typeInto(".global-message .composer textarea", "What is seven? ");
    document.querySelector<HTMLFormElement>(".global-message .composer")
      ?.dispatchEvent(new SubmitEvent("submit", { bubbles: true }));
    await vi.waitFor(() => {
      expect(document.querySelector(".global-message .working")).not.toBeNull();
      expect(document.querySelector(".global-message .turn-human time")
        ?.getAttribute("datetime")).toBe("2026-08-05T20:00:00Z");
    });
    const pendingTurn = document.querySelector(
      ".global-message > .turn-human",
    );
    const pendingWorking = document.querySelector(
      ".global-message > .working",
    );
    expect(pendingTurn?.compareDocumentPosition(olderThread ?? document.body))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(
      pendingWorking?.compareDocumentPosition(olderThread ?? document.body),
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);

    const folded = structuredClone(initial);
    const latest = folded.updates.at(-1);
    if (latest === undefined) {
      throw new Error("the sample brief lost its latest update");
    }
    latest.questions = [...(latest.questions ?? []), {
      id: "q-global",
      anchor: { kind: "element", path: latest.id },
      turns: [
        {
          author: "human",
          text: "What is seven?",
          at: "2026-08-05T20:00:00Z",
        },
      ],
    }];
    publish(folded);

    await vi.waitFor(() => {
      expect(document.querySelector(".global-message .row-thread"))
        .not.toBeNull();
      expect(document.querySelector(".global-message .working")).not.toBeNull();
    });
    expect(document.querySelectorAll(".latest-briefing .working"))
      .toHaveLength(briefingWorkingBefore);
    expect(document.querySelectorAll(".global-message .working"))
      .toHaveLength(1);
    expect(document.querySelector(".global-message .working"))
      .toBe(pendingWorking);
    const waitingThread = document.querySelector(
      '[data-row-id="newest#q-global"]',
    );
    expect(waitingThread?.querySelector(".working")).toBeNull();
    expect(waitingThread?.compareDocumentPosition(olderThread ?? document.body))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING);

    const answered = structuredClone(folded);
    answered.updates.at(-1)?.questions
      ?.find((thread) => thread.id === "q-global")
      ?.turns.push({
      author: "agent",
      text: "It is seven.",
      at: "2026-08-05T20:00:01Z",
    });
    publish(answered);

    await vi.waitFor(() => {
      expect(document.querySelector(
        '[data-row-id="newest#q-global"] .turn-human',
      )
        ?.textContent).toContain("What is seven?");
      expect(document.querySelector(
        '[data-row-id="newest#q-global"] .turn-agent',
      )
        ?.textContent).toContain("It is seven.");
    });
    expect(document.querySelector(".global-message .working")).toBeNull();
    const globalThread = document.querySelector(
      '[data-row-id="newest#q-global"]',
    );
    expect(globalThread).toBe(waitingThread);
    expect(globalThread?.closest(".global-message")).not.toBeNull();
    expect(document.querySelectorAll('[data-row-id="newest#q-global"]'))
      .toHaveLength(1);
  });

  it("uses current state when the brief has no updates", () => {
    const empty = sampleBrief();
    empty.updates = [];
    empty.current_state = {
      updated_at: "2026-08-05T20:00:00Z",
      headline: "The current state remains available",
      summary: "There is not yet a briefing in the ledger.",
      lanes: [],
    };

    mount(empty);
    press("a");

    expect(document.querySelector(".global-message .composer")
      ?.getAttribute("data-anchor-id")).toBe("//current-state");
  });

  it("stays hidden when a legacy read-only state has no briefing", () => {
    const legacy = sampleBrief();
    legacy.updates = [];
    legacy.current_state = {
      updated_at: "2026-08-05T20:00:00Z",
      goal: "Keep the old document readable.",
      focus: "Display its legacy state.",
      blocker: null,
      next: "Publish a modern briefing.",
    };

    mount(legacy);
    press("a");

    expect(document.querySelector(".global-message-button")).toBeNull();
    expect(document.querySelector(".global-message .composer")).toBeNull();
  });

  it("keeps a global draft but retargets it to a newly published briefing", async () => {
    let payload: unknown;
    globalThis.fetch = vi.fn(async (_path, init) => {
      payload = JSON.parse(String(init?.body)) as unknown;
      return new Response(
        JSON.stringify({ timestamp: "2026-08-05T20:00:00Z" }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof globalThis.fetch;
    const { publish } = mountLive();
    press("a");
    typeInto(".global-message .composer textarea", "Use the latest context");
    const textarea = document.querySelector<HTMLTextAreaElement>(
      ".global-message .composer textarea",
    );
    const next = sampleBrief();
    next.updates.push({
      id: "after-draft",
      timestamp: "2026-08-05T20:00:00Z",
      headline: "A newer briefing arrived",
      summary: "The open page-level message should follow it.",
      lanes: [],
    });

    publish(next);

    expect(document.querySelector(".global-message .composer textarea"))
      .toBe(textarea);
    expect(textarea?.value).toBe("Use the latest context");
    expect(document.querySelector(".global-message .composer")
      ?.getAttribute("data-anchor-id")).toBe("after-draft");

    document.querySelector<HTMLFormElement>(".global-message .composer")
      ?.dispatchEvent(new SubmitEvent("submit", { bubbles: true }));
    await vi.waitFor(() => {
      expect(payload).toEqual({
        anchor_id: "after-draft",
        text: "Use the latest context",
      });
    });
  });

  it("keeps a failed retry attached to its original briefing", async () => {
    const payloads: unknown[] = [];
    globalThis.fetch = vi.fn(async (_path, init) => {
      payloads.push(JSON.parse(String(init?.body)) as unknown);
      if (payloads.length === 1) {
        return new Response("not accepted", { status: 503 });
      }
      return new Response(
        JSON.stringify({ timestamp: "2026-08-05T20:00:00Z" }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof globalThis.fetch;
    const { publish } = mountLive();
    press("a");
    typeInto(".global-message .composer textarea", "Retry this exact message");
    document.querySelector<HTMLFormElement>(".global-message .composer")
      ?.dispatchEvent(new SubmitEvent("submit", { bubbles: true }));
    await vi.waitFor(() => {
      expect(document.querySelector(".global-message .status")?.textContent)
        .toContain("Could not send");
    });

    const next = sampleBrief();
    next.updates.push({
      id: "after-failure",
      timestamp: "2026-08-05T20:00:00Z",
      headline: "A newer briefing arrived after the failure",
      summary: "The retry still belongs to its original context.",
      lanes: [],
    });
    publish(next);

    expect(document.querySelector(".global-message .composer")
      ?.getAttribute("data-anchor-id")).toBe("newest");
    document.querySelector<HTMLFormElement>(".global-message .composer")
      ?.dispatchEvent(new SubmitEvent("submit", { bubbles: true }));
    await vi.waitFor(() => {
      expect(payloads).toEqual([
        { anchor_id: "newest", text: "Retry this exact message" },
        { anchor_id: "newest", text: "Retry this exact message" },
      ]);
    });
  });
});
