import { describe, expect, it } from "vitest";

import {
  click,
  composeAt,
  mount,
  mountLive,
  paintedCursor,
  paintedOpen,
  press,
  rowNode,
  typeInto,
  unmount,
  useHarness,
} from "../test/harness";
import { sampleBrief } from "../test/sample-brief";
import type {
  BriefDocument,
  LegacyCurrentState,
  StructuredCurrentState,
} from "./document";
import { humanStorageKey } from "./human-state";
import {
  CURRENT_STATE_ROOT_ID,
} from "./outline";
import { saveSentRecords } from "./session-store";

const ROOT = CURRENT_STATE_ROOT_ID;
const ACTIVE = `${ROOT}/lanes/active`;
const NEXT = `${ROOT}/lanes/next`;
const PARSER = `${ROOT}/items/parser`;
const ROOT_CHAT = `${ROOT}#q-state-root`;
const LANE_CHAT = `${ACTIVE}#q-state-lane`;
const ITEM_CHAT = `${PARSER}#q-state-item`;
const STAMP = "2026-08-01T12:00:00Z";

const LEGACY_STATE: LegacyCurrentState = {
  updated_at: STAMP,
  goal: "Give the reader one calm account of the work.",
  focus: "The publishing contract is being implemented now.",
  blocker: null,
  next: "Verify the contract against an already open page.",
};

const STATE: StructuredCurrentState = {
  updated_at: STAMP,
  headline: "The detailed current snapshot is active",
  summary: "Every important detail is individually addressable.",
  questions: [
    {
      id: "q-state-root",
      anchor: { kind: "element", path: ROOT },
      turns: [
        { author: "human", text: "What remains at the root?", at: STAMP },
      ],
    },
  ],
  lanes: [
    {
      id: "active",
      name: "What works now",
      questions: [
        {
          id: "q-state-lane",
          anchor: { kind: "element", path: ACTIVE },
          turns: [
            { author: "human", text: "Why this lane?", at: STAMP },
            {
              author: "agent",
              text: "It contains the currently working pieces.",
              at: "2026-08-01T12:01:00Z",
            },
          ],
        },
      ],
      items: [
        {
          id: "parser",
          glance: "The structured state parser is working.",
          explanation: "The parser shares the dated-update content model.",
          trust: "verified-by-me",
          forensics: [
            {
              id: "contract",
              title: "Contract check",
              body: "The stable anchor survived validation.",
            },
          ],
          questions: [
            {
              id: "q-state-item",
              anchor: { kind: "element", path: PARSER },
              turns: [
                { author: "human", text: "Can this item move?", at: STAMP },
                {
                  author: "agent",
                  text: "Yes. Its anchor does not include the lane id.",
                  at: "2026-08-01T12:01:00Z",
                },
              ],
            },
          ],
        },
      ],
    },
    {
      id: "next",
      name: "What comes next",
      items: [
        {
          id: "review",
          glance: "The final review remains outstanding.",
          explanation: "The completed tree still needs a cold review.",
          trust: "reported-by-agent",
        },
      ],
    },
  ],
};

useHarness();

function withState(
  current: StructuredCurrentState | LegacyCurrentState = STATE,
): BriefDocument {
  const brief = sampleBrief();
  brief.current_state = structuredClone(current);
  return brief;
}

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

