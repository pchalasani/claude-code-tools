import { createSignal, type Accessor } from "solid-js";
import { createHumanState, type HumanState } from "./human-state";
import { createPending, type Pending, type PendingNote } from "./pending";
export interface ComposeTarget {
  rowId: string;
  anchorId: string;
  parentId?: string;
}
export interface PostReply {
  ok: boolean;
  timestamp: string;
}
export const SIGNALS: [string, string][] = [
  ["too-dense", "Too dense"],
  ["show-evidence", "Show evidence"],
  ["go-deeper", "Go deeper"],
  ["skip", "Skip"],
];
export type Post = (path: string, payload: unknown) => Promise<PostReply>;
export type Release = (rowId: string, sent: boolean) => void;
export interface Composer {
  target: Accessor<ComposeTarget | null>;
  isOpenAt: (rowId: string) => boolean;
  toggleAt: (target: ComposeTarget) => void;
  close: () => void;
  escape: () => void;
  discard: () => void;
  text: Accessor<string>;
  setText: (value: string) => void;
  sending: Accessor<boolean>;
  sendingAt: (rowId: string) => boolean;
  status: Accessor<string>;
  draftWarning: Accessor<string>;
  submit: () => Promise<void>;
  pendingAt: (rowId: string) => PendingNote[];
  sendSignal: (rowId: string, anchorId: string, signal: string) => Promise<void>;
  signalStatus: (rowId: string) => string;
}
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
async function queuedTimestamp(response: Response): Promise<string> {
  try {
    const body: unknown = JSON.parse(await response.text());
    if (body !== null && typeof body === "object") {
      const stamped = (body as Record<string, unknown>).timestamp;
      return typeof stamped === "string" ? stamped : "";
    }
  } catch {
  }
  return "";
}
export function createComposer(
  post: Post = postJson,
  release: Release = () => undefined,
  pending: Pending = createPending(),
  human: HumanState = createHumanState(),
): Composer {
  const [target, setTarget] = createSignal<ComposeTarget | null>(null);
  const [sendingRow, setSendingRow] = createSignal<string | null>(null);
  const [status, setStatus] = createSignal("");
  const [discardArmed, setDiscardArmed] = createSignal<string | null>(null);
  const [signals, setSignals] = createSignal<Record<string, string>>({});
  const [inFlight, setInFlight] = createSignal<ReadonlySet<string>>(new Set());
  const text = (): string => {
    const current = target();
    if (current === null) {
      return "";
    }
    return pending.failureAt(current.rowId)
      ?? human.drafts[current.rowId]
      ?? "";
  };
  const persistFailed = (current: ComposeTarget): void => {
    const failed = pending.failureAt(current.rowId);
    if (failed !== null) {
      human.writeDraft(current.rowId, failed);
      pending.clearFailure(current.rowId);
    }
  };
  const clearActive = (): void => {
    setTarget(null);
    setStatus("");
    setDiscardArmed(null);
  };
  const close = (): void => {
    const current = target();
    if (current !== null) {
      persistFailed(current);
    }
    clearActive();
    if (current !== null) {
      release(current.rowId, false);
    }
  };
  const openAt = (wanted: ComposeTarget): void => {
    setTarget(wanted);
    setStatus("");
    setDiscardArmed(null);
  };
  const discard = (): void => {
    const current = target();
    if (current === null) {
      return;
    }
    human.discardDraft(current.rowId);
    pending.clearFailure(current.rowId);
    clearActive();
    release(current.rowId, false);
  };
  const submit = async (): Promise<void> => {
    const current = target();
    const written = text().trim();
    if (current === null || sendingRow() !== null) {
      return;
    }
    if (written === "") {
      setStatus("Write something first.");
      return;
    }
    const payload: Record<string, string> = {
      anchor_id: current.anchorId,
      text: written,
    };
    if (current.parentId !== undefined) {
      payload.parent_id = current.parentId;
    }
    human.discardDraft(current.rowId);
    pending.clearFailure(current.rowId);
    const token = pending.begin({
      rowId: current.rowId,
      anchorId: current.anchorId,
      text: written,
      at: new Date().toISOString(),
    });
    setSendingRow(current.rowId);
    clearActive();
    release(current.rowId, true);
    try {
      const reply = await post("ask", payload);
      if (!reply.ok) {
        throw new Error("not accepted");
      }
      pending.stamp(token, reply.timestamp);
    } catch {
      pending.fail(token);
      openAt(current);
      setStatus("Could not send. Is the local server running?");
    } finally {
      setSendingRow(null);
    }
  };
  return {
    target,
    isOpenAt: (rowId) => target()?.rowId === rowId,
    toggleAt: (wanted) => {
      const current = target();
      if (current?.rowId === wanted.rowId) {
        close();
      } else {
        if (current !== null) {
          persistFailed(current);
          release(current.rowId, false);
        }
        openAt(wanted);
      }
    },
    close,
    escape: () => {
      const current = target();
      if (current === null) {
        return;
      }
      if (text().trim() === "") {
        discard();
      } else if (discardArmed() === current.rowId) {
        discard();
      } else {
        setDiscardArmed(current.rowId);
        setStatus("Press Escape again to discard this draft.");
      }
    },
    discard,
    text,
    setText: (value) => {
      const current = target();
      if (current !== null) {
        human.writeDraft(current.rowId, value);
        pending.clearFailure(current.rowId);
        setDiscardArmed(null);
        setStatus("");
      }
    },
    sending: () => sendingRow() !== null,
    sendingAt: (rowId) => sendingRow() === rowId,
    status,
    draftWarning: human.draftWarning,
    submit,
    pendingAt: pending.at,
    sendSignal: async (rowId, anchorId, signal) => {
      const label = SIGNALS.find(([name]) => name === signal)?.[1] ?? signal;
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
        const reply = await post("signal", { anchor_id: anchorId, signal });
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
