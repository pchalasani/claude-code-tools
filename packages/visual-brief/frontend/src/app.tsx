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
import { Markdown } from "./markdown-view";
import { createBriefState, type BriefState } from "./state";
import { StructureMap } from "./structure-map";
import { UpdateView } from "./update-view";
export function App(props: { brief: BriefDocument }): JSX.Element {
  const state = createBriefState(() => props.brief);
  const [now, setNow] = createSignal(Date.now());
  const onKey = (event: KeyboardEvent): void => state.handleKey(event);
  let stopWatching: (() => void) | null = null;
  let ageTimer: number | null = null;
  createEffect(() => {
    document.title = props.brief.title;
  });
  onMount(() => {
    document.addEventListener("keydown", onKey);
    stopWatching = onPollCycle(() => state.pending.tick());
    ageTimer = window.setInterval(() => setNow(Date.now()), 30_000);
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
function Masthead(props: { state: BriefState }): JSX.Element {
  const shape = createMemo(() => describeShape(props.state.brief));
  const awaiting = () => props.state.nav.awaitingCount();
  const attention = () => props.state.nav.outstandingCount();
  return (
    <header class="masthead">
      <p class="eyebrow">Session briefing</p>
      <h1 class="brief-title">{props.state.brief.title}</h1>
      <div class="brief-summary">
        <Markdown text={props.state.brief.summary} />
      </div>
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
        <button
          type="button"
          class="meta-count meta-attention"
          data-attention-count={attention()}
          aria-label="Reveal chats, then restore the prior fold layout"
          aria-pressed={props.state.nav.chatRevealActive()}
          onClick={() => props.state.run("reveal-chats")}
        >
          <b>{attention()}</b> need attention
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
