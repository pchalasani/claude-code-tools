import { For, Show, type JSX } from "solid-js";
import { KEY_HELP, type Action } from "./keys";
import type { BriefState } from "./state";

type KeyEntry = {
  keys: string;
  label: string;
  action?: Action;
};

const KEY_BAR: KeyEntry[] = [
  { keys: "↑↓ · j/k", label: "Rows" },
  { keys: "⇧↑↓ · J/K", label: "Lanes" },
  { keys: "Space / Enter", label: "Open / close", action: "toggle" },
  { keys: "Esc", label: "Up / close", action: "close" },
  { keys: "1–9", label: "Jump to item" },
  { keys: "f", label: "Jump to row", action: "hints" },
  { keys: "c", label: "Chat here", action: "compose" },
  { keys: "n", label: "Next open chat", action: "next-awaiting" },
  { keys: "m", label: "Chats / restore", action: "reveal-chats" },
  { keys: "/", label: "Search", action: "search" },
  { keys: "?", label: "All shortcuts", action: "help" },
];
export function KeyBar(props: { state: BriefState }): JSX.Element {
  return (
    <section class="keybar" aria-labelledby="shortcut-title">
      <h2 class="keybar-title" id="shortcut-title">Shortcuts</h2>
      <div class="keybar-grid">
        <For each={KEY_BAR}>
          {(entry) => (
            <Show
              when={entry.action}
              fallback={
                <span class="key-control">
                  <kbd>{entry.keys}</kbd>
                  <span>{entry.label}</span>
                </span>
              }
            >
              {(action) => (
                <button
                  type="button"
                  class="key-control"
                  data-action={action()}
                  aria-pressed={action() === "reveal-chats"
                    ? props.state.nav.chatRevealActive()
                    : undefined}
                  onClick={() => props.state.run(action())}
                >
                  <kbd>{entry.keys}</kbd>
                  <span>{entry.label}</span>
                </button>
              )}
            </Show>
          )}
        </For>
      </div>
    </section>
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
