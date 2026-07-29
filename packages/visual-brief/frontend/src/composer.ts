/**
 * What the human is writing, and where it is going.
 *
 * The page has one composer at a time and it always has a target: an anchor
 * to attach to, and optionally a thread to continue. The target is a value
 * rather than something inferred from which button was clicked, so the same
 * composer serves a question about an item and a reply inside a thread.
 */

import { createSignal, type Accessor } from "solid-js";

import { readDrafts, saveDrafts, type Drafts } from "./draft-store";
import { createPending, type Pending, type PendingNote } from "./pending";

/** Where a composed message is going. */
export interface ComposeTarget {
  /** Row whose composer is open. */
  rowId: string;
  /** Anchor path the message attaches to. */
  anchorId: string;
  /** Thread the message continues, when it continues one. */
  parentId?: string;
}

/** What the daemon said about one record it was given. */
export interface PostReply {
  /** Whether it accepted the record. */
  ok: boolean;
  /**
   * The timestamp it wrote on the queue line, or empty when it did not say.
   *
   * This is half of the identity a sent message is later recognised by, so
   * the page can tell its own question from an identical one asked a minute
   * earlier once both have been folded into the document.
   */
  timestamp: string;
}

/** The fixed vocabulary of one-click feedback. */
export const SIGNALS: [string, string][] = [
  ["too-dense", "Too dense"],
  ["show-evidence", "Show evidence"],
  ["go-deeper", "Go deeper"],
  ["skip", "Skip"],
];

/** Sends one record to the local daemon. */
export type Post = (path: string, payload: unknown) => Promise<PostReply>;

/**
 * Told whenever the composer lets go of a row.
 *
 * ``sent`` separates abandoning the box from writing into it: a row the page
 * expanded only to host the composer is handed back when the human walks
 * away, and kept open when there is now a note in it to read.
 */
export type Release = (rowId: string, sent: boolean) => void;

/** The composition state of one open brief. */
export interface Composer {
  /** Where the open composer is pointed, when one is open. */
  target: Accessor<ComposeTarget | null>;
  /** Whether the composer is open at one row. */
  isOpenAt: (rowId: string) => boolean;
  /** Open the composer at a target, or close it if already there. */
  toggleAt: (target: ComposeTarget) => void;
  /** Close the composer while preserving its row's draft. */
  close: () => void;
  /** Handle Escape, requiring confirmation before discarding words. */
  escape: () => void;
  /** Explicitly discard the open row's draft and close it. */
  discard: () => void;
  /** The text being written. */
  text: Accessor<string>;
  /** Replace the text being written. */
  setText: (value: string) => void;
  /** Whether a request is in flight. */
  sending: Accessor<boolean>;
  /** Whether the request in flight was written at one row. */
  sendingAt: (rowId: string) => boolean;
  /** What to tell the human about the last attempt. */
  status: Accessor<string>;
  /** Send what is written to its target. */
  submit: () => Promise<void>;
  /** Messages sent from this page that are still unanswered. */
  pendingAt: (rowId: string) => PendingNote[];
  /** Report one fixed-vocabulary signal about an anchor. */
  sendSignal: (rowId: string, anchorId: string, signal: string) => Promise<void>;
  /** What to tell the human about a row's signal, in flight or landed. */
  signalStatus: (rowId: string) => string;
}

/**
 * Send one JSON record to the local daemon.
 *
 * @param path - Run-relative endpoint.
 * @param payload - Record to send.
 * @returns Whether the daemon accepted it.
 */
export async function postJson(
  path: string,
  payload: unknown,
): Promise<PostReply> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return { ok: response.ok, timestamp: await queuedTimestamp(response) };
}

/**
 * Read the timestamp a daemon reports for the record it just queued.
 *
 * An older daemon says nothing about it, and nothing is a perfectly good
 * answer: a message with no timestamp is recognised by its words alone.
 *
 * @param response - The daemon's answer.
 * @returns The queue timestamp, or an empty string.
 */
async function queuedTimestamp(response: Response): Promise<string> {
  try {
    const body: unknown = JSON.parse(await response.text());
    if (body !== null && typeof body === "object") {
      const stamped = (body as Record<string, unknown>).timestamp;
      return typeof stamped === "string" ? stamped : "";
    }
  } catch {
    // An empty or unreadable body is not a failure to send.
  }
  return "";
}

/**
 * Build the composition state for one open brief.
 *
 * @param post - How records reach the daemon.
 * @param release - Told which row the composer just let go of, and why.
 * @param pending - Where sent messages wait to be seen arriving.
 * @returns The live composer.
 */
