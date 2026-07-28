import { createMemo, For, type JSX } from "solid-js";

import type { Forensic, NestedNote } from "./document";
import { paintedEvidence } from "./evidence";
import { Markdown } from "./markdown-view";
import type { Row } from "./outline";
import { RowShell, VisibleRow } from "./row-shell";
import type { BriefState } from "./state";

/**
 * The fold holding one item's raw evidence.
 *
 * It is an ordinary row, which is the point: the keyboard reaches it the same
 * way it reaches everything else, and what the outline believes about the
 * page is what the page paints.
 *
 * @param props - The evidence row, its entries and the page state.
 * @returns The rendered fold.
 */
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

/**
 * One note of evidence, holding its body and whatever nests below it.
 *
 * @param props - The note's row, the note and the page state.
 * @returns The rendered note.
 */
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

/**
 * Paint one list of evidence entries in the order the outline lists them.
 *
 * A plain string has no head to fold, so it is content rather than a row and
 * is on the page as soon as its owner opens. A note has a title, so it is a
 * row, and it is painted under the very id the outline gave it: both come
 * from ``paintedEvidence``, so the page and the cursor cannot disagree about
 * which note is which.
 *
 * @param props - The owning row's id, its entries and the page state.
 * @returns The rendered entries.
 */
function EvidenceEntries(props: {
  state: BriefState;
  parentId: string;
  entries: Forensic[];
}): JSX.Element {
  const painted = createMemo(() =>
    paintedEvidence(props.entries, props.parentId),
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
