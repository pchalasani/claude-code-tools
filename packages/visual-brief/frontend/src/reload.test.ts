import { afterEach, describe, expect, it } from "vitest";

import {
  VERSION_META,
  checkVersion,
  pageVersion,
  readServedVersion,
  type VersionWatch,
} from "./reload";

const realFetch = globalThis.fetch;

/**
 * Answer the next request with one status and body.
 *
 * @param ok - Whether the daemon answers successfully.
 * @param body - The body it answers with.
 */
function serve(ok: boolean, body: string): void {
  globalThis.fetch = (async () => ({
    ok,
    text: async () => body,
  })) as unknown as typeof globalThis.fetch;
}

afterEach(() => {
  globalThis.fetch = realFetch;
});

function pageWithVersion(version: string): Document {
  const page = document.implementation.createHTMLDocument("test");
  const meta = page.createElement("meta");
  meta.name = VERSION_META;
  meta.content = version;
  page.head.append(meta);
  return page;
}

function watching(served: () => Promise<string>): {
  watch: VersionWatch;
  reloads: () => number;
} {
  let reloads = 0;
  return {
    watch: {
      current: "a".repeat(64),
      read: served,
      reload: () => {
        reloads += 1;
      },
    },
    reloads: () => reloads,
  };
}

describe("pageVersion", () => {
  it("reads the generation the page was rendered from", () => {
    expect(pageVersion(pageWithVersion("b".repeat(64)))).toBe("b".repeat(64));
  });

  it("returns an empty string when the page has no generation", () => {
    const page = document.implementation.createHTMLDocument("test");

    expect(pageVersion(page)).toBe("");
  });
});

describe("checkVersion", () => {
  it("reloads when the served generation moved on", async () => {
    const driver = watching(async () => "c".repeat(64));

    expect(await checkVersion(driver.watch)).toBe(true);
    expect(driver.reloads()).toBe(1);
  });

  it("stays put when the generation is unchanged", async () => {
    const driver = watching(async () => "a".repeat(64));

    expect(await checkVersion(driver.watch)).toBe(false);
    expect(driver.reloads()).toBe(0);
  });

  it("stays put when the daemon is unreachable", async () => {
    const driver = watching(async () => {
      throw new Error("connection refused");
    });

    expect(await checkVersion(driver.watch)).toBe(false);
    expect(driver.reloads()).toBe(0);
  });

  it("stays put when the daemon answers with nothing", async () => {
    const driver = watching(async () => "");

    expect(await checkVersion(driver.watch)).toBe(false);
    expect(driver.reloads()).toBe(0);
  });
});

describe("readServedVersion", () => {
  it("returns the generation the daemon serves", async () => {
    serve(true, "d".repeat(64));

    expect(await readServedVersion()).toBe("d".repeat(64));
  });

  it("treats an error answer as no answer at all", async () => {
    serve(false, '{"error": "Unknown run"}');

    expect(await readServedVersion()).toBe("");
  });

  it("does not mistake an error body for a new generation", async () => {
    serve(false, '{"error": "Rendered page is unavailable"}');
    const driver = watching(readServedVersion);

    expect(await checkVersion(driver.watch)).toBe(false);
    expect(driver.reloads()).toBe(0);
  });
});
