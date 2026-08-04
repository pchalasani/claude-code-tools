import { For, Show, type JSX } from "solid-js";

import { formatTimestamp, humanAge } from "./age";
import {
  ComposeBox,
  ComposeButton,
  PendingNotes,
  WorkingSign,
} from "./compose-view";
import {
  isStructuredCurrentState,
  type CurrentState,
  type LegacyCurrentState,
  type StructuredCurrentState,
} from "./document";
import { Markdown } from "./markdown-view";
import {
  CURRENT_STATE_ROOT_ID,
  currentStateItemRowId,
  currentStateLaneRowId,
  orderedThreads,
  threadRowId,
} from "./outline";
import { RowShell, VisibleRow } from "./row-shell";
import type { BriefState } from "./state";
import { ThreadView } from "./thread-view";
import { LaneView } from "./update-view";

/** Render structured state as shared rows, or legacy state read-only. */
export function CurrentStateView(props: {
  state: BriefState;
  current: CurrentState;
  now: number;
}): JSX.Element {
  const structured = (): StructuredCurrentState | undefined =>
    isStructuredCurrentState(props.current) ? props.current : undefined;
  return (
    <Show
      when={structured()}
      fallback={(
        <LegacyCurrentStateView
          state={props.current as LegacyCurrentState}
          now={props.now}
        />
      )}
    >
      {(current) => (
        <VisibleRow state={props.state} id={CURRENT_STATE_ROOT_ID}>
          {(row) => (
            <RowShell
              state={props.state}
              row={row}
              head={(
                <>
                  <span class="current-state-label">Current state</span>
                  <span class="current-state-title">{current().headline}</span>
                  <span class="current-state-when">
                    <time
                      dateTime={current().updated_at}
                      title={formatTimestamp(current().updated_at)}
                    >
                      {humanAge(current().updated_at, props.now)}
                    </time>
                  </span>
                </>
              )}
              actions={(
                <ComposeButton
                  state={props.state}
                  row={row}
                  label="Chat about current state"
                />
              )}
            >
              <div class="current-state-summary">
                <Markdown text={current().summary} />
              </div>
              <For each={orderedThreads(current().questions)}>
                {(thread) => (
                  <VisibleRow
                    state={props.state}
                    id={threadRowId(row.id, thread)}
                  >
                    {(threadRow) => (
                      <ThreadView
                        state={props.state}
                        row={threadRow}
                        thread={thread}
                      />
                    )}
                  </VisibleRow>
                )}
              </For>
              <PendingNotes state={props.state} row={row} />
              <WorkingSign state={props.state} row={row} />
              <ComposeBox state={props.state} row={row} />
              <div class="current-state-lanes">
                <For each={current().lanes ?? []}>
                  {(lane) => (
                    <VisibleRow
                      state={props.state}
                      id={currentStateLaneRowId(lane)}
                    >
                      {(laneRow) => (
                        <LaneView
                          state={props.state}
                          row={laneRow}
                          lane={lane}
                          itemId={currentStateItemRowId}
                        />
                      )}
                    </VisibleRow>
                  )}
                </For>
              </div>
            </RowShell>
          )}
        </VisibleRow>
      )}
    </Show>
  );
}

/** Keep the shipped four-claim state readable until a structured publish. */
function LegacyCurrentStateView(props: {
  state: LegacyCurrentState;
  now: number;
}): JSX.Element {
  return (
    <section class="current-state current-state-legacy">
      <header class="current-state-head">
        <h2>Current state</h2>
        <time
          dateTime={props.state.updated_at}
          title={formatTimestamp(props.state.updated_at)}
        >
          {humanAge(props.state.updated_at, props.now)}
        </time>
      </header>
      <dl class="current-state-claims">
        <StateClaim label="Goal" value={props.state.goal} />
        <StateClaim label="Working now" value={props.state.focus} />
        <Show when={props.state.blocker !== null}>
          <StateClaim label="Blocked" value={props.state.blocker ?? ""} />
        </Show>
        <StateClaim label="Next" value={props.state.next} />
      </dl>
    </section>
  );
}

function StateClaim(props: { label: string; value: string }): JSX.Element {
  return (
    <div class="current-state-claim">
      <dt>{props.label}</dt>
      <dd>{props.value}</dd>
    </div>
  );
}
