import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
  type JSX,
} from "solid-js";
import { CurrentStateView } from "./current-state";
import {
  DEFAULT_DESIGN_VARIANT,
  DESIGN_THEMES,
  activeDesignVariant,
  designThemeForVariant,
  designVariantForTheme,
  designVariantMode,
  pairedDesignVariant,
  pairedVariantSwitchLabel,
  searchWithDesignVariant,
  setDesignVariant,
  type DesignVariant,
} from "./design-variant";
import {
  TRUST_LABELS,
  TRUST_ORDER,
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
  const [design, setDesign] = createSignal<DesignVariant>(
    activeDesignVariant(document.documentElement) ?? DEFAULT_DESIGN_VARIANT,
  );
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
  const applyDesign = (next: DesignVariant): void => {
    setDesignVariant(next, document.documentElement);
    const nextSearch = searchWithDesignVariant(window.location.search, next);
    const nextUrl = `${window.location.pathname}${nextSearch}${
      window.location.hash
    }`;
    window.history.replaceState(window.history.state, "", nextUrl);
    setDesign(next);
  };
  const toggleDesign = (): void => {
    const current = design();
    const next = pairedDesignVariant(current);
    if (next !== null) {
      applyDesign(next);
    }
  };
  const chooseTheme = (value: string): void => {
    const next = designVariantForTheme(value, designVariantMode(design()));
    if (next !== null) {
      applyDesign(next);
    }
  };
  return (
    <div class="shell" data-mounted="true">
      <StructureMap state={state} />
      <main class="stream" onClick={(event) => selectFromClick(state, event)}>
        <Masthead
          state={state}
          design={design()}
          onChooseTheme={chooseTheme}
          onToggleDesign={toggleDesign}
        />
        <Show when={props.brief.current_state}>
          {(current) => (
            <CurrentStateView
              state={state}
              current={current()}
              now={now()}
            />
          )}
        </Show>
        <div class="briefing-list">
          <For each={ordered()}>
            {(update, index) => (
              <section
                classList={{
                  "briefing-entry": true,
                  "latest-briefing": index() === 0,
                  "ledger-briefing": index() > 0,
                }}
              >
                <Show when={index() === 1}>
                  <header class="history-heading" id="briefing-ledger">
                    <p class="history-kicker">Earlier briefings</p>
                    <h2>Briefing ledger</h2>
                    <p>Previous briefings, newest first.</p>
                  </header>
                </Show>
                <VisibleRow state={state} id={update.id}>
                  {(row) => (
                    <UpdateView
                      state={state}
                      row={row}
                      update={update}
                      now={now()}
                      latest={index() === 0}
                    />
                  )}
                </VisibleRow>
              </section>
            )}
          </For>
        </div>
      </main>
      <SearchOverlay state={state} />
      <HelpOverlay state={state} />
    </div>
  );
}
function Masthead(props: {
  state: BriefState;
  design: DesignVariant;
  onChooseTheme: (value: string) => void;
  onToggleDesign: () => void;
}): JSX.Element {
  const attention = () => props.state.nav.latestBriefingAttentionCount();
  const paired = () => pairedDesignVariant(props.design);
  const switchLabel = () => {
    const current = props.design;
    const next = paired();
    if (next === null) {
      return "";
    }
    return pairedVariantSwitchLabel(current, next);
  };
  return (
    <header class="masthead">
      <div class="design-controls">
        <label class="design-picker">
          <span>Themes</span>
          <select
            aria-label="Theme"
            value={designThemeForVariant(props.design).id}
            onChange={(event) => {
              props.onChooseTheme(event.currentTarget.value);
              event.currentTarget.blur();
            }}
          >
            <optgroup label="Paired themes">
              <For each={DESIGN_THEMES.slice(0, 3)}>
                {(theme) => (
                  <option value={theme.id}>{theme.label}</option>
                )}
              </For>
            </optgroup>
            <optgroup label="Single-mode studies">
              <For each={DESIGN_THEMES.slice(3)}>
                {(theme) => (
                  <option value={theme.id}>{theme.label}</option>
                )}
              </For>
            </optgroup>
          </select>
        </label>
        <Show when={paired()}>
          {(next) => (
            <button
              type="button"
              class="design-pair-toggle"
              aria-label={switchLabel()}
              title={switchLabel()}
              onClick={props.onToggleDesign}
            >
              <span class="design-pair-toggle-icon" aria-hidden="true">
                {designVariantMode(next()) === "dark" ? "☾" : "☀"}
              </span>
            </button>
          )}
        </Show>
      </div>
      <div class="masthead-main">
        <div class="masthead-copy">
          <p class="eyebrow">Session briefing</p>
          <h1 class="brief-title">{props.state.brief.title}</h1>
          <div class="brief-summary">
            <Markdown text={props.state.brief.summary} />
          </div>
          <Show when={attention() > 0}>
            <div class="meta">
              <button
                type="button"
                class="meta-attention"
                data-attention-count={attention()}
                onClick={(event) => {
                  event.currentTarget.blur();
                  props.state.nav.toLatestBriefingAttention();
                }}
              >
                <b>{attention()}</b>{" "}
                {attention() === 1 ? "needs" : "need"}{" "}
                attention in latest briefing
              </button>
            </div>
          </Show>
        </div>
        <KeyBar state={props.state} />
      </div>
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
