import { beforeEach, describe, expect, it } from "vitest";

import { createComposer, sentFromThisPage, type Post } from "./composer";

/** One recorded call to the daemon. */
interface Sent {
  path: string;
  payload: unknown;
}

/**
 * Build a sender that records what it was given and answers on demand.
 *
 * @param accepted - What the daemon answers.
 * @param timestamp - The queue timestamp it reports, if any.
 * @returns The sender, its record, and a release for the request in flight.
 */
function recorder(accepted = true, timestamp = ""): {
  post: Post;
  sent: Sent[];
  release: () => void;
} {
  const sent: Sent[] = [];
  let unblock: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    unblock = resolve;
  });
  return {
    sent,
    release: () => unblock(),
    post: async (path, payload) => {
      sent.push({ path, payload });
      await gate;
      return { ok: accepted, timestamp };
    },
  };
}

beforeEach(() => window.sessionStorage.clear());

describe("composition", () => {
  it("always carries a target, and sends what was written there", async () => {
    const daemon = recorder(true, "2026-07-27T09:00:00.000Z");
    const composer = createComposer(daemon.post);

    composer.toggleAt({ rowId: "u/l/i", anchorId: "u/l/i" });
    composer.setText("  Why this way?  ");
    const inFlight = composer.submit();
    daemon.release();
    await inFlight;

    expect(daemon.sent).toEqual([
      { path: "ask", payload: { anchor_id: "u/l/i", text: "Why this way?" } },
    ]);
    expect(composer.target()).toBeNull();
    expect(composer.pendingAt("u/l/i")).toEqual([
      {
        rowId: "u/l/i",
        text: "Why this way?",
        at: "2026-07-27T09:00:00.000Z",
        stalled: false,
      },
    ]);
  });

  it("carries the thread it continues when the target is a reply", async () => {
    const daemon = recorder();
    const composer = createComposer(daemon.post);

    composer.toggleAt({
      rowId: "u/l/i#q-1",
      anchorId: "u/l/i",
      parentId: "q-1",
    });
    composer.setText("And what about nesting?");
    const inFlight = composer.submit();
    daemon.release();
    await inFlight;

    expect(daemon.sent[0]?.payload).toEqual({
      anchor_id: "u/l/i",
      text: "And what about nesting?",
      parent_id: "q-1",
    });
  });

  it("refuses to send twice while one request is in flight", async () => {
    const daemon = recorder();
    const composer = createComposer(daemon.post);

    composer.toggleAt({ rowId: "u/l/i", anchorId: "u/l/i" });
    composer.setText("Send this only once");
    const first = composer.submit();
    const second = composer.submit();

    expect(composer.sending()).toBe(true);
    daemon.release();
    await Promise.all([first, second]);

    expect(daemon.sent).toHaveLength(1);
    expect(composer.sending()).toBe(false);
  });

  it("sends nothing at all without a target or without text", async () => {
    const daemon = recorder();
    const composer = createComposer(daemon.post);

    await composer.submit();
    composer.toggleAt({ rowId: "u/l/i", anchorId: "u/l/i" });
    composer.setText("   ");
    await composer.submit();

    expect(daemon.sent).toEqual([]);
  });

  it("says so instead of going quiet when nothing was written", async () => {
    const daemon = recorder();
    const composer = createComposer(daemon.post);

    composer.toggleAt({ rowId: "u/l/i", anchorId: "u/l/i" });
    composer.setText("   ");
    await composer.submit();

    expect(daemon.sent).toEqual([]);
    expect(composer.status()).toBe("Write something first.");
  });

  it("hands back every row it let go of, and says which was written", async () => {
    const daemon = recorder();
    const released: [string, boolean][] = [];
    const composer = createComposer(daemon.post, (rowId, sent) => {
      released.push([rowId, sent]);
    });

    composer.toggleAt({ rowId: "u/l/a", anchorId: "u/l/a" });
    composer.toggleAt({ rowId: "u/l/b", anchorId: "u/l/b" });
    composer.close();
    composer.toggleAt({ rowId: "u/l/c", anchorId: "u/l/c" });
    composer.setText("Ship it");
    const inFlight = composer.submit();
    daemon.release();
    await inFlight;

    expect(released).toEqual([
      ["u/l/a", false],
      ["u/l/b", false],
      ["u/l/c", true],
    ]);
  });

  it("reports one signal however fast the same button is pressed", async () => {
    const daemon = recorder();
    const composer = createComposer(daemon.post);

    const first = composer.sendSignal("u/l/i", "u/l/i", "too-dense");
    const second = composer.sendSignal("u/l/i", "u/l/i", "too-dense");

    expect(composer.signalStatus("u/l/i")).toBe("Sending Too dense…");
    daemon.release();
    await Promise.all([first, second]);

    expect(daemon.sent).toHaveLength(1);
    expect(composer.signalStatus("u/l/i")).toBe("Feedback received: Too dense");
  });

  it("still reports a different signal pressed while one is in flight", async () => {
    const daemon = recorder();
    const composer = createComposer(daemon.post);

    const first = composer.sendSignal("u/l/i", "u/l/i", "too-dense");
    const second = composer.sendSignal("u/l/i", "u/l/i", "go-deeper");
    daemon.release();
    await Promise.all([first, second]);

    expect(daemon.sent.map((call) => call.payload)).toEqual([
      { anchor_id: "u/l/i", signal: "too-dense" },
      { anchor_id: "u/l/i", signal: "go-deeper" },
    ]);
    expect(composer.signalStatus("u/l/i")).toBe("Feedback received: Go deeper");
  });

  it("keeps what was written when the daemon refuses it", async () => {
    const daemon = recorder(false);
    const composer = createComposer(daemon.post);

    composer.toggleAt({ rowId: "u/l/i", anchorId: "u/l/i" });
    composer.setText("Does this survive?");
    const inFlight = composer.submit();
    daemon.release();
    await inFlight;

    expect(composer.target()).not.toBeNull();
    expect(composer.text()).toBe("Does this survive?");
    expect(composer.status()).toContain("Could not send");
    expect(composer.pendingAt("u/l/i")).toEqual([]);
  });

  it("owns the waiting sign from the moment of sending until a reload", async () => {
    const daemon = recorder();
    const composer = createComposer(daemon.post);

    expect(sentFromThisPage(composer, "u/l/i")).toBe(false);
    composer.toggleAt({ rowId: "u/l/i", anchorId: "u/l/i" });
    composer.setText("Is anything happening?");
    const inFlight = composer.submit();

    expect(sentFromThisPage(composer, "u/l/i")).toBe(true);
    expect(sentFromThisPage(composer, "u/l/other")).toBe(false);
    daemon.release();
    await inFlight;

    expect(sentFromThisPage(composer, "u/l/i")).toBe(true);
  });

  it("reports one-click feedback against the anchor it belongs to", async () => {
    const daemon = recorder();
    daemon.release();
    const composer = createComposer(daemon.post);

    await composer.sendSignal("u/l/i", "u/l/i", "too-dense");

    expect(daemon.sent).toEqual([
      { path: "signal", payload: { anchor_id: "u/l/i", signal: "too-dense" } },
    ]);
    expect(composer.signalStatus("u/l/i")).toBe("Feedback received: Too dense");
  });
});
