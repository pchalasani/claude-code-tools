import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  MAX_POLL_INTERVAL_MS,
  POLL_INTERVAL_MS,
  POLL_META,
  VERSION_META,
  announcePoll,
  decidePoll,
  healingWatch,
  isGeneration,
  nextDelay,
  onPollCycle,
  pageVersion,
  pollInterval,
  pollOnce,
  readServedVersion,
  type VersionWatch,
} from "./reload";
import { forgetStores, withoutSessionStorage } from "../test/storage";

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

beforeEach(() => {
  forgetStores();
});

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
 * Load the page again, on a watch that records what it was asked to do.
 *
 * The watch is the one a real page runs on, memory and all: what it recalls
 * having healed comes out of the browser's own stores rather than out of a
 * flag set by the test. Calling this twice is what two page loads look like —
 * the second watch starts with no memory but the store's, exactly as a
 * reloaded page does.
 *
 * @param served - What the daemon answers.
 * @param options - The page's own generation.
 * @returns The watch and what it did.
 */
function watching(
  served: () => Promise<string | null>,
  options: { current?: string } = {},
): {
  watch: VersionWatch;
  reloads: () => number;
  remembered: () => number;
} {
  let reloads = 0;
  let remembered = 0;
  const watch = healingWatch(options.current ?? "a".repeat(64), served, () => {
    reloads += 1;
  });
  const remember = watch.remember;
  watch.remember = (served) => {
    remembered += 1;
    remember(served);
  };
  return {
    watch,
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
    driver.watch.remember = (): never => {
      throw new Error("storage is disabled");
    };

    expect(await pollOnce(driver.watch)).toBe("reload");
    expect(driver.reloads()).toBe(1);
  });
});

describe("healing, remembered where the page really keeps it", () => {
  it("reloads out of an answer it cannot read once, not every load", async () => {
    const first = watching(async () => "not a generation");
    expect(await pollOnce(first.watch)).toBe("reload");
    expect(first.reloads()).toBe(1);

    // The page comes back exactly as it left: same generation, same daemon,
    // saying the same unintelligible thing.
    const second = watching(async () => "not a generation");

    expect(await pollOnce(second.watch)).toBe("same");
    expect(second.reloads()).toBe(0);
  });

  it("does the same for a page served with no generation at all", async () => {
    // This is the page that used to reload forever: what it remembered was
    // the empty string, and the store handed the empty string back as
    // "nothing was ever remembered".
    const first = watching(async () => "c".repeat(64), { current: "" });
    expect(await pollOnce(first.watch)).toBe("reload");
    expect(first.reloads()).toBe(1);

    const second = watching(async () => "c".repeat(64), { current: "" });

    expect(await pollOnce(second.watch)).toBe("same");
    expect(second.reloads()).toBe(0);
  });

  it("remembers even where session storage is refused outright", async () => {
    // Storage that is switched off fails silently, and a silently forgotten
    // healing is a tab that reloads, comes back, and reloads again.
    await withoutSessionStorage(async () => {
      const first = watching(async () => "not a generation");
      expect(await pollOnce(first.watch)).toBe("reload");

      const second = watching(async () => "not a generation");

      expect(await pollOnce(second.watch)).toBe("same");
      expect(second.reloads()).toBe(0);
    });
  });

  it("still notices a publish after healing out of a page it cannot read", async () => {
    // The gap a stale tab could hide in. A page served with no generation of
    // its own can never be compared with anything, so it heals once — and,
    // when what it remembered was only itself, it then read EVERY later
    // answer as the same impasse. It stopped reloading for good and went on
    // running whatever code it had been served, which is exactly what a tab
    // showing withdrawn wording looks like.
    const first = watching(async () => "c".repeat(64), { current: "" });
    expect(await pollOnce(first.watch)).toBe("reload");

    const settled = watching(async () => "c".repeat(64), { current: "" });
    expect(await pollOnce(settled.watch)).toBe("same");

    // The agent publishes. The tab cannot compare the two generations, but
    // it can see that the answer is not the one it gave up on, and one more
    // reload is what fetches the page — and the bundle — being served now.
    const republished = watching(async () => "d".repeat(64), { current: "" });

    expect(await pollOnce(republished.watch)).toBe("reload");
    expect(republished.reloads()).toBe(1);
  });

  it("reloads again when the daemon starts saying something different", async () => {
    const first = watching(async () => "visual-brief 2: unreadable");
    expect(await pollOnce(first.watch)).toBe("reload");

    const upgraded = watching(async () => "visual-brief 3: also unreadable");

    expect(await pollOnce(upgraded.watch)).toBe("reload");
    expect(upgraded.reloads()).toBe(1);
  });

  it("keeps reloading for content that simply moved on", async () => {
    // Healing is remembered once; a generation that keeps changing is the
    // daemon publishing, and every one of those is worth a reload.
    const first = watching(async () => "c".repeat(64));
    expect(await pollOnce(first.watch)).toBe("reload");

    const second = watching(async () => "d".repeat(64));

    expect(await pollOnce(second.watch)).toBe("reload");
    expect(second.reloads()).toBe(1);
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
