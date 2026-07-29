export type Action =
  | "next-row"
  | "previous-row"
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
export interface KeyEventLike {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  target?: EventTarget | null;
}
export function isApplePlatform(platform: string = readPlatform()): boolean {
  return /mac|iphone|ipad|ipod/i.test(platform);
}
function readPlatform(): string {
  if (typeof navigator === "undefined") {
    return "";
  }
  return navigator.platform !== "" ? navigator.platform : navigator.userAgent;
}
export const SEND_CHORD_LABEL = isApplePlatform() ? "⌘ + Enter" : "Ctrl + Enter";
export function isSendChord(
  event: KeyEventLike,
  apple: boolean = isApplePlatform(),
): boolean {
  if (event.key !== "Enter") {
    return false;
  }
  return apple ? event.metaKey === true : event.ctrlKey === true;
}
export const BINDINGS: Record<string, Action> = {
  j: "next-row",
  k: "previous-row",
  J: "next-lane",
  K: "previous-lane",
  ArrowDown: "next-row",
  ArrowUp: "previous-row",
  " ": "toggle",
  Enter: "toggle",
  E: "expand-all",
  C: "collapse-all",
  c: "compose",
  a: "compose",
  n: "next-awaiting",
  m: "chats",
  f: "hints",
  "/": "search",
  g: "top",
  G: "bottom",
  "?": "help",
  Escape: "close",
};
export const SHIFTED_BINDINGS: Record<string, Action> = {
  ArrowDown: "next-lane",
  ArrowUp: "previous-lane",
};
export const KEY_HELP: [string, string][] = [
  ["j / k  or  ↓ / ↑", "Next / previous row"],
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
export function resolveAction(event: KeyEventLike): Action | null {
  if (event.ctrlKey === true || event.metaKey === true || event.altKey === true) {
    return null;
  }
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
      "a[href], button:not(.meta-awaiting):not(.meta-chats), "
        + "input, select, textarea, summary",
    ) !== null
  ) {
    return null;
  }
  return action;
}
