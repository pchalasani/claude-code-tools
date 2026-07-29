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
  | "expand-all"
  | "collapse-all"
  | "compose"
  | "next-awaiting"
  | "chats"
  | "hints"
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
  /** Whether shift was held, which turns an arrow into a lane move. */
  shiftKey?: boolean;
  /** Element the key was delivered to. */
  target?: EventTarget | null;
}

/**
 * Report whether this is an Apple keyboard, where the send chord is Command.
 *
 * @param platform - What the browser calls the platform.
 * @returns True on macOS and on the iPad's desktop-class browser.
 */
export function isApplePlatform(platform: string = readPlatform()): boolean {
  return /mac|iphone|ipad|ipod/i.test(platform);
}

/**
 * Ask the browser what it is running on.
 *
 * @returns The platform string, or an empty string outside a browser.
 */
function readPlatform(): string {
  if (typeof navigator === "undefined") {
    return "";
  }
  return navigator.platform !== "" ? navigator.platform : navigator.userAgent;
}

/** How the send chord is written for the human. */
export const SEND_CHORD_LABEL = isApplePlatform() ? "⌘ + Enter" : "Ctrl + Enter";

/**
 * Report whether a key press is the chord that sends what is written.
 *
 * Plain Enter has to keep making a paragraph — a question worth asking is
 * often longer than one line — so sending needs a chord. It is Command on a
 * Mac and Control everywhere else, which is what the rest of both platforms
 * already means by "send this now".
 *
 * @param event - The key press.
 * @param apple - Whether this is an Apple keyboard.
 * @returns True when the press should send.
 */
export function isSendChord(
  event: KeyEventLike,
  apple: boolean = isApplePlatform(),
): boolean {
  if (event.key !== "Enter") {
    return false;
  }
  return apple ? event.metaKey === true : event.ctrlKey === true;
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
  // Space folds one row; the shifted letters fold the page. E and C are free,
  // sit under the same fingers as the keys they extend, and read as what they
  // do the moment the key bar names them.
  E: "expand-all",
  C: "collapse-all",
  c: "compose",
  // The key this used to be. Undocumented, kept because fingers remember.
  a: "compose",
  n: "next-awaiting",
  // The human's own conversations, which nothing else on the page collects:
  // after a collapse-all they are invisible, and n only visits the ones still
  // waiting for an answer.
  m: "chats",
  // Vimium's key for "label everything I can go to", which is exactly this.
  f: "hints",
  "/": "search",
  g: "top",
  G: "bottom",
  "?": "help",
  Escape: "close",
};

/**
 * Bindings that only apply while Shift is held.
 *
 * Only keys whose ``key`` value does not already change under Shift belong
 * here: a letter arrives as "J" rather than "j" and is bound directly.
 */
export const SHIFTED_BINDINGS: Record<string, Action> = {
  ArrowDown: "next-lane",
  ArrowUp: "previous-lane",
};

/** The bindings as the help overlay lists them. */
export const KEY_HELP: [string, string][] = [
  ["j / k  or  ↓ / ↑", "Next / previous content row"],
  ["J / K  or  ⇧↓ / ⇧↑", "Next / previous lane"],
  ["Space / Enter", "Open or close the selected row"],
  ["E / C", "Expand everything / collapse back to lanes"],
  ["f", "Label every row, then type a label to jump there"],
  [
    "c",
    "Chat wherever the cursor is — update, lane, item or conversation",
  ],
  [SEND_CHORD_LABEL, "Send what you have written"],
  ["n", "Jump to your next open chat"],
  ["m", "Show every conversation you have written in"],
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
 * touched instead of the row the cursor is on.
 *
 * Enter belongs to the cursor unless it was delivered to an ordinary button.
 * A keyboard reader who tabs to a fold or a composer control still activates
 * that exact control natively. The masthead counters are the exception:
 * stale focus there cannot press an invisible button after the cursor moves.
 * Text fields keep Enter too, for paragraphs.
 *
 * @param event - The key press.
 * @returns The action to run, or null when the page should not react.
 */
export function resolveAction(event: KeyEventLike): Action | null {
  if (event.ctrlKey === true || event.metaKey === true || event.altKey === true) {
    return null;
  }
  // Shift with an arrow moves by lane, mirroring how Shift turns j/k into
  // J/K. The letters already carry their shift in the key value; the arrows
  // do not, so the modifier is read here.
  const action = event.shiftKey === true
    ? SHIFTED_BINDINGS[event.key] ?? BINDINGS[event.key]
    : BINDINGS[event.key];
  if (action === undefined) {
    return null;
  }
  if (isTypingTarget(event.target ?? null)) {
    return action === "close" ? "close" : null;
  }
  if (
    event.key === "Enter"
    && event.target instanceof Element
    && event.target.closest(
      "button:not(.meta-awaiting):not(.meta-chats)",
    ) !== null
  ) {
    return null;
  }
  return action;
}
