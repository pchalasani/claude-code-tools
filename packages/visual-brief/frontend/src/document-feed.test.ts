import { afterEach, describe, expect, it } from "vitest";

import {
  isGeneration,
  readDocumentPayload,
  readServedDocument,
  readServedVersion,
} from "./document-feed";

const realFetch = globalThis.fetch;

/**
 * Answer the next request with one status and body.
 *
 * @param ok - Whether the daemon answers successfully.
 * @param body - The body it answers with.
 * @param status - The status it answers with.
 */
function serve(ok: boolean, body: string, status = ok ? 200 : 404): void {
  globalThis.fetch = (async () => ({
    ok,
    status,
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

/**
 * Build one well-formed answer from the document endpoint.
 *
 * @param overrides - Fields to replace.
 * @returns The payload as the daemon would serialize it.
 */
function payload(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    generation: "a".repeat(64),
    assets: "b".repeat(64),
    document: { title: "A brief", summary: "A summary.", updates: [] },
    ...overrides,
  });
}

afterEach(() => {
  globalThis.fetch = realFetch;
});

describe("what counts as a generation", () => {
  it("recognises one only in the shape this client speaks", () => {
    expect(isGeneration("c".repeat(64))).toBe(true);
    expect(isGeneration("c".repeat(63))).toBe(false);
    expect(isGeneration('{"error": "Unknown run"}')).toBe(false);
    expect(isGeneration("")).toBe(false);
  });
});

describe("what the daemon says about the generation", () => {
  it("returns the generation it serves", async () => {
    serve(true, `${"d".repeat(64)}\n`);

    expect(await readServedVersion()).toBe("d".repeat(64));
  });

  it("treats an error answer as no answer at all", async () => {
    serve(false, '{"error": "Unknown run"}');

    expect(await readServedVersion()).toBeNull();
  });

  it("gives up on a daemon that answers by never answering", async () => {
    // A daemon that accepts the connection and then freezes used to end the
    // watch: the promise never settled, so the next cycle was never
    // scheduled, and the tab stopped checking for the rest of its life.
    hang();

    expect(await readServedVersion(20)).toBeNull();
  });

  it("stops waiting the moment a hung request is given up on", async () => {
    hang();
    const started = Date.now();

    await readServedVersion(20);

    expect(Date.now() - started).toBeLessThan(2000);
  });
});

describe("what the daemon says about the document", () => {
  it("hands back the answer it was given", async () => {
    serve(true, payload());

    const answer = await readServedDocument();

    expect(readDocumentPayload(answer)?.generation).toBe("a".repeat(64));
  });

  it("refuses to swallow an endpoint that is not there", async () => {
    // An older daemon has no document endpoint at all. Unlike the version
    // question, that is not a shrug: the caller's only other way forward is
    // the reload that has always worked, and it needs to be told.
    serve(false, '{"error": "Not found"}', 404);

    await expect(readServedDocument()).rejects.toThrow();
  });

  it("refuses an answer that is not JSON", async () => {
    serve(true, "<!doctype html>");

    await expect(readServedDocument()).rejects.toThrow();
  });

  it("refuses a daemon that answers by never answering", async () => {
    hang();

    await expect(readServedDocument(20)).rejects.toThrow();
  });
});

describe("what a document payload has to look like", () => {
  it("accepts the three fields the page acts on", () => {
    const read = readDocumentPayload(JSON.parse(payload()));

    expect(read).not.toBeNull();
    expect(read?.assets).toBe("b".repeat(64));
    expect(read?.document.title).toBe("A brief");
  });

  it("refuses anything that is not an object at all", () => {
    expect(readDocumentPayload(null)).toBeNull();
    expect(readDocumentPayload("a document")).toBeNull();
    expect(readDocumentPayload([1, 2, 3])).toBeNull();
  });

  it("refuses a generation this client cannot compare", () => {
    expect(
      readDocumentPayload(JSON.parse(payload({ generation: "soon" }))),
    ).toBeNull();
    expect(
      readDocumentPayload(JSON.parse(payload({ generation: 7 }))),
    ).toBeNull();
  });

  it("refuses a payload that says nothing about the bundle", () => {
    // Which bundle a document belongs to is the whole basis of the decision
    // to patch rather than reload, so a payload that does not say is one this
    // page cannot act on.
    const anonymous = JSON.parse(payload()) as Record<string, unknown>;
    delete anonymous.assets;

    expect(readDocumentPayload(anonymous)).toBeNull();
  });

  it("refuses a document with nothing in the shape of a brief", () => {
    expect(
      readDocumentPayload(JSON.parse(payload({ document: {} }))),
    ).toBeNull();
    expect(
      readDocumentPayload(
        JSON.parse(payload({ document: { title: "t", summary: "s" } })),
      ),
    ).toBeNull();
    expect(
      readDocumentPayload(JSON.parse(payload({ document: "a brief" }))),
    ).toBeNull();
  });

  it("accepts an empty bundle stamp, and leaves the judging to the page", () => {
    // An older daemon may say nothing about the bundle. That is a difference
    // from what this page carries, not a malformed answer, and the page is
    // where differences are acted on.
    expect(readDocumentPayload(JSON.parse(payload({ assets: "" })))?.assets)
      .toBe("");
  });
});
