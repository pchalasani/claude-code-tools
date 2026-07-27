import { For, Show, createMemo, onCleanup, onMount, type JSX } from "solid-js";

import {
  TRUST_LABELS,
  TRUST_ORDER,
  describeShape,
  type BriefDocument,
} from "./document";
import { TrustChip } from "./item-view";
import { NOW_UPDATE_ID, orderedUpdates } from "./outline";
import { HelpOverlay, KeyBar, SearchOverlay } from "./overlays";
import { VisibleRow } from "./row-shell";
import { createBriefState, type BriefState } from "./state";
import { StructureMap } from "./structure-map";
import { UpdateView } from "./update-view";

/**
 * The briefing surface.
 *
 * @param props - The delivered document.
 * @returns The rendered page.
 */
export function App(props: { brief: BriefDocument }): JSX.Element {
  const state = createBriefState(props.brief);
  const onKey = (event: KeyboardEvent): void => state.handleKey(event);
  onMount(() => document.addEventListener("keydown", onKey));
  onCleanup(() => document.removeEventListener("keydown", onKey));
  const ordered = createMemo(() => orderedUpdates(props.brief));
  // The divider only earns its place when a Now panel leads AND at least one
  // dated update survives the search filter; otherwise it would label
  // nothing.
  const earlierVisible = (): boolean =>
    ordered()[0]?.id === NOW_UPDATE_ID &&
    ordered()
      .slice(1)
      .some((update) => state.nav.isVisible(update.id));
  return (
    <div class="shell" data-mounted="true">
      <StructureMap state={state} />
      <main class="stream" onClick={(event) => selectFromClick(state, event)}>
        <Masthead state={state} />
        <For each={ordered()}>
          {(update, index) => (
            <>
              <Show when={index() === 1 && earlierVisible()}>
                <h2 class="earlier-heading">Earlier updates</h2>
              </Show>
              <VisibleRow state={state} id={update.id}>
                {(row) => (
                  <UpdateView state={state} row={row} update={update} />
                )}
              </VisibleRow>
            </>
          )}
        </For>
      </main>
      <SearchOverlay state={state} />
      <HelpOverlay state={state} />
    </div>
  );
}

/**
 * The page's own heading: what this is, how big it is, what is outstanding.
 *
 * @param props - The page state.
 * @returns The rendered masthead.
 */
function Masthead(props: { state: BriefState }): JSX.Element {
  const shape = createMemo(() => describeShape(props.state.brief));
  const awaiting = () => props.state.nav.awaitingCount();
  return (
    <header class="masthead">
      <p class="eyebrow">Session briefing</p>
      <h1 class="brief-title">{props.state.brief.title}</h1>
      <p class="brief-summary">{props.state.brief.summary}</p>
      <div class="meta">
        <span class="meta-count" data-count="updates">
          <b>{shape().updates}</b> updates
        </span>
        <span class="meta-count" data-count="lanes">
          <b>{shape().lanes}</b> lanes
        </span>
        <span class="meta-count" data-count="items">
          <b>{shape().items}</b> items
        </span>
        <button
          type="button"
          class="meta-count meta-awaiting"
          data-awaiting-count={awaiting()}
          onClick={() => props.state.run("next-awaiting")}
        >
          <b>{awaiting()}</b> unanswered
        </button>
      </div>
      <KeyBar state={props.state} />
      <div class="legend">
        <span class="legend-label">Trust</span>
        <For each={TRUST_ORDER}>
          {(trust) => (
            <span class="legend-chip" title={TRUST_LABELS[trust]}>
              <TrustChip trust={trust} />
            </span>
          )}
        </For>
      </div>
    </header>
  );
}

/**
 * Let a click anywhere in a row move the cursor to that row.
 *
 * The mouse and the keyboard drive one selection: whatever the human clicks,
 * the nearest row containing it becomes the cursor.
 *
 * @param state - The page state.
 * @param event - The click.
 */
function selectFromClick(state: BriefState, event: MouseEvent): void {
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }
  const row = target.closest("[data-row-id]");
  if (row instanceof HTMLElement && row.dataset.rowId !== undefined) {
    state.nav.select(row.dataset.rowId, { scroll: false });
  }
}
