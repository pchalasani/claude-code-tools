import {
  For,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
  type JSX,
} from "solid-js";

import {
  TRUST_LABELS,
  TRUST_ORDER,
  describeShape,
  type BriefDocument,
} from "./document";
import { TrustChip } from "./item-view";
import { orderedUpdates } from "./outline";
import { HelpOverlay, KeyBar, SearchOverlay } from "./overlays";
import { onPollCycle } from "./reload";
import { VisibleRow } from "./row-shell";
import { createBriefState, type BriefState } from "./state";
import { StructureMap } from "./structure-map";
import { UpdateView } from "./update-view";

/**
 * The briefing surface.
 *
 * @param props - The delivered document, which a publish may replace under
 *     the running page.
 * @returns The rendered page.
 */
export function App(props: { brief: BriefDocument }): JSX.Element {
  const state = createBriefState(() => props.brief);
  const [now, setNow] = createSignal(Date.now());
  const onKey = (event: KeyboardEvent): void => state.handleKey(event);
  let stopWatching: (() => void) | null = null;
  let ageTimer: number | null = null;
  // The tab's own name for this run follows the document rather than the page
  // it was served in: a patched page whose title still said what it said an
  // hour ago would be lying in the one place the human cannot expand.
  createEffect(() => {
    document.title = props.brief.title;
  });
  onMount(() => {
    document.addEventListener("keydown", onKey);
    // A message that never appears has to stop claiming progress eventually,
    // and "eventually" is counted in polls rather than in seconds: the page
    // is only ever wrong about this when the daemon has gone quiet.
    stopWatching = onPollCycle(() => state.pending.tick());
    ageTimer = window.setInterval(() => setNow(Date.now()), 30_000);
    // A load that followed the human's own message opens on that message.
    state.nav.revealAnchor();
  });
  onCleanup(() => {
    document.removeEventListener("keydown", onKey);
    stopWatching?.();
    if (ageTimer !== null) {
      window.clearInterval(ageTimer);
    }
  });
  const ordered = createMemo(() => orderedUpdates(props.brief));
  return (
    <div class="shell" data-mounted="true">
      <StructureMap state={state} />
      <main class="stream" onClick={(event) => selectFromClick(state, event)}>
        <Masthead state={state} />
        <For each={ordered()}>
          {(update) => (
            <VisibleRow state={state} id={update.id}>
              {(row) => (
                <UpdateView
                  state={state}
                  row={row}
                  update={update}
                  now={now()}
                />
              )}
            </VisibleRow>
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
  const chats = () => props.state.nav.chatCount();
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
        {/*
          The human's own conversations, answered or not. Nothing else on the
          page collects them: folded away they are invisible, and the awaiting
          count deliberately skips the ones already answered.
        */}
        <button
          type="button"
          class="meta-count meta-chats"
          data-chats-count={chats()}
          aria-pressed={props.state.nav.chats()}
          onClick={() => props.state.run("chats")}
        >
          <b>{chats()}</b> my chats
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
