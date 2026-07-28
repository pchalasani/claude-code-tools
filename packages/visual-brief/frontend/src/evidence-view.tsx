import { createMemo, For, type JSX } from "solid-js";

import type { Forensic, NestedNote } from "./document";
import { paintedEvidence, type PaintedEvidence } from "./evidence";
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
  // ``For`` keeps a row's DOM for as long as it is handed the same object, and
  // ``paintedEvidence`` builds new ones every time it runs. Left alone, a
  // publish that touched anything in this item would discard and rebuild every
  // note under it — the flicker this whole arrangement exists to be rid of, in
  // the one place that pairs rows with entries rather than reading them out of
  // the document.
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

/**
 * Hand back the object an entry was last painted under, when it still fits.
 *
 * A note is the same note when it answers to the same row id and is still the
 * same note object — which it is, because the document is patched in place. A
 * piece of raw evidence has no name of its own, so it is the same when the
 * same position still carries the same words.
 *
 * @param held - What each entry was painted under last time, keyed by name.
 * @param entries - This reading's entries, in painted order.
 * @returns The same entries, with unchanged ones keeping their identity.
 */
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
