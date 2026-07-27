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
});
