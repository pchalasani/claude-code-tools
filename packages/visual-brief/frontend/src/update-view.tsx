import { For, Show, type JSX } from "solid-js";

import { ComposeBox, ComposeButton, PendingNotes } from "./compose-view";
import type { Lane, Update } from "./document";
import {
  NOW_UPDATE_ID,
  itemRowId,
  laneRowId,
  threadRowId,
  type Row,
} from "./outline";
import { ItemView } from "./item-view";
import { AwaitingChip, RowShell, VisibleRow } from "./row-shell";
import type { BriefState } from "./state";
import { ThreadView } from "./thread-view";

/**
 * One published update, holding its lanes.
 *
 * The update carrying the reserved ``now`` id is not history: it is the Now
 * panel, pinned first by the outline and marked here so its head reads as
 * current state ("as of ...") rather than as a dated event.
 *
 * @param props - The update, its row and the page state.
 * @returns The rendered update.
 */
export function UpdateView(props: {
  state: BriefState;
  row: Row;
  update: Update;
}): JSX.Element {
  const isNow = () => props.update.id === NOW_UPDATE_ID;
  return (
    <RowShell
      state={props.state}
      row={props.row}
      head={
        <>
          <Show when={isNow()}>
            <span class="now-mark">Now</span>
          </Show>
          <span class="update-title">{props.update.headline}</span>
          <AwaitingChip when={props.row.awaiting} />
          <time class="update-time">
            {isNow()
              ? `as of ${props.update.timestamp}`
              : props.update.timestamp}
          </time>
        </>
      }
    >
      <p class="update-summary">{props.update.summary}</p>
      <For each={props.update.lanes ?? []}>
        {(lane) => (
          <VisibleRow
            state={props.state}
            id={laneRowId(props.update.id, lane)}
          >
            {(row) => <LaneView state={props.state} row={row} lane={lane} />}
          </VisibleRow>
        )}
      </For>
    </RowShell>
  );
}

/**
 * One lane of an update, holding its items and its own conversations.
 *
 * @param props - The lane, its row and the page state.
 * @returns The rendered lane.
 */
export function LaneView(props: {
  state: BriefState;
  row: Row;
  lane: Lane;
}): JSX.Element {
  const shown = () =>
    (props.lane.items ?? []).filter((item) =>
      props.state.nav.isVisible(itemRowId(props.row.id, item)),
    ).length;
  return (
    <RowShell
      state={props.state}
      row={props.row}
      head={
        <>
          <span class="lane-name">{props.lane.name}</span>
          <AwaitingChip when={props.row.awaiting} />
          <span class="row-count">{shown()}</span>
        </>
      }
      actions={
        <ComposeButton
          state={props.state}
          row={props.row}
          label={`Ask about ${props.lane.name}`}
        />
      }
    >
      <For each={props.lane.items ?? []}>
        {(item) => (
          <VisibleRow state={props.state} id={itemRowId(props.row.id, item)}>
            {(row) => <ItemView state={props.state} row={row} item={item} />}
          </VisibleRow>
        )}
      </For>
      <For each={props.lane.questions ?? []}>
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
    </RowShell>
  );
}
