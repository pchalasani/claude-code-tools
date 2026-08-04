import { beforeEach, describe, expect, it } from "vitest";

import { createComposer, type Post, type PostReply } from "./composer";
import type { SuggestedReply } from "./document";
import type { Row } from "./outline";
import { isWorking } from "./working";

/** One recorded call to the daemon. */
interface Sent {
  path: string;
  payload: unknown;
}

const DENSE: SuggestedReply = {
  label: "Less dense",
  message: "Rewrite this item so it is easier to scan.",
};
const EVIDENCE: SuggestedReply = {
  label: "Show proof",
  message: "Show me the concrete evidence behind this claim.",
};
const DEEPER: SuggestedReply = {
  label: "Go deeper",
  message: "Explain this item in more depth.",
};

/**
 * Build the row an item would have in the outline.
 *
 * @param id - The row's id, which is also its anchor.
 * @returns The row.
 */
function itemRow(id: string): Row {
  return {
    id,
    kind: "item",
    anchorId: id,
    parentId: null,
    label: id,
    search: "",
    awaiting: false,
    human: false,
  };
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

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState(null, "");
});

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

  it("keeps one draft per row across navigation and a new page state", () => {
    const composer = createComposer();
    composer.toggleAt({ rowId: "u/l/a", anchorId: "u/l/a" });
    composer.setText("Draft for A");
    composer.toggleAt({ rowId: "u/l/b", anchorId: "u/l/b" });
    composer.setText("Draft for B");
    composer.close();

    const reloaded = createComposer();
    reloaded.toggleAt({ rowId: "u/l/a", anchorId: "u/l/a" });
    expect(reloaded.text()).toBe("Draft for A");
    reloaded.toggleAt({ rowId: "u/l/b", anchorId: "u/l/b" });
    expect(reloaded.text()).toBe("Draft for B");
  });

  it("needs two Escapes to discard words but one for an empty box", () => {
    const composer = createComposer();
    composer.toggleAt({ rowId: "u/l/a", anchorId: "u/l/a" });
    composer.escape();
    expect(composer.target()).toBeNull();

    composer.toggleAt({ rowId: "u/l/a", anchorId: "u/l/a" });
    composer.setText("Do not lose this");
    composer.escape();
    expect(composer.target()?.rowId).toBe("u/l/a");
    expect(composer.text()).toBe("Do not lose this");
    expect(composer.status()).toContain("again");

    composer.escape();
    expect(composer.target()).toBeNull();
    composer.toggleAt({ rowId: "u/l/a", anchorId: "u/l/a" });
    expect(composer.text()).toBe("");
  });

  it("clears only the draft that was sent", async () => {
    const daemon = recorder();
    const composer = createComposer(daemon.post);
    composer.toggleAt({ rowId: "u/l/a", anchorId: "u/l/a" });
    composer.setText("Send A");
    composer.toggleAt({ rowId: "u/l/b", anchorId: "u/l/b" });
    composer.setText("Keep B");
    composer.toggleAt({ rowId: "u/l/a", anchorId: "u/l/a" });
    const submitted = composer.submit();
    daemon.release();
    await submitted;

    composer.toggleAt({ rowId: "u/l/a", anchorId: "u/l/a" });
    expect(composer.text()).toBe("");
    composer.toggleAt({ rowId: "u/l/b", anchorId: "u/l/b" });
    expect(composer.text()).toBe("Keep B");
  });

  it("reports one signal however fast the same button is pressed", async () => {
    const daemon = recorder();
    const composer = createComposer(daemon.post);

    const first = composer.sendSignal("u/l/i", "u/l/i", DENSE);
    const second = composer.sendSignal("u/l/i", "u/l/i", DENSE);

    expect(composer.signalStatus("u/l/i")).toBe("Sending Less dense…");
    expect(composer.selectedSignalAt("u/l/i")).toBe(DENSE.message);
    daemon.release();
    await Promise.all([first, second]);

    expect(daemon.sent).toHaveLength(1);
    expect(composer.signalStatus("u/l/i")).toBe("");
    expect(composer.signalWorkingAt("u/l/i")).toBe(true);
    expect(composer.selectedSignalAt("u/l/i")).toBe(DENSE.message);

    await composer.sendSignal("u/l/i", "u/l/i", DENSE);
    expect(daemon.sent).toHaveLength(1);
  });

  it("still reports a different signal pressed while one is in flight", async () => {
    const daemon = recorder();
    const composer = createComposer(daemon.post);

    const first = composer.sendSignal("u/l/i", "u/l/i", DENSE);
    const second = composer.sendSignal("u/l/i", "u/l/i", DEEPER);
    daemon.release();
    await Promise.all([first, second]);

    expect(daemon.sent.map((call) => call.payload)).toEqual([
      { anchor_id: "u/l/i", label: DENSE.label, text: DENSE.message },
      { anchor_id: "u/l/i", label: DEEPER.label, text: DEEPER.message },
    ]);
    expect(composer.signalStatus("u/l/i")).toBe("");
    expect(composer.signalWorkingAt("u/l/i")).toBe(true);
  });

  it("does not start signal work when feedback is rejected", async () => {
    const daemon = recorder(false);
    const composer = createComposer(daemon.post);

    const sent = composer.sendSignal("u/l/i", "u/l/i", EVIDENCE);
    expect(composer.signalStatus("u/l/i")).toBe("Sending Show proof…");
    expect(composer.signalWorkingAt("u/l/i")).toBe(false);
    expect(composer.selectedSignalAt("u/l/i")).toBe(EVIDENCE.message);
    daemon.release();
    await sent;

    expect(composer.signalStatus("u/l/i")).toBe(
      "Could not send feedback.",
    );
    expect(composer.signalWorkingAt("u/l/i")).toBe(false);
    expect(composer.selectedSignalAt("u/l/i")).toBeNull();
  });

  it("restores an accepted selection when a later retry is rejected", async () => {
    let retryReply: ((reply: PostReply) => void) | undefined;
    let attempt = 0;
    const post: Post = async () => {
      attempt += 1;
      if (attempt === 1) {
        return { ok: true, timestamp: "" };
      }
      return new Promise<PostReply>((resolve) => {
        retryReply = resolve;
      });
    };
    const composer = createComposer(post);

    await composer.sendSignal("u/l/i", "u/l/i", EVIDENCE);
    const retry = composer.sendSignal("u/l/i", "u/l/i", DEEPER);
    expect(composer.selectedSignalAt("u/l/i")).toBe(DEEPER.message);

    retryReply?.({ ok: false, timestamp: "" });
    await retry;

    expect(composer.signalWorkingAt("u/l/i")).toBe(true);
    expect(composer.selectedSignalAt("u/l/i")).toBe(EVIDENCE.message);
  });

  it("restores an older success when a newer request is rejected", async () => {
    const replies: ((reply: PostReply) => void)[] = [];
    const post: Post = async () => new Promise<PostReply>((resolve) => {
      replies.push(resolve);
    });
    const composer = createComposer(post);

    const older = composer.sendSignal("u/l/i", "u/l/i", DENSE);
    const newer = composer.sendSignal("u/l/i", "u/l/i", DEEPER);

    replies[0]?.({ ok: true, timestamp: "" });
    await older;
    expect(composer.selectedSignalAt("u/l/i")).toBe(DEEPER.message);

    replies[1]?.({ ok: false, timestamp: "" });
    await newer;
    expect(composer.selectedSignalAt("u/l/i")).toBe(DENSE.message);
    expect(createComposer().selectedSignalAt("u/l/i")).toBe(DENSE.message);
  });

  it("does not let an older success replace a newer success", async () => {
    const replies: ((reply: PostReply) => void)[] = [];
    const post: Post = async () => new Promise<PostReply>((resolve) => {
      replies.push(resolve);
    });
    const composer = createComposer(post);

    const older = composer.sendSignal("u/l/i", "u/l/i", DENSE);
    const newer = composer.sendSignal("u/l/i", "u/l/i", DEEPER);
    expect(composer.selectedSignalAt("u/l/i")).toBe(DEEPER.message);

    replies[1]?.({ ok: true, timestamp: "" });
    await newer;
    expect(composer.selectedSignalAt("u/l/i")).toBe(DEEPER.message);

    replies[0]?.({ ok: true, timestamp: "" });
    await older;
    expect(composer.selectedSignalAt("u/l/i")).toBe(DEEPER.message);
    expect(createComposer().selectedSignalAt("u/l/i")).toBe(DEEPER.message);
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

    const reloaded = createComposer();
    reloaded.toggleAt({ rowId: "u/l/i", anchorId: "u/l/i" });
    expect(reloaded.text()).toBe("Does this survive?");
  });

  it("owns the waiting sign from the moment of sending until a reload", async () => {
    // The seam this closes: the request in the air and the note that
    // replaces it are two different facts, and a sign built out of two facts
    // can flicker between them. One question, asked of both at once, cannot.
    const daemon = recorder();
    const composer = createComposer(daemon.post);
    const here = itemRow("u/l/i");
    const elsewhere = itemRow("u/l/other");

    expect(isWorking({ composer }, here)).toBe(false);
    composer.toggleAt({ rowId: "u/l/i", anchorId: "u/l/i" });
    composer.setText("Is anything happening?");
    const inFlight = composer.submit();

    expect(isWorking({ composer }, here)).toBe(true);
    expect(composer.pendingAt("u/l/i")[0]?.at).not.toBe("");
    expect(isWorking({ composer }, elsewhere)).toBe(false);
    daemon.release();
    await inFlight;

    expect(isWorking({ composer }, here)).toBe(true);
  });

  it("uses provisional time only for display when no queue stamp returns", async () => {
    const daemon = recorder();
    const composer = createComposer(daemon.post);
    composer.toggleAt({ rowId: "u/l/i", anchorId: "u/l/i" });
    composer.setText("Match these words without a stamp");

    const inFlight = composer.submit();
    const provisional = composer.pendingAt("u/l/i")[0]?.at;
    daemon.release();
    await inFlight;

    expect(provisional).not.toBe("");
    expect(composer.pendingAt("u/l/i")[0]?.at).toBe(provisional);
  });

  it("reports one-click feedback against the anchor it belongs to", async () => {
    const daemon = recorder();
    daemon.release();
    const composer = createComposer(daemon.post);

    await composer.sendSignal("u/l/i", "u/l/i", DENSE);

    expect(daemon.sent).toEqual([
      {
        path: "signal",
        payload: {
          anchor_id: "u/l/i",
          label: DENSE.label,
          text: DENSE.message,
        },
      },
    ]);
    expect(composer.signalStatus("u/l/i")).toBe("");
    expect(composer.signalWorkingAt("u/l/i")).toBe(true);
  });
});
