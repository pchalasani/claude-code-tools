import { createSignal, type Accessor } from "solid-js";
import { createHumanState, type HumanState } from "./human-state";
import { createPending, type Pending, type PendingNote } from "./pending";
import { createSignalWork, type SignalWork } from "./signal-work";
import type { SuggestedReply } from "./document";
export interface ComposeTarget {
  rowId: string;
  anchorId: string;
  parentId?: string;
  keepOpenAfterSubmit?: boolean;
}
export interface PostReply {
  ok: boolean;
  timestamp: string;
}
export type Post = (path: string, payload: unknown) => Promise<PostReply>;
export type Release = (rowId: string, sent: boolean) => void;
export interface Composer {
  target: Accessor<ComposeTarget | null>;
  isOpenAt: (rowId: string) => boolean;
  toggleAt: (target: ComposeTarget) => void;
  retarget: (target: ComposeTarget) => void;
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
  sendSignal: (
    rowId: string,
    anchorId: string,
    suggestion: SuggestedReply,
  ) => Promise<void>;
  signalStatus: (rowId: string) => string;
  signalWorkingAt: (rowId: string) => boolean;
  selectedSignalAt: (rowId: string) => string | null;
}
interface SignalRequest {
  baseline: string | null;
  key: string;
  label: string;
  sequence: number;
}
export async function postJson(path: string, payload: unknown): Promise<PostReply> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return { ok: response.ok, timestamp: await queuedTimestamp(response) };
}
async function queuedTimestamp(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    const stamped = body !== null && typeof body === "object"
      ? (body as Record<string, unknown>).timestamp
      : null;
    return typeof stamped === "string" ? stamped : "";
  } catch {
    return "";
  }
}
export function createComposer(
  post: Post = postJson,
  release: Release = () => undefined,
  pending: Pending = createPending(),
  human: HumanState = createHumanState(),
  signalWork: SignalWork = createSignalWork(),
): Composer {
  const [target, setTarget] = createSignal<ComposeTarget | null>(null);
  const [sendingRow, setSendingRow] = createSignal<string | null>(null);
  const [status, setStatus] = createSignal("");
  const [discardArmed, setDiscardArmed] = createSignal<string | null>(null);
  const [signalRequests, setSignalRequests] = createSignal<
    Record<string, SignalRequest[]>
  >({});
  const [signalOutcomes, setSignalOutcomes] = createSignal<
    Record<string, { sequence: number; message: string }>
  >({});
  const [signalSelections, setSignalSelections] = createSignal<
    Record<string, { sequence: number; signal: string | null }>
  >({});
  let signalSequence = 0;
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
      at: "",
      displayAt: new Date().toISOString(),
    });
    setSendingRow(current.rowId);
    if (current.keepOpenAfterSubmit === true) {
      setStatus("");
      setDiscardArmed(null);
    } else {
      clearActive();
    }
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
    retarget: (wanted) => {
      if (target()?.rowId === wanted.rowId) {
        setTarget(wanted);
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
    sendSignal: async (rowId, anchorId, suggestion) => {
      const signal = suggestion.message;
      const label = suggestion.label;
      const key = `${rowId}:${signal}`;
      if (
        signalWork.selectedAt(rowId) === signal
        || signalRequests()[rowId]?.some((request) => request.key === key)
      ) {
        return;
      }
      signalSequence += 1;
      const request = {
        baseline: signalWork.baseline(),
        key,
        label,
        sequence: signalSequence,
      };
      setSignalSelections((current) => ({
        ...current,
        [rowId]: { sequence: request.sequence, signal },
      }));
      setSignalRequests((current) => ({
        ...current,
        [rowId]: [...(current[rowId] ?? []), request],
      }));
      try {
        const reply = await post("signal", {
          anchor_id: anchorId,
          label,
          text: suggestion.message,
        });
        if (reply.ok) {
          const latest = signalSelections()[rowId]?.sequence
            === request.sequence;
          signalWork.accept(
            rowId,
            signal,
            request.baseline,
            request.sequence,
            suggestion.message,
            reply.timestamp,
          );
          if (latest) {
            setSignalSelections((current) => withoutKey(current, rowId));
          }
          setSignalOutcome(rowId, request.sequence, "");
        } else {
          clearRejectedSelection(rowId, request.sequence);
          setSignalOutcome(
            rowId,
            request.sequence,
            "Could not send feedback.",
          );
        }
      } catch {
        clearRejectedSelection(rowId, request.sequence);
        setSignalOutcome(
          rowId,
          request.sequence,
          "Could not send feedback.",
        );
      } finally {
        setSignalRequests((current) => ({
          ...current,
          [rowId]: (current[rowId] ?? []).filter(
            (active) => active.sequence !== request.sequence,
          ),
        }));
      }
    },
    signalStatus: (rowId) => {
      const active = signalRequests()[rowId] ?? [];
      const latest = active.at(-1);
      return latest === undefined
        ? signalOutcomes()[rowId]?.message ?? ""
        : `Sending ${latest.label}…`;
    },
    signalWorkingAt: signalWork.at,
    selectedSignalAt: (rowId) => {
      const current = signalSelections()[rowId];
      return current === undefined ? signalWork.selectedAt(rowId) : current.signal;
    },
  };

  function clearRejectedSelection(rowId: string, sequence: number): void {
    setSignalSelections((current) =>
      current[rowId]?.sequence === sequence
        ? withoutKey(current, rowId)
        : current
    );
  }

  function setSignalOutcome(
    rowId: string,
    sequence: number,
    message: string,
  ): void {
    setSignalOutcomes((current) => {
      if ((current[rowId]?.sequence ?? -1) > sequence) {
        return current;
      }
      return { ...current, [rowId]: { sequence, message } };
    });
  }
}

function withoutKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record };
  delete next[key];
  return next;
}
