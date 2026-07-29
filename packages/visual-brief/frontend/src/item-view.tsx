import { For, Show, type JSX } from "solid-js";

import {
  ComposeBox,
  ComposeButton,
  PendingNotes,
  SignalBar,
  WorkingSign,
} from "./compose-view";
import {
  TRUST_LABELS,
  TRUST_MARKS,
  type Item,
  type Table,
} from "./document";
import { evidenceRowId } from "./evidence";
import { EvidenceView } from "./evidence-view";
import { Markdown } from "./markdown-view";
import { orderedThreads, threadRowId, type Row } from "./outline";
import { RowShell, VisibleRow } from "./row-shell";
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
          <div class="glance">
            <Markdown text={props.item.glance} />
          </div>
          <TrustChip trust={props.item.trust} />
        </>
      }
      actions={
        <ComposeButton
          state={props.state}
          row={props.row}
          label={`Chat about ${props.item.glance}`}
        />
      }
    >
      <div class="explanation">
        <Markdown text={props.item.explanation} />
      </div>
      <For each={props.item.tables ?? []}>
        {(table) => <TableView table={table} />}
      </For>
      <Show when={(props.item.forensics ?? []).length > 0}>
        <VisibleRow state={props.state} id={evidenceRowId(props.row.id)}>
          {(row) => (
            <EvidenceView
              state={props.state}
              row={row}
              entries={props.item.forensics ?? []}
            />
          )}
        </VisibleRow>
      </Show>
      <For each={orderedThreads(props.item.questions)}>
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
      <WorkingSign state={props.state} row={props.row} />
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
