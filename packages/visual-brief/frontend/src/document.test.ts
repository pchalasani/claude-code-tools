import { describe, expect, it } from "vitest";

import {
  DOCUMENT_SCRIPT_ID,
  describeShape,
  readEmbeddedDocument,
  type BriefDocument,
} from "./document";

const BRIEF: BriefDocument = {
  title: "Toolchain",
  summary: "One bundle, one stylesheet, one page.",
  updates: [
    {
      id: "u1",
      timestamp: "2026-07-25T19:00:00Z",
      headline: "First",
      summary: "First update",
      lanes: [
        {
          id: "l1",
          name: "What changed",
          items: [
            {
              id: "i1",
              glance: "A thing happened",
              explanation: "Longer explanation",
              trust: "verified-by-me",
              questions: [
                {
                  id: "q-1",
                  anchor: { kind: "element", path: "u1/l1/i1" },
                  turns: [
                    { author: "human", text: "Why?", at: "2026-07-25T19:01:00Z" },
                  ],
                },
              ],
            },
          ],
          questions: [
            {
              id: "q-2",
              anchor: { kind: "element", path: "u1/l1" },
              turns: [
                { author: "human", text: "Lane?", at: "2026-07-25T19:02:00Z" },
              ],
            },
          ],
        },
      ],
    },
  ],
};

const STATE = {
  updated_at: "2026-08-01T12:00:00Z",
  headline: "The detailed state is active",
  summary: "Embedded structured documents remain valid.",
  lanes: [],
};

const LEGACY_STATE = {
  updated_at: "2026-08-01T12:00:00Z",
  goal: "Ship the brief.",
  focus: "Validate embedded documents.",
  blocker: null,
  next: "Run the checks.",
};

function pageWith(json: string): Document {
  const page = document.implementation.createHTMLDocument("test");
  const holder = page.createElement("script");
  holder.type = "application/json";
  holder.id = DOCUMENT_SCRIPT_ID;
  holder.textContent = json;
  page.body.append(holder);
  return page;
}

describe("readEmbeddedDocument", () => {
  it("parses the embedded blob", () => {
    const parsed = readEmbeddedDocument(pageWith(JSON.stringify(BRIEF)));

    expect(parsed.title).toBe("Toolchain");
    expect(parsed.updates[0]?.lanes[0]?.items[0]?.glance).toBe(
      "A thing happened",
    );
  });

  it("keeps escaped markup as inert text", () => {
    const hostile: BriefDocument = structuredClone(BRIEF);
    const turn = hostile.updates[0]?.lanes[0]?.items[0]?.questions?.[0]
      ?.turns[0];
    if (turn === undefined) {
      throw new Error("fixture lost its turn");
    }
    turn.text = "<script>alert(1)</" + "script>";

    const parsed = readEmbeddedDocument(pageWith(JSON.stringify(hostile)));

    expect(
      parsed.updates[0]?.lanes[0]?.items[0]?.questions?.[0]?.turns[0]?.text,
    ).toBe("<script>alert(1)</" + "script>");
  });

  it("accepts a valid current-state object", () => {
    const withState = { ...BRIEF, current_state: STATE };

    expect(
      readEmbeddedDocument(pageWith(JSON.stringify(withState))).current_state,
    ).toEqual(STATE);
  });

  it("accepts the shipped legacy current-state object", () => {
    const withState = { ...BRIEF, current_state: LEGACY_STATE };

    expect(
      readEmbeddedDocument(pageWith(JSON.stringify(withState))).current_state,
    ).toEqual(LEGACY_STATE);
  });

  it("keeps legacy documents without current state valid", () => {
    expect(readEmbeddedDocument(pageWith(JSON.stringify(BRIEF))).current_state)
      .toBeUndefined();
  });

  it.each([[], null])("refuses current state shaped as %j", (currentState) => {
    const candidate = { ...BRIEF, current_state: currentState };

    expect(() => readEmbeddedDocument(pageWith(JSON.stringify(candidate))))
      .toThrow(/invalid current state/);
  });

  it("refuses a current-state object with a missing field", () => {
    const { headline: _headline, ...missing } = STATE;
    const candidate = { ...BRIEF, current_state: missing };

    expect(() => readEmbeddedDocument(pageWith(JSON.stringify(candidate))))
      .toThrow(/invalid current state/);
  });

  it("refuses a current-state object with a wrong-typed field", () => {
    const candidate = { ...BRIEF, current_state: { ...STATE, lanes: 3 } };

    expect(() => readEmbeddedDocument(pageWith(JSON.stringify(candidate))))
      .toThrow(/invalid current state/);
  });

  it("fails loudly when the blob is missing", () => {
    const page = document.implementation.createHTMLDocument("test");

    expect(() => readEmbeddedDocument(page)).toThrow(
      /no embedded brief document/,
    );
  });

  it("fails loudly when the blob is not JSON", () => {
    expect(() => readEmbeddedDocument(pageWith("not json"))).toThrow();
  });
});

describe("describeShape", () => {
  it("counts updates, lanes, items and threads", () => {
    expect(describeShape(BRIEF)).toEqual({
      updates: 1,
      lanes: 1,
      items: 1,
      threads: 2,
    });
  });

  it("includes structured current-state lanes, items and threads", () => {
    const stateItem = structuredClone(BRIEF.updates[0]?.lanes[0]?.items[0]);
    if (stateItem === undefined) {
      throw new Error("fixture lost its item");
    }
    const withState: BriefDocument = {
      ...BRIEF,
      current_state: {
        ...STATE,
        questions: [
          {
            id: "q-root",
            anchor: { kind: "element", path: "//current-state" },
            turns: [
              { author: "human", text: "Root?", at: STATE.updated_at },
            ],
          },
        ],
        lanes: [{ id: "state", name: "State", items: [stateItem] }],
      },
    };

    expect(describeShape(withState)).toEqual({
      updates: 1,
      lanes: 2,
      items: 2,
      threads: 4,
    });
  });
});