export function createComposer(
  post: Post = postJson,
  release: Release = () => undefined,
  pending: Pending = createPending(),
): Composer {
  const [target, setTarget] = createSignal<ComposeTarget | null>(null);
  const [text, setText] = createSignal("");
  const [sending, setSending] = createSignal(false);
  const [status, setStatus] = createSignal("");
  const [signals, setSignals] = createSignal<Record<string, string>>({});
  const [inFlight, setInFlight] = createSignal<ReadonlySet<string>>(new Set());
  const [drafts, setDrafts] = createSignal<Drafts>(readDrafts());
  const [discardArmed, setDiscardArmed] = createSignal<string | null>(null);

  const clearActive = (): void => {
    setTarget(null);
    setText("");
    setStatus("");
    setDiscardArmed(null);
  };

  const letGo = (rowId: string, sent: boolean): void => {
    clearActive();
    release(rowId, sent);
  };

  const close = (): void => {
    const current = target();
    if (current === null) {
      clearActive();
      return;
    }
    letGo(current.rowId, false);
  };

  const replaceDrafts = (next: Drafts): void => {
    setDrafts(next);
    saveDrafts(next);
  };

  const writeText = (value: string): void => {
    setText(value);
    setDiscardArmed(null);
    const current = target();
    if (current === null) {
      return;
    }
    const next = { ...drafts() };
    if (value === "") {
      delete next[current.rowId];
    } else {
      next[current.rowId] = value;
    }
    replaceDrafts(next);
  };

  const clearDraft = (rowId: string): void => {
    const next = { ...drafts() };
    delete next[rowId];
    replaceDrafts(next);
  };

  const discard = (): void => {
    const current = target();
    if (current === null) {
      return;
    }
    clearDraft(current.rowId);
    letGo(current.rowId, false);
  };

  const escape = (): void => {
    const current = target();
    if (current === null) {
      return;
    }
    if (text().trim() === "") {
      clearDraft(current.rowId);
      letGo(current.rowId, false);
      return;
    }
    if (discardArmed() === current.rowId) {
      discard();
      return;
    }
    setDiscardArmed(current.rowId);
    setStatus("Press Escape again to discard this draft.");
  };

  const openAt = (wanted: ComposeTarget): void => {
    setTarget(wanted);
    setText(drafts()[wanted.rowId] ?? "");
    setStatus("");
    setDiscardArmed(null);
  };

  const submit = async (): Promise<void> => {
    const current = target();
    const written = text().trim();
    if (current === null || sending()) {
      return;
    }
    if (written === "") {
      setStatus("Write something first.");
      return;
    }
    setSending(true);
    setStatus("Sending…");
    const payload: Record<string, string> = {
      anchor_id: current.anchorId,
      text: written,
    };
    if (current.parentId !== undefined) {
      payload.parent_id = current.parentId;
    }
    try {
      const reply = await post("ask", payload);
      if (!reply.ok) {
        throw new Error("not accepted");
      }
      clearDraft(current.rowId);
      pending.add({
        rowId: current.rowId,
        anchorId: current.anchorId,
        text: written,
        at: reply.timestamp,
      });
      letGo(current.rowId, true);
    } catch {
      setStatus("Could not send. Is the local server running?");
    } finally {
      setSending(false);
    }
  };

  return {
    target,
    isOpenAt: (rowId) => target()?.rowId === rowId,
    toggleAt: (wanted) => {
      const current = target();
      close();
      if (current?.rowId === wanted.rowId) {
        return;
      }
      openAt(wanted);
    },
    close,
    escape,
    discard,
    text,
    setText: writeText,
    sending,
    sendingAt: (rowId) => sending() && target()?.rowId === rowId,
    status,
    submit,
    pendingAt: (rowId) => pending.at(rowId),
    sendSignal: async (rowId, anchorId, signal) => {
      const label = SIGNALS.find(([name]) => name === signal)?.[1] ?? signal;
      // Keyed on the button, not on the row: a double-click collapses into
      // one report, while a human who presses a second, different signal
      // gets the report they asked for instead of silence.
      const key = `${rowId}:${signal}`;
      if (inFlight().has(key)) {
        return;
      }
      setInFlight((current) => new Set(current).add(key));
      setSignals((current) => ({
        ...current,
        [rowId]: `Sending ${label}…`,
      }));
      try {
        const reply = await post("signal", {
          anchor_id: anchorId,
          signal,
        });
        setSignals((current) => ({
          ...current,
          [rowId]: reply.ok
            ? `Feedback received: ${label}`
            : "Could not send feedback.",
        }));
      } catch {
        setSignals((current) => ({
          ...current,
          [rowId]: "Could not send feedback.",
        }));
      } finally {
        setInFlight((current) => {
          const next = new Set(current);
          next.delete(key);
          return next;
        });
      }
    },
    signalStatus: (rowId) => signals()[rowId] ?? "",
  };
}
