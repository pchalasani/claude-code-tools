import { beforeEach, describe, expect, it } from "vitest";

import type { BriefDocument } from "./document";
import { healingWatch, pollOnce, type VersionWatch } from "./reload";
import { forgetStores, withoutSessionStorage } from "../test/storage";

const HERE = "a".repeat(64);
const THERE = "c".repeat(64);
const BUNDLE = "b".repeat(64);
const INSTANCE = "d".repeat(64);

beforeEach(() => {
  forgetStores();
});

/**
 * Build one well-formed answer from the document endpoint.
 *
 * @param overrides - Fields to replace.
 * @returns The payload, already parsed.
 */
function payload(overrides: Record<string, unknown> = {}): unknown {
  return {
    generation: THERE,
    assets: BUNDLE,
    instance: INSTANCE,
    document: { title: "A brief", summary: "A summary.", updates: [] },
    ...overrides,
  };
}

/** What one driven watch was asked to do. */
interface Driver {
  watch: VersionWatch;
  reloads: () => number;
  remembered: () => number;
  applied: () => BriefDocument[];
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
 * @param served - What the daemon answers about the generation.
 * @param options - The page's own generation and bundle, and what the document
 *     endpoint and the application do.
 * @returns The watch and what it did.
 */
function watching(
  served: () => Promise<string | null>,
  options: {
    current?: string;
    assets?: string;
    instance?: string;
    fetchPayload?: () => Promise<unknown>;
    apply?: (document: BriefDocument) => void;
  } = {},
): Driver {
  let reloads = 0;
  let remembered = 0;
  const applied: BriefDocument[] = [];
  const watch = healingWatch({
    current: options.current ?? HERE,
    assets: options.assets ?? BUNDLE,
    instance: options.instance ?? INSTANCE,
    read: served,
    fetchPayload: options.fetchPayload ?? (async () => payload()),
    apply:
      options.apply
      ?? ((document): void => {
        applied.push(document);
      }),
    reload: () => {
      reloads += 1;
    },
  });
  const remember = watch.remember;
  watch.remember = (answer) => {
    remembered += 1;
    remember(answer);
  };
  return {
    watch,
    reloads: () => reloads,
    remembered: () => remembered,
    applied: () => applied,
  };
}

describe("a publish, on a page that stays alive", () => {
  it("fetches the new document and shows it, without reloading", async () => {
    const driver = watching(async () => THERE);

    expect(await pollOnce(driver.watch)).toBe("patched");
    expect(driver.applied().map((brief) => brief.title)).toEqual(["A brief"]);
    expect(driver.reloads()).toBe(0);
    expect(driver.remembered()).toBe(0);
  });

  it("adopts the generation the document came with, not the polled one", async () => {
    // A second publish landing between the poll and the fetch is ordinary.
    // Adopting what the poll said would leave the page believing it is
    // showing something it is not, and it would then sit out the next
    // publish entirely.
    const newer = "e".repeat(64);
    const driver = watching(async () => THERE, {
      fetchPayload: async () => payload({ generation: newer }),
    });

    await pollOnce(driver.watch);

    expect(driver.watch.current).toBe(newer);
  });

  it("does nothing at all when the generation has not moved", async () => {
    const driver = watching(async () => HERE);

    expect(await pollOnce(driver.watch)).toBe("same");
    expect(driver.applied()).toEqual([]);
    expect(driver.reloads()).toBe(0);
  });
});

describe("when a publish cannot be patched in", () => {
  it("reloads for a different bundle, and shows nothing", async () => {
    // Only a reload loads code. A document patched into a page running last
    // week's bundle leaves that page running last week's bundle for good.
    const driver = watching(async () => THERE, {
      fetchPayload: async () => payload({ assets: "f".repeat(64) }),
    });

    expect(await pollOnce(driver.watch)).toBe("reload");
    expect(driver.applied()).toEqual([]);
    expect(driver.reloads()).toBe(1);
  });

  it("reloads for a different physical run, and shows nothing", async () => {
    const driver = watching(async () => THERE, {
      fetchPayload: async () => payload({ instance: "e".repeat(64) }),
    });

    expect(await pollOnce(driver.watch)).toBe("reload");
    expect(driver.applied()).toEqual([]);
    expect(driver.reloads()).toBe(1);
  });

  it("reloads when the document endpoint is not there", async () => {
    const driver = watching(async () => THERE, {
      fetchPayload: async () => {
        throw new Error("404");
      },
    });

    expect(await pollOnce(driver.watch)).toBe("reload");
    expect(driver.reloads()).toBe(1);
  });

  it("reloads when the payload is the wrong shape", async () => {
    const driver = watching(async () => THERE, {
      fetchPayload: async () => ({ generation: THERE }),
    });

    expect(await pollOnce(driver.watch)).toBe("reload");
    expect(driver.reloads()).toBe(1);
  });

  it("reloads when showing the document throws", async () => {
    const driver = watching(async () => THERE, {
      apply: () => {
        throw new Error("the application refused it");
      },
    });

    expect(await pollOnce(driver.watch)).toBe("reload");
    expect(driver.reloads()).toBe(1);
  });

  it("reloads once, and then stays put on the same standoff", async () => {
    // The page comes back into exactly the situation it left. A second reload
    // would be a third, and a fourth, for the life of the tab.
    const missing = async (): Promise<unknown> => {
      throw new Error("404");
    };
    const first = watching(async () => THERE, { fetchPayload: missing });
    expect(await pollOnce(first.watch)).toBe("reload");
    expect(first.remembered()).toBe(1);

    const second = watching(async () => THERE, { fetchPayload: missing });

    expect(await pollOnce(second.watch)).toBe("same");
    expect(second.reloads()).toBe(0);
  });

  it("still reloads for the next publish after giving up on one", async () => {
    const missing = async (): Promise<unknown> => {
      throw new Error("404");
    };
    const first = watching(async () => THERE, { fetchPayload: missing });
    expect(await pollOnce(first.watch)).toBe("reload");

    // The agent publishes again. A different answer is a different situation.
    const later = watching(async () => "d".repeat(64), {
      fetchPayload: missing,
    });

    expect(await pollOnce(later.watch)).toBe("reload");
    expect(later.reloads()).toBe(1);
  });
});

describe("one poll cycle", () => {
  it("reloads, and remembers, when the answer cannot be read", async () => {
    const driver = watching(async () => "not a generation");

    expect(await pollOnce(driver.watch)).toBe("reload");
    expect(driver.reloads()).toBe(1);
    expect(driver.remembered()).toBe(1);
    expect(driver.applied()).toEqual([]);
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

  it("does not reload the page for an error body", async () => {
    const driver = watching(async () => null);

    expect(await pollOnce(driver.watch)).toBe("retry");
    expect(driver.reloads()).toBe(0);
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
    const first = watching(async () => THERE, { current: "" });
    expect(await pollOnce(first.watch)).toBe("reload");
    expect(first.reloads()).toBe(1);

    const second = watching(async () => THERE, { current: "" });

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
    const first = watching(async () => THERE, { current: "" });
    expect(await pollOnce(first.watch)).toBe("reload");

    const settled = watching(async () => THERE, { current: "" });
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

  it("keeps patching for content that simply moved on", async () => {
    // Healing is remembered once; a generation that keeps changing is the
    // daemon publishing, and every one of those is worth showing.
    const first = watching(async () => THERE);
    expect(await pollOnce(first.watch)).toBe("patched");

    const second = watching(async () => "d".repeat(64));

    expect(await pollOnce(second.watch)).toBe("patched");
    expect(second.reloads()).toBe(0);
  });
});
