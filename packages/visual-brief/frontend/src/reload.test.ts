import { afterEach, describe, expect, it } from "vitest";

import {
  MAX_POLL_INTERVAL_MS,
  POLL_INTERVAL_MS,
  POLL_META,
  VERSION_META,
  announcePoll,
  decidePoll,
  isGeneration,
  nextDelay,
  onPollCycle,
  pageVersion,
  pollInterval,
  pollOnce,
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

/** Accept the next request and never answer it, until it is abandoned. */
function hang(): void {
  globalThis.fetch = ((_path: string, init?: RequestInit) =>
    new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        reject(new Error("the request was abandoned"));
      });
    })) as unknown as typeof globalThis.fetch;
}

afterEach(() => {
  globalThis.fetch = realFetch;
});

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

/**
 * Build a watch that records what it was asked to do.
 *
 * @param served - What the daemon answers.
 * @param options - The page's own generation and healing memory.
 * @returns The watch and what it did.
 */
function watching(
  served: () => Promise<string | null>,
  options: { current?: string; healed?: boolean } = {},
): {
  watch: VersionWatch;
  reloads: () => number;
  remembered: () => number;
} {
  let reloads = 0;
  let remembered = 0;
  return {
    watch: {
      current: options.current ?? "a".repeat(64),
      read: served,
      reload: () => {
        reloads += 1;
      },
      healed: () => options.healed === true,
      remember: () => {
        remembered += 1;
      },
    },
    reloads: () => reloads,
    remembered: () => remembered,
  };
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

  it("recognises a generation only in the shape this client speaks", () => {
    expect(isGeneration("c".repeat(64))).toBe(true);
    expect(isGeneration("c".repeat(63))).toBe(false);
    expect(isGeneration('{"error": "Unknown run"}')).toBe(false);
    expect(isGeneration("")).toBe(false);
  });
});

describe("what one answer from the daemon means", () => {
  const current = "a".repeat(64);

  it("reloads when the served generation moved on", () => {
    expect(decidePoll(current, "c".repeat(64), false)).toBe("reload");
  });

  it("stays put when the generation is unchanged", () => {
    expect(decidePoll(current, current, false)).toBe("same");
  });

  it("backs off rather than reloading when nothing answered", () => {
    expect(decidePoll(current, null, false)).toBe("retry");
  });

  it("reloads on an answer it cannot read at all", () => {
    // The daemon was upgraded under an open tab and now says something this
    // page does not understand. Swallowing that is what stranded the tab.
    expect(decidePoll(current, "version 2 ok", false)).toBe("reload");
    expect(decidePoll(current, "", false)).toBe("reload");
  });

  it("reloads a page whose own generation the daemon cannot speak", () => {
    expect(decidePoll("", "c".repeat(64), false)).toBe("reload");
  });

  it("reloads for a mismatch once, and then stays readable", () => {
    expect(decidePoll(current, "version 2 ok", true)).toBe("same");
    expect(decidePoll("", "c".repeat(64), true)).toBe("same");
  });
});

describe("one poll cycle", () => {
  it("reloads, and remembers, when the answer cannot be read", async () => {
    const driver = watching(async () => "not a generation");

    expect(await pollOnce(driver.watch)).toBe("reload");
    expect(driver.reloads()).toBe(1);
    expect(driver.remembered()).toBe(1);
  });

  it("reloads without remembering when content simply moved on", async () => {
    const driver = watching(async () => "c".repeat(64));

    expect(await pollOnce(driver.watch)).toBe("reload");
    expect(driver.reloads()).toBe(1);
    expect(driver.remembered()).toBe(0);
  });

  it("survives a read that throws, and asks again later", async () => {
    const driver = watching(async () => {
      throw new Error("connection refused");
    });

    expect(await pollOnce(driver.watch)).toBe("retry");
    expect(driver.reloads()).toBe(0);
  });

  it("survives a page that cannot even remember it healed", async () => {
    const driver = watching(async () => "not a generation");
    driver.watch.remember = () => {
      throw new Error("storage is disabled");
    };

    expect(await pollOnce(driver.watch)).toBe("reload");
    expect(driver.reloads()).toBe(1);
  });
});

describe("backing off", () => {
  it("doubles the wait while nothing answers, up to a ceiling", () => {
    expect(nextDelay("retry", 5000, 5000)).toBe(10_000);
    expect(nextDelay("retry", 40_000, 5000)).toBe(MAX_POLL_INTERVAL_MS);
  });

  it("returns to the normal rhythm the moment anything answers", () => {
    expect(nextDelay("same", 40_000, 5000)).toBe(5000);
    expect(nextDelay("reload", 40_000, 5000)).toBe(5000);
  });
});

describe("what the daemon says", () => {
  it("returns the generation it serves", async () => {
    serve(true, `${"d".repeat(64)}\n`);

    expect(await readServedVersion()).toBe("d".repeat(64));
  });

  it("treats an error answer as no answer at all", async () => {
    serve(false, '{"error": "Unknown run"}');

    expect(await readServedVersion()).toBeNull();
  });

  it("does not let an error body strand or reload the page", async () => {
    serve(false, '{"error": "Rendered page is unavailable"}');
    const driver = watching(readServedVersion);

    expect(await pollOnce(driver.watch)).toBe("retry");
    expect(driver.reloads()).toBe(0);
  });

  it("gives up on a daemon that answers by never answering", async () => {
    // A daemon that accepts the connection and then freezes used to end the
    // watch: the promise never settled, so the next cycle was never
    // scheduled, and the tab stopped checking for the rest of its life.
    hang();
    const driver = watching(() => readServedVersion(20));

    expect(await readServedVersion(20)).toBeNull();
    expect(await pollOnce(driver.watch)).toBe("retry");
    expect(driver.reloads()).toBe(0);
  });

  it("stops waiting the moment a hung request is given up on", async () => {
    hang();
    const started = Date.now();

    await readServedVersion(20);

    expect(Date.now() - started).toBeLessThan(2000);
  });
});

describe("telling the page about polls", () => {
  it("delivers every cycle, and one bad listener spoils nothing", () => {
    const heard: string[] = [];
    const stopFirst = onPollCycle(() => {
      throw new Error("a listener that fails");
    });
    const stopSecond = onPollCycle((outcome) => heard.push(outcome));

    announcePoll("same");
    stopFirst();
    announcePoll("retry");
    stopSecond();
    announcePoll("reload");

    expect(heard).toEqual(["same", "retry"]);
  });
});
