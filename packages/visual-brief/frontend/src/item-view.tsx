import { For, Show, createSignal, type JSX } from "solid-js";

import {
  ComposeBox,
  ComposeButton,
  PendingNotes,
  SignalBar,
} from "./compose-view";
import {
  TRUST_LABELS,
  TRUST_MARKS,
  type Forensic,
  type Item,
  type Table,
} from "./document";
import { threadRowId, type Row } from "./outline";
import { AwaitingChip, RowShell, VisibleRow } from "./row-shell";
import type { BriefState } from "./state";
import { ThreadView } from "./thread-view";

/**
 * One item: a glance line that opens into the reasoning under it.
 *
 * @param props - The item, its row and the page state.
 * @returns The rendered item.
 */
export function ItemView(props: {
  state: BriefState;
  row: Row;
  item: Item;
}): JSX.Element {
  return (
    <RowShell
      state={props.state}
      row={props.row}
      head={
        <>
          <span class="glance">{props.item.glance}</span>
          <AwaitingChip when={props.row.awaiting} />
          <TrustChip trust={props.item.trust} />
        </>
      }
      actions={
        <ComposeButton
          state={props.state}
          row={props.row}
          label={`Ask about ${props.item.glance}`}
        />
      }
    >
      <p class="explanation">{props.item.explanation}</p>
      <For each={props.item.tables ?? []}>
        {(table) => <TableView table={table} />}
      </For>
      <Show when={(props.item.forensics ?? []).length > 0}>
        <Disclosure label="Raw evidence and deeper forensics">
          <For each={props.item.forensics ?? []}>
            {(entry) => <ForensicView entry={entry} />}
          </For>
        </Disclosure>
      </Show>
      <For each={props.item.questions ?? []}>
        {(thread) => (
          <VisibleRow
            state={props.state}
            id={threadRowId(props.row.id, thread)}
          >
            {(row) => (
              <ThreadView state={props.state} row={row} thread={thread} />
            )}
          </VisibleRow>
        )}
      </For>
      <PendingNotes state={props.state} row={props.row} />
      <ComposeBox state={props.state} row={props.row} />
      <SignalBar state={props.state} row={props.row} />
    </RowShell>
  );
}

/**
 * The chip carrying how far an item has been checked.
 *
 * @param props - The item's trust level.
 * @returns The chip.
 */
export function TrustChip(props: { trust: Item["trust"] }): JSX.Element {
  return (
    <span class={`chip chip-trust trust-${props.trust}`}>
      <span class="chip-mark" aria-hidden="true">
        {TRUST_MARKS[props.trust]}
      </span>
      {TRUST_LABELS[props.trust]}
    </span>
  );
}

/**
 * A fold that is part of the reading, not part of the navigation.
 *
 * Evidence nests arbitrarily deep and is not something the cursor steps
 * through, so these keep their own open state rather than becoming rows.
 *
 * @param props - The summary label and the content it hides.
 * @returns The rendered fold.
 */
function Disclosure(props: {
  label: string;
  children: JSX.Element;
}): JSX.Element {
  const [open, setOpen] = createSignal(false);
  return (
    <div class="fold" data-open={open() ? "true" : "false"}>
      <button
        type="button"
        class="fold-head"
        aria-expanded={open()}
        onClick={() => setOpen(!open())}
      >
        <span class="row-fold" aria-hidden="true">
          {open() ? "▾" : "▸"}
        </span>
        {props.label}
      </button>
      <Show when={open()}>
        <div class="fold-body">{props.children}</div>
      </Show>
    </div>
  );
}

/**
 * One piece of evidence: raw text, or a note that nests further.
 *
 * @param props - The forensic entry.
 * @returns The rendered evidence.
 */
function ForensicView(props: { entry: Forensic }): JSX.Element {
  return (
    <Show
      when={typeof props.entry === "string" ? undefined : props.entry}
      fallback={<pre class="evidence">{String(props.entry)}</pre>}
    >
      {(note) => (
        <Disclosure label={note().title}>
          <p class="note-body">{note().body}</p>
          <For each={note().children ?? []}>
            {(child) => <ForensicView entry={child} />}
          </For>
        </Disclosure>
      )}
    </Show>
  );
}

/**
 * One comparison table.
 *
 * @param props - The table.
 * @returns The rendered table inside its own scroll box.
 */
function TableView(props: { table: Table }): JSX.Element {
  return (
    <div class="table-wrap">
      <table>
        <caption>{props.table.caption}</caption>
        <thead>
          <tr>
            <For each={props.table.columns}>
              {(column) => <th scope="col">{column}</th>}
            </For>
          </tr>
        </thead>
        <tbody>
          <For each={props.table.rows}>
            {(row) => (
              <tr>
                <For each={row}>
                  {(cell) => (
                    <td
                      class={
                        String(cell).startsWith("WRONG") ? "wrong" : undefined
                      }
                    >
                      {cell}
                    </td>
                  )}
                </For>
              </tr>
            )}
          </For>
        </tbody>
      </table>
    </div>
  );
}
