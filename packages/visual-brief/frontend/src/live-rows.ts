import { createMemo, type Accessor } from "solid-js";
import type { BriefDocument, SuggestedReply } from "./document";
import { awaitingThreadCount, outline, type Row } from "./outline";
export interface RowIndex {
  rows: Accessor<Row[]>;
  ids: Accessor<ReadonlySet<string>>;
  row: (id: string) => Row | undefined;
  awaitingCount: Accessor<number>;
}
export function createRowIndex(brief: Accessor<BriefDocument>): RowIndex {
  const rows = createMemo(() => outline(brief()));
  const byId = createMemo(
    () => new Map(rows().map((row) => [row.id, row])),
  );
  const ids = createMemo(() => new Set(byId().keys()));
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
  };
}
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
    get suggestions(): SuggestedReply[] | undefined {
      return now()?.suggestions;
    },
  };
}
