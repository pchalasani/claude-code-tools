import { For, type JSX } from "solid-js";

import { humanAge } from "./age";
import {
  ComposeBox,
  ComposeButton,
  PendingNotes,
  WorkingSign,
} from "./compose-view";
import type { Lane, Update } from "./document";
import {
  itemRowId,
  laneRowId,
  orderedThreads,
  threadRowId,
  type Row,
} from "./outline";
import { ItemView } from "./item-view";
import { Markdown } from "./markdown-view";
import { RowShell, VisibleRow } from "./row-shell";
import type { BriefState } from "./state";
import { ThreadView } from "./thread-view";

/**
 * One published update, holding its lanes.
 *
 * @param props - The update, its row and the page state.
 * @returns The rendered update.
 */
export function UpdateView(props: {
  state: BriefState;
  row: Row;
  update: Update;
  now: number;
}): JSX.Element {
  return (
    <RowShell
      state={props.state}
      row={props.row}
      head={
        <>
          <span class="update-title">{props.update.headline}</span>
          <span class="update-when">
            <time class="update-time" dateTime={props.update.timestamp}>
              {props.update.timestamp}
            </time>
            <span class="update-age">
              {humanAge(props.update.timestamp, props.now)}
            </span>
          </span>
        </>
      }
    >
      <div class="update-summary">
        <Markdown text={props.update.summary} />
      </div>
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
          <span class="row-count">{shown()}</span>
        </>
      }
      actions={
        <ComposeButton
          state={props.state}
          row={props.row}
          label={`Chat about ${props.lane.name}`}
        />
      }
    >
      {/*
        A conversation about the lane sits with the lane, directly under its
        head and above its items. Down at the bottom of a long lane it was
        nowhere near the thing it was about, and the chat box opened there
        too — the human clicked a control on the header and had to go looking
        for the box. The outline lists these rows in this same order, because
        the two are one list.
      */}
      <For each={orderedThreads(props.lane.questions)}>
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
      <For each={props.lane.items ?? []}>
        {(item) => (
          <VisibleRow state={props.state} id={itemRowId(props.row.id, item)}>
            {(row) => <ItemView state={props.state} row={row} item={item} />}
          </VisibleRow>
        )}
      </For>
    </RowShell>
  );
}
