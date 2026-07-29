import { createMemo, For, type JSX } from "solid-js";
import type { Forensic, NestedNote } from "./document";
import { paintedEvidence, type PaintedEvidence } from "./evidence";
import { Markdown } from "./markdown-view";
import type { Row } from "./outline";
import { RowShell, VisibleRow } from "./row-shell";
import type { BriefState } from "./state";
export function EvidenceView(props: {
  state: BriefState;
  row: Row;
  entries: Forensic[];
}): JSX.Element {
  return (
    <RowShell
      state={props.state}
      row={props.row}
      head={<span class="evidence-title">{props.row.label}</span>}
    >
      <EvidenceEntries
        state={props.state}
        parentId={props.row.id}
        entries={props.entries}
      />
    </RowShell>
  );
}
function NoteView(props: {
  state: BriefState;
  row: Row;
  note: NestedNote;
}): JSX.Element {
  return (
    <RowShell
      state={props.state}
      row={props.row}
      head={<span class="evidence-title">{props.note.title}</span>}
    >
      <div class="note-body">
        <Markdown text={props.note.body} />
      </div>
      <EvidenceEntries
        state={props.state}
        parentId={props.row.id}
        entries={props.note.children ?? []}
      />
    </RowShell>
  );
}
function EvidenceEntries(props: {
  state: BriefState;
  parentId: string;
  entries: Forensic[];
}): JSX.Element {
  const held = new Map<string, PaintedEvidence>();
  const painted = createMemo(() =>
    keepIdentity(held, paintedEvidence(props.entries, props.parentId)),
  );
  return (
    <For each={painted()}>
      {(entry) =>
        entry.kind === "text" ? (
          <pre class="evidence">{entry.text}</pre>
        ) : (
          <VisibleRow state={props.state} id={entry.id}>
            {(row) => (
              <NoteView state={props.state} row={row} note={entry.note} />
            )}
          </VisibleRow>
        )
      }
    </For>
  );
}
function keepIdentity(
  held: Map<string, PaintedEvidence>,
  entries: PaintedEvidence[],
): PaintedEvidence[] {
  const named = entries.map((entry, index): [string, PaintedEvidence] => [
    entry.kind === "note" ? `note:${entry.id}` : `text:${index}`,
    entry,
  ]);
  const kept = named.map(([name, entry]) => {
    const before = held.get(name);
    if (before === undefined) {
      return entry;
    }
    if (
      before.kind === "note"
      && entry.kind === "note"
      && before.note === entry.note
    ) {
      return before;
    }
    if (
      before.kind === "text"
      && entry.kind === "text"
      && before.text === entry.text
    ) {
      return before;
    }
    return entry;
  });
  held.clear();
  named.forEach(([name], index) => {
    const entry = kept[index];
    if (entry !== undefined) {
      held.set(name, entry);
    }
  });
  return kept;
}
