/**
 * The keyboard, resolved before anything is done about it.
 *
 * Binding lookup is a pure function of the event, which is the only way to
 * test that the shifted keys are alive: ``J``, ``K``, ``G`` and ``?`` arrive
 * as their own ``key`` values, and a table written against lowercase letters
 * silently drops all four.
 */

/** Everything the keyboard can ask the interface to do. */
export type Action =
  | "next-item"
  | "previous-item"
  | "next-lane"
  | "previous-lane"
  | "toggle"
  | "compose"
  | "next-awaiting"
  | "search"
  | "top"
  | "bottom"
  | "help"
  | "close";

/** The part of a keyboard event a binding is resolved from. */
export interface KeyEventLike {
  /** Value of the pressed key. */
  key: string;
  /** Whether a control-like modifier was held. */
  ctrlKey?: boolean;
  /** Whether the command key was held. */
  metaKey?: boolean;
  /** Whether the alt key was held. */
  altKey?: boolean;
  /** Element the key was delivered to. */
  target?: EventTarget | null;
}

/** Every binding, keyed by the ``key`` value the browser reports. */
export const BINDINGS: Record<string, Action> = {
  j: "next-item",
  k: "previous-item",
  J: "next-lane",
  K: "previous-lane",
  // Arrow keys do the same as j/k. Vim-style letter keys are the fast path,
  // but a browser extension such as Vimium binds j/k globally and wins, so
  // there has to be a route that no extension is likely to have claimed.
  ArrowDown: "next-item",
  ArrowUp: "previous-item",
  " ": "toggle",
  Enter: "toggle",
  a: "compose",
  n: "next-awaiting",
  "/": "search",
  g: "top",
  G: "bottom",
  "?": "help",
  Escape: "close",
};

/** The bindings as the help overlay lists them. */
export const KEY_HELP: [string, string][] = [
  ["j / k  or  ↓ / ↑", "Next / previous item"],
  ["J / K", "Next / previous lane"],
  ["Enter / Space", "Open or close the selected row"],
  ["a", "Ask about the selected row"],
  ["n", "Jump to your next unanswered question"],
  ["/", "Search items"],
  ["g / G", "Top / bottom"],
  ["?", "Show this key list"],
  ["Escape", "Close, or leave a text box"],
];

/**
 * Report whether a key press belongs to a text box rather than the page.
 *
 * @param target - Element the key was delivered to.
 * @returns True while the human is typing.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) {
    return false;
  }
  if (target.matches("textarea, input, select")) {
    return true;
  }
  return target.closest('[contenteditable]:not([contenteditable="false"])')
    !== null;
}

/**
 * Resolve one key press to the action it should run.
 *
 * Bindings are inert while the human is typing, so that a question containing
 * the letter ``j`` does not move the cursor out from under it. Escape is the
 * one key that survives, because leaving the text box is how typing ends.
 *
 * Outside a text box the page owns every bound key, Space included. Standing
 * aside for a focused control looked polite and was not: the browser focuses a
 * button when it is clicked, the cursor is deliberately not the browser's
 * focus, so after one mouse click Space folded whichever row the mouse last
 * touched instead of the row the cursor is on. Every control on this page is a
 * ``<button>``, which Enter activates natively, so a keyboard reader who tabs
 * to an evidence fold loses nothing.
 *
 * @param event - The key press.
 * @returns The action to run, or null when the page should not react.
 */
export function resolveAction(event: KeyEventLike): Action | null {
  if (event.ctrlKey === true || event.metaKey === true || event.altKey === true) {
    return null;
  }
  const action = BINDINGS[event.key];
  if (action === undefined) {
    return null;
  }
  if (isTypingTarget(event.target ?? null)) {
    return action === "close" ? "close" : null;
  }
  return action;
}
