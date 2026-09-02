import { createEffect, createSignal, type Accessor } from "solid-js";
import {
  readAcceptedSignalWork,
  saveAcceptedSignalWork,
  type AcceptedSignalWork,
} from "./session-store";

export interface SignalWorkContext {
  latestUpdateId: Accessor<string | undefined>;
  rowExists: (rowId: string) => boolean;
  answered: (rowId: string, text: string, at: string) => boolean;
}

export interface SignalWork {
  baseline: () => string | null;
  accept: (
    rowId: string,
    signal: string,
    acceptedBaseline: string | null,
    requestSequence: number | undefined,
    text: string,
    at: string,
  ) => void;
  at: (rowId: string) => boolean;
  selectedAt: (rowId: string) => string | null;
}

export function createSignalWork(
  context?: SignalWorkContext,
): SignalWork {
  const baseline = (): string | null => context?.latestUpdateId() ?? null;
  const stored = readAcceptedSignalWork();
  const initial = context === undefined
    ? stored
    : reconcileSignalWork(
      stored,
      baseline(),
      context.rowExists,
      context.answered,
    );
  if (!sameSignalWork(stored, initial)) {
    saveAcceptedSignalWork(initial);
  }
  const [accepted, setAccepted] = createSignal(initial);
  const acceptedSequences: Record<
    string,
    { baseline: string | null; sequence: number }
  > = {};
  if (context !== undefined) {
    createEffect(() => {
      const current = accepted();
      const next = reconcileSignalWork(
        current,
        baseline(),
        context.rowExists,
        context.answered,
      );
      if (!sameSignalWork(current, next)) {
        setAccepted(next);
        saveAcceptedSignalWork(next);
      }
    });
  }
  return {
    baseline,
    accept: (
      rowId,
      signal,
      acceptedBaseline,
      requestSequence,
      text,
      at,
    ) => {
      const previousSequence = acceptedSequences[rowId];
      if (
        acceptedBaseline !== baseline()
        || (context !== undefined && !context.rowExists(rowId))
        || context?.answered(rowId, text, at) === true
        || (
          requestSequence !== undefined
          && previousSequence?.baseline === acceptedBaseline
          && previousSequence.sequence >= requestSequence
        )
      ) {
        return;
      }
      const next = {
        ...accepted(),
        [rowId]: {
          baseline: acceptedBaseline,
          at,
          signal,
          text,
        },
      };
      setAccepted(next);
      saveAcceptedSignalWork(next);
      if (requestSequence !== undefined) {
        acceptedSequences[rowId] = {
          baseline: acceptedBaseline,
          sequence: requestSequence,
        };
      }
    },
    at: (rowId) => Object.hasOwn(accepted(), rowId),
    selectedAt: (rowId) => accepted()[rowId]?.signal ?? null,
  };
}

export function reconcileSignalWork(
  work: AcceptedSignalWork,
  baseline: string | null,
  rowExists: (rowId: string) => boolean,
  answered: (rowId: string, text: string, at: string) => boolean,
): AcceptedSignalWork {
  return Object.fromEntries(
    Object.entries(work).filter(
      ([rowId, record]) =>
        record.baseline === baseline
        && record.at !== undefined
        && record.text !== undefined
        && !answered(rowId, record.text, record.at)
        && rowExists(rowId),
    ),
  );
}

function sameSignalWork(
  left: AcceptedSignalWork,
  right: AcceptedSignalWork,
): boolean {
  const leftEntries = Object.entries(left);
  const rightEntries = Object.entries(right);
  return leftEntries.length === rightEntries.length
    && leftEntries.every(([rowId, record]) => {
      const other = right[rowId];
      return other?.baseline === record.baseline
        && other.at === record.at
        && other.signal === record.signal
        && other.text === record.text;
    });
}
