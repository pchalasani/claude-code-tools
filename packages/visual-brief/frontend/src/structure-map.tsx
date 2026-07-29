import { For, type JSX } from "solid-js";
import { ancestorIds, laneRowId, orderedUpdates } from "./outline";
import { explicitSelectionTookOver } from "./pointer";
import type { BriefState } from "./state";
const MAX_TICK = 56;
export function StructureMap(props: { state: BriefState }): JSX.Element {
  const currentLane = (): string | null => {
    const id = props.state.nav.cursorId();
    if (id === null) {
      return null;
    }
    for (const candidate of [id, ...ancestorIds(id)]) {
      if (props.state.nav.row(candidate)?.kind === "lane") {
        return candidate;
      }
    }
    return null;
  };
  return (
    <aside class="map" aria-label="Document map">
      <p class="map-title">Structure</p>
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
            <ol class="map-lanes">
              <For each={update.lanes ?? []}>
                {(lane) => {
                  const id = laneRowId(update.id, lane);
                  const row = () => props.state.nav.row(id);
                  const width = (): number =>
                    Math.min(MAX_TICK, 10 + (lane.items ?? []).length * 8);
                  return (
                    <li>
                      <button
                        type="button"
                        class="map-lane"
                        data-map-lane={id}
                        data-current={currentLane() === id ? "true" : "false"}
                        data-awaiting={
                          row()?.awaiting === true ? "true" : "false"
                        }
                        onClick={() => {
                          explicitSelectionTookOver();
                          props.state.nav.setOpen(update.id, true);
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
          </section>
        )}
      </For>
    </aside>
  );
}
