/**
 * The document's rows, read through a document that changes.
 *
 * ``outline`` is a pure function of the delivered document, so every publish
 * produces an entirely new list of ``Row`` objects. That is fine for anything
 * computed from the list — counts, filters, what is painted — and fatal for
 * anything holding one: the rendered tree hands each row object to a component
 * once, and a component holding last week's object paints last week's chip.
 *
 * So there are two readings here. The list is a memo and is rebuilt whenever
 * the document changes, and a row looked up by id is a stable object whose
 * every field is read through that memo. The rendered tree keeps its object —
 * and therefore its DOM — while what the object says stays current.
 */

import { createMemo, type Accessor } from "solid-js";

import { countChats } from "./cursor";
import type { BriefDocument } from "./document";
import { awaitingThreadCount, outline, type Row } from "./outline";

/** The rows of the document being shown, as live readings. */
export interface RowIndex {
  /** Every row, in document order, as the document stands now. */
  rows: Accessor<Row[]>;
  /** The ids of those rows. */
  ids: Accessor<ReadonlySet<string>>;
  /**
   * Look one row up by id.
   *
   * The object handed back belongs to the id rather than to one reading of the
   * document, so whoever holds it keeps holding the same row.
   */
  row: (id: string) => Row | undefined;
  /** How many question threads await an answer. */
  awaitingCount: Accessor<number>;
  /** How many conversations the human has written in. */
  chatCount: Accessor<number>;
}

/**
 * Build the row index for a document that can change.
 *
 * @param brief - The document being shown, read live.
 * @returns The live rows.
 */
export function createRowIndex(brief: Accessor<BriefDocument>): RowIndex {
  const rows = createMemo(() => outline(brief()));
  const byId = createMemo(
    () => new Map(rows().map((row) => [row.id, row])),
  );
  const ids = createMemo(() => new Set(byId().keys()));
  // One stable view per id ever asked for. It is bounded by the ids this tab
  // has seen, and a view whose row has gone is never handed out again — the
  // lookup below asks the current document first.
  const held = new Map<string, Row>();
  return {
    rows,
    ids,
    row: (id) => {
      if (!byId().has(id)) {
        return undefined;
      }
      let view = held.get(id);
      if (view === undefined) {
        view = liveRow(id, byId);
        held.set(id, view);
      }
      return view;
    },
    awaitingCount: createMemo(() => awaitingThreadCount(rows())),
    chatCount: createMemo(() => countChats(rows())),
  };
}

/**
 * Build one row's stable view onto whichever document is current.
 *
 * The fallbacks are never reached in practice — a view is only handed out for
 * a row the current document holds — and they are there so that a row read one
 * instant after it went away answers something inert rather than throwing in
 * the middle of a paint.
 *
 * @param id - The row's id, which is the one thing about it that cannot move.
 * @param byId - The document's rows, keyed by id.
 * @returns A row object whose every field is a live reading.
 */
function liveRow(
  id: string,
  byId: Accessor<ReadonlyMap<string, Row>>,
): Row {
  const now = (): Row | undefined => byId().get(id);
  return {
    get id(): string {
      return now()?.id ?? id;
    },
    get kind(): Row["kind"] {
      return now()?.kind ?? "item";
    },
    get anchorId(): string {
      return now()?.anchorId ?? id;
    },
    get parentThreadId(): string | undefined {
      return now()?.parentThreadId;
    },
    get parentId(): string | null {
      return now()?.parentId ?? null;
    },
    get label(): string {
      return now()?.label ?? "";
    },
    get search(): string {
      return now()?.search ?? "";
    },
    get awaiting(): boolean {
      return now()?.awaiting ?? false;
    },
    get human(): boolean {
      return now()?.human ?? false;
    },
    get answerState(): string | undefined {
      return now()?.answerState;
    },
  };
}
