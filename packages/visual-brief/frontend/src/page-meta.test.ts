import { describe, expect, it } from "vitest";

import {
  ASSETS_META,
  POLL_INTERVAL_MS,
  POLL_META,
  VERSION_META,
  pageAssets,
  pageVersion,
  pollInterval,
} from "./page-meta";

const BUNDLE = "b".repeat(64);

/**
 * Build a page carrying the given meta elements.
 *
 * @param metas - Meta names and their content.
 * @returns The page.
 */
function pageWith(metas: Record<string, string>): Document {
  const page = document.implementation.createHTMLDocument("test");
  for (const [name, content] of Object.entries(metas)) {
    const meta = page.createElement("meta");
    meta.name = name;
    meta.content = content;
    page.head.append(meta);
  }
  return page;
}

describe("what a page knows about itself", () => {
  it("reads the generation it was rendered from", () => {
    expect(pageVersion(pageWith({ [VERSION_META]: "b".repeat(64) }))).toBe(
      "b".repeat(64),
    );
  });

  it("returns an empty string when the page has no generation", () => {
    expect(pageVersion(pageWith({}))).toBe("");
  });

  it("reads which front-end bundle it is running", () => {
    expect(pageAssets(pageWith({ [ASSETS_META]: BUNDLE }))).toBe(BUNDLE);
  });

  it("returns an empty string when the page names no bundle", () => {
    // A page from before bundle stamps existed names none, and an empty
    // string matches nothing the daemon can answer — so such a page reloads
    // rather than patching a document into code it cannot vouch for.
    expect(pageAssets(pageWith({}))).toBe("");
  });

  it("reads how often the page asks to be checked", () => {
    expect(pollInterval(pageWith({ [POLL_META]: "250" }))).toBe(250);
  });

  it("falls back when the page does not say, or says nonsense", () => {
    expect(pollInterval(pageWith({}))).toBe(POLL_INTERVAL_MS);
    expect(pollInterval(pageWith({ [POLL_META]: "soon" }))).toBe(
      POLL_INTERVAL_MS,
    );
  });

  it("refuses an interval that would hammer the daemon", () => {
    expect(pollInterval(pageWith({ [POLL_META]: "1" }))).toBe(100);
  });
});
