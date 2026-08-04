import {
  For,
  Show,
  createSignal,
  onCleanup,
  onMount,
  type JSX,
} from "solid-js";
import { formatTimestamp } from "./age";
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
const HOVER_INTENT_MS = 200;
export function StructureMap(props: { state: BriefState }): JSX.Element {
  const [open, setOpen] = createSignal(false);
  let trigger: HTMLButtonElement | undefined;
  let drawer: HTMLElement | undefined;
  let hoverTimer: number | undefined;
  let suppressFocusOpen = false;
  const cancelHover = (): void => {
    if (hoverTimer !== undefined) {
      window.clearTimeout(hoverTimer);
      hoverTimer = undefined;
    }
  };
  const openNow = (): void => {
    cancelHover();
    setOpen(true);
  };
  const close = (): void => {
    cancelHover();
    if (drawer?.contains(document.activeElement)) {
      suppressFocusOpen = true;
      trigger?.focus({ preventScroll: true });
      suppressFocusOpen = false;
    }
    setOpen(false);
  };
  const closeOnEscape = (event: KeyboardEvent): void => {
    if (
      event.key !== "Escape"
      || !open()
      || props.state.nav.overlay() === "help"
    ) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    close();
  };
  onMount(() => {
    document.addEventListener("keydown", closeOnEscape, true);
  });
  onCleanup(() => {
    cancelHover();
    document.removeEventListener("keydown", closeOnEscape, true);
  });
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
    <>
      <button
        ref={trigger}
        type="button"
        class="map-trigger"
        aria-controls="brief-structure-map"
        aria-expanded={open()}
        aria-label="Open updates log"
        onClick={openNow}
        onFocus={() => {
          if (!suppressFocusOpen) {
            openNow();
          }
        }}
        onPointerEnter={(event) => {
          if (event.pointerType === "mouse" && !open()) {
            cancelHover();
            hoverTimer = window.setTimeout(openNow, HOVER_INTENT_MS);
          }
        }}
        onPointerLeave={cancelHover}
      >
        <span class="map-trigger-mark" aria-hidden="true">›</span>
        <span class="map-trigger-label">Updates log</span>
      </button>
      <aside
        ref={drawer}
        id="brief-structure-map"
        class="map"
        aria-label="Updates log"
        aria-hidden={!open()}
        data-open={open() ? "true" : "false"}
        inert={!open()}
      >
        <header class="map-header">
          <p class="map-title">Updates log</p>
          <button
            type="button"
            class="map-close"
            aria-label="Close updates log"
            onClick={close}
          >
            <span aria-hidden="true">×</span>
          </button>
        </header>
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
                onClick={() => props.state.nav.select(
                  update.id,
                  { dropFilter: true },
                )}
              >
                <time dateTime={update.timestamp}>
                  {formatTimestamp(update.timestamp)}
                </time>
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
    </>
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
