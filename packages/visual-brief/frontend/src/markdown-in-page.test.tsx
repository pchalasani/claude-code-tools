import { describe, expect, it } from "vitest";

import type { BriefDocument } from "./document";
import { mount, press, useHarness } from "../test/harness";
import { sampleBrief } from "../test/sample-brief";

const ITEM = "newest/changed/alpha";
const THREAD = `${ITEM}#q-answered`;
const IMAGE = "<img src=x onerror=alert(1)>";
const HOSTILE_LINK = "[click](javascript:alert(1))";

useHarness();

/**
 * Build a page where every author has written something hostile.
 *
 * The agent's words and the human's words go into the same document and come
 * back through the same renderer, so both are planted here: whatever is true
 * of one has to be true of the other.
 *
 * @returns The document.
 */
function hostile(): BriefDocument {
  const brief = sampleBrief();
  const item = brief.updates
    .find((update) => update.id === "newest")
    ?.lanes.find((lane) => lane.id === "changed")
    ?.items.find((one) => one.id === "alpha");
  const thread = item?.questions?.[0];
  if (item === undefined || thread === undefined) {
    throw new Error("the sample document lost what this test writes into");
  }
  item.explanation = `A **checked** claim.\n\n${IMAGE}\n\n${HOSTILE_LINK}`;
  thread.turns = [
    {
      author: "human",
      text: `Is \`alpha\` checked? ${IMAGE}`,
      at: "2026-07-25T11:00:00Z",
    },
    {
      author: "agent",
      text: `Yes — see *the log*:\n\n\`\`\`\n${IMAGE}\n\`\`\``,
      at: "2026-07-25T11:01:00Z",
    },
  ];
  return brief;
}

describe("markdown in what the agent and the human wrote", () => {
  it("reads an item's explanation as prose", () => {
    mount(hostile());
    press("E");

    expect(
      document.querySelector(`[data-row-id="${ITEM}"] .explanation strong`)
        ?.textContent,
    ).toBe("checked");
  });

  it("reads both authors' turns, including the human's own", () => {
    // Both, deliberately: the renderer builds elements out of a closed
    // grammar and cannot produce markup, so the human's text is exactly as
    // safe as the agent's — and one path is easier to keep safe than two.
    mount(hostile());
    press("E");

    const turns = document.querySelectorAll(
      `[data-row-id="${THREAD}"] .turn-text`,
    );
    expect(turns[0]?.querySelector("code")?.textContent).toBe("alpha");
    expect(turns[1]?.querySelector("em")?.textContent).toBe("the log");
  });

  it("never lets any of it become a live element", () => {
    mount(hostile());
    press("E");

    expect(document.querySelectorAll("img")).toHaveLength(0);
    expect(document.querySelectorAll("script")).toHaveLength(0);
    expect(document.querySelector('[onerror]')).toBeNull();
    expect(
      [...document.querySelectorAll("a")].map((link) => link.getAttribute("href")),
    ).not.toContain("javascript:alert(1)");
  });

  it("shows the characters the author actually wrote", () => {
    mount(hostile());
    press("E");

    const explanation = document.querySelector(
      `[data-row-id="${ITEM}"] .explanation`,
    );
    expect(explanation?.textContent).toContain(IMAGE);
    expect(explanation?.textContent).toContain(HOSTILE_LINK);
    // Inside a fenced block the markup is code, and code is text.
    expect(
      document.querySelector(
        `[data-row-id="${THREAD}"] .turn-agent pre.md-code-block`,
      )?.textContent,
    ).toBe(IMAGE);
  });
});
