import { For, type JSX } from "solid-js";

import { ancestorIds, laneRowId, orderedUpdates } from "./outline";
import type { BriefState } from "./state";

/** How wide a lane's tick can grow, in pixels. */
const MAX_TICK = 56;

/**
 * The document's own shape, standing in for a scrollbar.
 *
 * Each lane is one tick, sized by how much it holds; the tick holding the
 * cursor is filled. It answers "where am I" without reading a word, and
 * clicking a tick moves the same cursor the keyboard moves. The map shows
 * the whole document, search or no search, so a tick a search is hiding is
 * still clickable: ``select`` drops the search rather than sending the cursor
 * somewhere the page is not rendering.
 *
 * @param props - The page state.
 * @returns The rendered map.
 */
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
              onClick={() => props.state.nav.select(update.id)}
            >
              {update.timestamp}
            </button>
            <ol class="map-lanes">
              <For each={update.lanes ?? []}>
                {(lane) => {
                  const id = laneRowId(update.id, lane);
                  const row = () => props.state.nav.row(id);
                  const width = Math.min(
                    MAX_TICK,
                    10 + (lane.items ?? []).length * 8,
                  );
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
                        onClick={() => props.state.nav.select(id)}
                      >
                        <span
                          class="map-tick"
                          style={{ width: `${width}px` }}
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
