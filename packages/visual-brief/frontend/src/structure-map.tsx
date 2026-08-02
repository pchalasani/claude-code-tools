import { For, Show, type JSX } from "solid-js";
import { isStructuredCurrentState, type Lane } from "./document";
import {
  CURRENT_STATE_ROOT_ID,
  currentStateLaneRowId,
  laneRowId,
  orderedUpdates,
} from "./outline";
import { explicitSelectionTookOver } from "./pointer";
import type { BriefState } from "./state";
const MAX_TICK = 56;
export function StructureMap(props: { state: BriefState }): JSX.Element {
  const currentLane = (): string | null => {
    const id = props.state.nav.cursorId();
    if (id === null) {
      return null;
    }
    for (const candidate of [id, ...props.state.nav.ancestors(id)]) {
      if (props.state.nav.row(candidate)?.kind === "lane") {
        return candidate;
      }
    }
    return null;
  };
  return (
    <aside class="map" aria-label="Document map">
      <p class="map-title">Structure</p>
      <Show
        when={
          props.state.brief.current_state !== undefined
          && isStructuredCurrentState(props.state.brief.current_state)
            ? props.state.brief.current_state
            : undefined
        }
      >
        {(current) => (
          <section class="map-update map-current-state">
            <button
              type="button"
              class="map-update-head"
              onClick={() => props.state.nav.select(
                CURRENT_STATE_ROOT_ID,
                { dropFilter: true },
              )}
            >
              Current state
            </button>
            <MapLanes
              state={props.state}
              lanes={current().lanes}
              parentId={CURRENT_STATE_ROOT_ID}
              laneId={currentStateLaneRowId}
              currentLane={currentLane()}
            />
          </section>
        )}
      </Show>
      <For each={orderedUpdates(props.state.brief)}>
        {(update) => (
          <section class="map-update">
            <button
              type="button"
              class="map-update-head"
              onClick={() => props.state.nav.select(update.id, { dropFilter: true })}
            >
              {update.timestamp}
            </button>
            <MapLanes
              state={props.state}
              lanes={update.lanes ?? []}
              parentId={update.id}
              laneId={(lane) => laneRowId(update.id, lane)}
              currentLane={currentLane()}
            />
          </section>
        )}
      </For>
    </aside>
  );
}

function MapLanes(props: {
  state: BriefState;
  lanes: Lane[];
  parentId: string;
  laneId: (lane: Lane) => string;
  currentLane: string | null;
}): JSX.Element {
  return (
    <ol class="map-lanes">
      <For each={props.lanes}>
        {(lane) => {
          const id = props.laneId(lane);
          const row = () => props.state.nav.row(id);
          const width = (): number =>
            Math.min(MAX_TICK, 10 + (lane.items ?? []).length * 8);
          return (
            <li>
              <button
                type="button"
                class="map-lane"
                data-map-lane={id}
                data-current={props.currentLane === id ? "true" : "false"}
                data-awaiting={row()?.awaiting === true ? "true" : "false"}
                onClick={() => {
                  explicitSelectionTookOver();
                  props.state.nav.setOpen(props.parentId, true);
                  props.state.nav.select(id, { dropFilter: true });
                }}
              >
                <span
                  class="map-tick"
                  style={{ width: `${width()}px` }}
                  aria-hidden="true"
                />
                <span class="map-label">{lane.name}</span>
              </button>
            </li>
          );
        }}
      </For>
    </ol>
  );
}
