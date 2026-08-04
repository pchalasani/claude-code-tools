import { For, Show, type JSX } from "solid-js";
import { KEY_HELP, type Action } from "./keys";
import type { BriefState } from "./state";
const KEY_BAR: [string, Action, string][] = [
  ["k", "previous-row", "Previous row"],
  ["j", "next-row", "Next row"],
  ["K", "previous-lane", "Previous lane"],
  ["J", "next-lane", "Next lane"],
  ["c", "compose", "Chat here"],
  ["f", "hints", "Jump to a row"],
  ["E", "expand-all", "Expand all"],
  ["C", "collapse-all", "Collapse all"],
  ["n", "next-awaiting", "Next open chat"],
  ["m", "reveal-chats", "Reveal / restore chats"],
  ["/", "search", "Search"],
  ["g", "top", "Top"],
  ["G", "bottom", "Bottom"],
  ["?", "help", "Keys"],
];
export function KeyBar(props: { state: BriefState }): JSX.Element {
  return (
    <nav class="keybar" aria-label="Keyboard and page controls">
      <For each={KEY_BAR}>
        {([key, action, label]) => (
          <button
            type="button"
            class="key-control"
            data-action={action}
            aria-pressed={action === "reveal-chats"
              ? props.state.nav.chatRevealActive()
              : undefined}
            onClick={() => props.state.run(action)}
          >
            <kbd>{key}</kbd>
            <span>{label}</span>
          </button>
        )}
      </For>
      <span
        class="key-control key-guide"
        aria-label="Number keys 1 through 9 choose the matching visible tag"
      >
        <kbd>1–9</kbd>
        <span>Numbered choice</span>
      </span>
    </nav>
  );
}
export function SearchOverlay(props: { state: BriefState }): JSX.Element {
  const nav = props.state.nav;
  return (
    <Show when={nav.overlay() === "search"}>
      <div class="search" role="search">
        <label for="brief-search">Search items</label>
        <input
          id="brief-search"
          type="search"
          autocomplete="off"
          value={nav.query()}
          onInput={(event) => nav.setQuery(event.currentTarget.value)}
        />
        <span class="match-count" role="status">
          {nav.matchCount()} {nav.matchCount() === 1 ? "match" : "matches"}
        </span>
        <button
          type="button"
          class="quiet"
          onClick={() => props.state.run("close")}
        >
          <kbd>Esc</kbd> Close
        </button>
      </div>
    </Show>
  );
}
export function HelpOverlay(props: { state: BriefState }): JSX.Element {
  return (
    <Show when={props.state.nav.overlay() === "help"}>
      <div
        class="help-scrim"
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            props.state.run("close");
          }
        }}
      >
        <div
          class="help"
          role="dialog"
          aria-modal="true"
          aria-labelledby="help-title"
        >
          <h2 id="help-title">Keyboard control</h2>
          <dl>
            <For each={KEY_HELP}>
              {([keys, meaning]) => (
                <div class="help-row">
                  <dt>
                    <kbd>{keys}</kbd>
                  </dt>
                  <dd>{meaning}</dd>
                </div>
              )}
            </For>
          </dl>
          <button
            type="button"
            class="quiet"
            onClick={() => props.state.run("close")}
          >
            <kbd>Esc</kbd> Close
          </button>
        </div>
      </div>
    </Show>
  );
}