describe("Current state compatibility", () => {
  it("renders nothing when absent and keeps legacy state read-only", () => {
    mount();
    expect(document.querySelector(".row-state")).toBeNull();
    expect(document.querySelector(".current-state-legacy")).toBeNull();
    unmount();

    mount(withState(LEGACY_STATE));

    const section = document.querySelector(".current-state-legacy");
    expect(section?.textContent).toContain("Goal");
    expect(section?.textContent).toContain("Working now");
    expect(section?.textContent).toContain("Next");
    expect(section?.textContent).not.toContain("Blocked");
    expect(section?.closest("[data-row-id]")).toBeNull();
    expect(section?.querySelector(".chat-button")).toBeNull();
    expect(
      section?.compareDocumentPosition(rowNode("newest") as Node)
        ?? Node.DOCUMENT_POSITION_PRECEDING,
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });
});

describe("Detailed current-state rows", () => {
  it("uses shared rows, chats, evidence, counts, and map navigation", () => {
    mount(withState());
    press("E");

    expect(rowNode(ROOT)?.getAttribute("data-row-kind")).toBe("state");
    expect(rowNode(ACTIVE)?.getAttribute("data-row-kind")).toBe("lane");
    expect(rowNode(PARSER)?.getAttribute("data-row-kind")).toBe("item");
    expect(
      rowNode(ACTIVE)?.parentElement?.classList.contains(
        "current-state-lanes",
      ),
    ).toBe(true);
    expect(rowNode(ROOT_CHAT)?.getAttribute("data-row-kind")).toBe("thread");
    expect(rowNode(LANE_CHAT)).not.toBeNull();
    expect(rowNode(ITEM_CHAT)).not.toBeNull();
    expect(rowNode(`${PARSER}#~evidence`)).not.toBeNull();
    expect(rowNode(`${PARSER}#~evidence#~contract`)).not.toBeNull();
    expect(rowNode(ROOT)?.compareDocumentPosition(rowNode("newest") as Node))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(rowNode(ROOT)?.querySelector(".chat-button")).not.toBeNull();
    expect(rowNode(ACTIVE)?.querySelector(".chat-button")).not.toBeNull();
    expect(rowNode(PARSER)?.querySelector(".chat-button")).not.toBeNull();
    expect(document.querySelector(`[data-map-lane="${ACTIVE}"]`)).not.toBeNull();
    expect(document.querySelector('[data-count="lanes"] b')?.textContent)
      .toBe("5");
    expect(document.querySelector('[data-count="items"] b')?.textContent)
      .toBe("6");
    expect(document.querySelector('[data-awaiting-count] b')?.textContent)
      .toBe("2");

    press("J");
    expect(paintedCursor()).toBe(ACTIVE);
    press("j");
    expect(paintedCursor()).toBe(LANE_CHAT);
    click(PARSER);
    press("c");
    expect(
      document.querySelector(".composer")?.getAttribute("data-anchor-id"),
    ).toBe(PARSER);
  });

  it("searches and reveals state chats through the ordinary controls", () => {
    mount(withState());
    press("C");
    expect(rowNode(PARSER)).toBeNull();

    press("/");
    typeInto("#brief-search", "stable anchor survived");
    expect(rowNode(PARSER)).not.toBeNull();
    expect(rowNode(`${PARSER}#~evidence`)).not.toBeNull();

    press("m");
    expect(rowNode(ROOT_CHAT)).not.toBeNull();
    expect(rowNode(LANE_CHAT)).not.toBeNull();
    expect(rowNode(ITEM_CHAT)).not.toBeNull();
    expect(
      document.querySelector('[data-action="reveal-chats"]')?.getAttribute(
        "aria-pressed",
      ),
    ).toBe("true");
    press("m");
    expect(document.querySelector<HTMLInputElement>("#brief-search")?.value)
      .toBe("stable anchor survived");
  });

  it("marks a newly answered state conversation until it is visited", () => {
    const initial = withState();
    const { publish } = mountLive(initial);
    expect(rowNode(ROOT_CHAT)?.getAttribute("data-awaiting")).toBe("true");

    const next = structuredClone(initial);
    const current = next.current_state;
    if (current === undefined || !("lanes" in current)) {
      throw new Error("fixture lost structured state");
    }
    current.questions?.[0]?.turns.push({
      author: "agent",
      text: "Only final verification remains.",
      at: "2026-08-01T13:00:00Z",
    });
    publish(next);

    expect(rowNode(ROOT_CHAT)?.getAttribute("data-fresh")).toBe("true");
    expect(rowNode(ROOT_CHAT)?.querySelector(".chip-new")?.textContent)
      .toContain("New answer");
    click(ROOT_CHAT);
    expect(rowNode(ROOT_CHAT)?.getAttribute("data-fresh")).toBe("false");
  });
});

describe("Live detailed-state publishing", () => {
  it("retires a pending state note when its folded conversation arrives", () => {
    const text = "This state question is still being folded";
    saveSentRecords([{
      rowId: PARSER,
      anchorId: PARSER,
      text,
      at: "2026-08-01T12:05:00Z",
    }]);
    const initial = withState();
    const { publish } = mountLive(initial);
    press("E");
    expect(rowNode(PARSER)?.querySelector('[data-pending="true"]'))
      .not.toBeNull();

    const next = structuredClone(initial);
    const current = next.current_state;
    if (current === undefined || !("lanes" in current)) {
      throw new Error("fixture lost structured state");
    }
    current.lanes[0]?.items[0]?.questions?.push({
      id: "q-state-pending",
      anchor: { kind: "element", path: PARSER },
      turns: [
        { author: "human", text, at: "2026-08-01T12:05:00Z" },
      ],
    });
    publish(next);

    expect(rowNode(PARSER)?.querySelector('[data-pending="true"]')).toBeNull();
    expect(rowNode(`${PARSER}#q-state-pending`)?.querySelector(".working"))
      .not.toBeNull();
  });

  it("patches in place while preserving every human-owned state", () => {
    saveSentRecords([{
      rowId: PARSER,
      anchorId: PARSER,
      text: "Keep waiting for this answer",
      at: "2026-08-01T12:05:00Z",
    }]);
    const initial = withState();
    const { publish } = mountLive(initial);
    press("E");
    click(NEXT);
    const nextFold = paintedOpen(NEXT);
    composeAt(PARSER);
    typeInto(".composer textarea", "Keep this unfinished state question");
    press("m");
    press("/");
    typeInto("#brief-search", "parser");
    const nodes = new Map([
      [ROOT, rowNode(ROOT)],
      [ACTIVE, rowNode(ACTIVE)],
      [PARSER, rowNode(PARSER)],
      [ITEM_CHAT, rowNode(ITEM_CHAT)],
      ["newest/changed/alpha", rowNode("newest/changed/alpha")],
    ]);
    const pending = rowNode(PARSER)?.querySelector('[data-pending="true"]');
    const human = storedHumanState();
    const cursor = paintedCursor();

    const next = structuredClone(initial);
    const current = next.current_state;
    if (current === undefined || !("lanes" in current)) {
      throw new Error("fixture lost structured state");
    }
    current.headline = "The detailed current snapshot is under verification";
    current.summary = "The same rows now carry the newest verified details.";
    current.updated_at = "2026-08-01T13:00:00Z";
    current.lanes[0]?.items.push({
      id: "embedding",
      glance: "The embedded state remains valid.",
      explanation: "The live patch delivered the additional item.",
      trust: "verified-by-me",
    });
    next.updates.push({
      id: "state-verified",
      timestamp: "2026-08-01T13:00:00Z",
      headline: "The detailed state reached verification",
      summary: "State and history arrived in the same live patch.",
      lanes: [],
    });
    publish(next);

    for (const [id, node] of nodes) {
      expect(rowNode(id), id).toBe(node);
    }
    expect(storedHumanState()).toEqual(human);
    expect(paintedCursor()).toBe(cursor);
    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("Keep this unfinished state question");
    expect(document.querySelector<HTMLInputElement>("#brief-search")?.value)
      .toBe("parser");
    expect(
      document.querySelector('[data-action="reveal-chats"]')?.getAttribute(
        "aria-pressed",
      ),
    ).toBe("true");
    expect(rowNode(PARSER)?.querySelector('[data-pending="true"]')).toBe(
      pending,
    );
    expect(rowNode(PARSER)?.querySelector(".working")).not.toBeNull();
    press("Escape");
    expect(paintedOpen(NEXT)).toBe(nextFold);
    expect(rowNode(`${ROOT}/items/embedding`)).not.toBeNull();
    expect(rowNode("state-verified")).not.toBeNull();
  });

  it("preserves focused composer selection when its item changes lanes",
    async () => {
    const initial = withState();
    const { publish } = mountLive(initial);
    press("E");
    click(NEXT);
    expect(paintedOpen(NEXT)).toBe("false");
    composeAt(PARSER);
    const draft = "This draft follows the item between lanes";
    typeInto(".composer textarea", draft);
    await Promise.resolve();
    const textarea = document.querySelector<HTMLTextAreaElement>(
      ".composer textarea",
    );
    textarea?.focus();
    textarea?.setSelectionRange(5, 26, "backward");
    const next = structuredClone(initial);
    const current = next.current_state;
    if (current === undefined || !("lanes" in current)) {
      throw new Error("fixture lost structured state");
    }
    const parser = current.lanes[0]?.items.shift();
    if (parser === undefined) {
      throw new Error("fixture lost parser item");
    }
    current.lanes[1]?.items.push(parser);
    publish(next);
    await Promise.resolve();

    const replacement = document.querySelector<HTMLTextAreaElement>(
      ".composer textarea",
    );
    expect(paintedOpen(NEXT)).toBe("true");
    expect(rowNode(PARSER)?.parentElement?.closest("[data-row-id]")?.getAttribute(
      "data-row-id",
    )).toBe(NEXT);
    expect(paintedCursor()).toBe(PARSER);
    expect(replacement).not.toBe(textarea);
    expect(document.activeElement).toBe(replacement);
    expect(replacement?.value).toBe(draft);
    expect(replacement?.selectionStart).toBe(5);
    expect(replacement?.selectionEnd).toBe(26);
    expect(replacement?.selectionDirection).toBe("backward");
    expect(
      document.querySelector(".composer")?.getAttribute("data-anchor-id"),
    ).toBe(PARSER);
  });

  it.each([
    ["row toggle", ".row-toggle"],
    ["Chat button", ".chat-button"],
  ])("restores focus to a moved item's %s", async (_name, selector) => {
    const initial = withState();
    const { publish } = mountLive(initial);
    press("E");
    const oldRow = rowNode(PARSER);
    const oldControl = oldRow?.querySelector<HTMLElement>(selector);
    oldControl?.focus();

    const next = structuredClone(initial);
    const current = next.current_state;
    if (current === undefined || !("lanes" in current)) {
      throw new Error("fixture lost structured state");
    }
    const parser = current.lanes[0]?.items.shift();
    if (parser === undefined) {
      throw new Error("fixture lost parser item");
    }
    current.lanes[1]?.items.push(parser);
    publish(next);
    await Promise.resolve();

    const newRow = rowNode(PARSER);
    const newControl = newRow?.querySelector<HTMLElement>(selector);
    expect(newRow).not.toBe(oldRow);
    expect(oldControl?.isConnected).toBe(false);
    expect(newControl).not.toBe(oldControl);
    expect(document.activeElement).toBe(newControl);
  });

  it("keeps a moved composer visible when chat reveal restores", async () => {
    const initial = withState();
    const { publish } = mountLive(initial);
    press("E");
    click(NEXT);
    click("older");
    expect(paintedOpen(NEXT)).toBe("false");
    expect(paintedOpen("older")).toBe("false");
    composeAt(PARSER);
    const draft = "Keep this selected draft visible after chat reveal";
    typeInto(".composer textarea", draft);
    await Promise.resolve();
    const textarea = document.querySelector<HTMLTextAreaElement>(
      ".composer textarea",
    );
    textarea?.focus();
    textarea?.setSelectionRange(5, 24, "backward");
    press("m");

    const next = structuredClone(initial);
    const current = next.current_state;
    if (current === undefined || !("lanes" in current)) {
      throw new Error("fixture lost structured state");
    }
    const parser = current.lanes[0]?.items.shift();
    if (parser === undefined) {
      throw new Error("fixture lost parser item");
    }
    current.lanes[1]?.items.push(parser);
    publish(next);
    await Promise.resolve();
    press("m");
    await Promise.resolve();

    const replacement = document.querySelector<HTMLTextAreaElement>(
      ".composer textarea",
    );
    expect(paintedOpen(NEXT)).toBe("true");
    expect(paintedOpen("older")).toBe("false");
    expect(rowNode(PARSER)).not.toBeNull();
    expect(paintedCursor()).toBe(PARSER);
    expect(document.activeElement).toBe(replacement);
    expect(replacement?.value).toBe(draft);
    expect(replacement?.selectionStart).toBe(5);
    expect(replacement?.selectionEnd).toBe(24);
    expect(replacement?.selectionDirection).toBe("backward");
    expect(
      document.querySelector(".composer")?.getAttribute("data-anchor-id"),
    ).toBe(PARSER);
  });

  it("restores exact folds after ordinary composing during chat reveal", () => {
    mountLive(withState());
    press("E");
    click(ACTIVE);
    click(NEXT);
    click("older");
    const layout = paintedLayout();
    expect(paintedOpen(ACTIVE)).toBe("false");
    expect(paintedOpen(NEXT)).toBe("false");
    expect(paintedOpen("older")).toBe("false");

    press("m");
    composeAt(PARSER);
    expect(document.querySelector(".composer")).not.toBeNull();
    press("m");

    expect(paintedLayout()).toEqual(layout);
  });

  it("does not focus an unfocused composer when its item changes lanes",
    async () => {
    const initial = withState();
    const { publish } = mountLive(initial);
    press("E");
    composeAt(PARSER);
    typeInto(".composer textarea", "Keep this unfocused draft");
    await Promise.resolve();
    const stableButton = rowNode(ROOT)?.querySelector<HTMLButtonElement>(
      ".chat-button",
    );
    stableButton?.focus();

    const next = structuredClone(initial);
    const current = next.current_state;
    if (current === undefined || !("lanes" in current)) {
      throw new Error("fixture lost structured state");
    }
    const parser = current.lanes[0]?.items.shift();
    if (parser === undefined) {
      throw new Error("fixture lost parser item");
    }
    current.lanes[1]?.items.push(parser);
    publish(next);
    await Promise.resolve();

    expect(document.activeElement).toBe(stableButton);
    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("Keep this unfocused draft");
  });

  it("keeps a searched move destination open with its composer", async () => {
    const initial = withState();
    const { publish } = mountLive(initial);
    press("E");
    click(NEXT);
    expect(paintedOpen(NEXT)).toBe("false");
    composeAt(PARSER);
    typeInto(".composer textarea", "This searched draft follows the item");
    press("/");
    typeInto("#brief-search", "parser");

    const next = structuredClone(initial);
    const current = next.current_state;
    if (current === undefined || !("lanes" in current)) {
      throw new Error("fixture lost structured state");
    }
    const parser = current.lanes[0]?.items.shift();
    if (parser === undefined) {
      throw new Error("fixture lost parser item");
    }
    current.lanes[1]?.items.push(parser);
    publish(next);
    await Promise.resolve();

    press("Escape");

    expect(paintedOpen(NEXT)).toBe("true");
    expect(paintedCursor()).toBe(PARSER);
    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("This searched draft follows the item");
    expect(document.querySelector(".composer")).not.toBeNull();
    expect(
      document.querySelector(".composer")?.getAttribute("data-anchor-id"),
    ).toBe(PARSER);
  });

  it("keeps a searched move destination open for the selected row", async () => {
    const initial = withState();
    const { publish } = mountLive(initial);
    press("E");
    click(NEXT);
    expect(paintedOpen(NEXT)).toBe("false");
    click(PARSER);
    expect(document.querySelector(".composer")).toBeNull();
    press("/");
    typeInto("#brief-search", "parser");

    const next = structuredClone(initial);
    const current = next.current_state;
    if (current === undefined || !("lanes" in current)) {
      throw new Error("fixture lost structured state");
    }
    const parser = current.lanes[0]?.items.shift();
    if (parser === undefined) {
      throw new Error("fixture lost parser item");
    }
    current.lanes[1]?.items.push(parser);
    publish(next);
    await Promise.resolve();

    press("Escape");

    expect(paintedOpen(NEXT)).toBe("true");
    expect(paintedCursor()).toBe(PARSER);
    expect(rowNode(PARSER)?.parentElement?.closest("[data-row-id]")?.getAttribute(
      "data-row-id",
    )).toBe(NEXT);
    expect(document.querySelector(".composer")).toBeNull();
  });

  it("closes on manual ancestor collapse but keeps the saved draft", async () => {
    mountLive(withState());
    press("E");
    composeAt(PARSER);
    typeInto(".composer textarea", "This draft survives a manual fold");

    click(ACTIVE);
    await Promise.resolve();

    expect(document.querySelector(".composer")).toBeNull();
    click(ACTIVE);
    composeAt(PARSER);
    expect(
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.value,
    ).toBe("This draft survives a manual fold");
  });

  it("closes the composer when its item is truly removed", async () => {
    const initial = withState();
    const { publish } = mountLive(initial);
    press("E");
    composeAt(PARSER);
    typeInto(".composer textarea", "This draft belongs to a removed item");
    const next = structuredClone(initial);
    const current = next.current_state;
    if (current === undefined || !("lanes" in current)) {
      throw new Error("fixture lost structured state");
    }
    current.lanes[0]?.items.shift();

    publish(next);
    await Promise.resolve();

    expect(document.querySelector(".composer")).toBeNull();
  });
});
