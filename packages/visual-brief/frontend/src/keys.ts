export type Action =
  | "next-row"
  | "previous-row"
  | "next-lane"
  | "previous-lane"
  | "toggle"
  | "expand-all"
  | "collapse-all"
  | "compose"
  | "compose-global"
  | "next-awaiting"
  | "reveal-chats"
  | "hints"
  | "search"
  | "top"
  | "bottom"
  | "help"
  | "close"
  | "digit-1"
  | "digit-2"
  | "digit-3"
  | "digit-4"
  | "digit-5"
  | "digit-6"
  | "digit-7"
  | "digit-8"
  | "digit-9";
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
  a: "compose-global",
  n: "next-awaiting",
  m: "reveal-chats",
  f: "hints",
  "/": "search",
  g: "top",
  G: "bottom",
  "?": "help",
  Escape: "close",
  "1": "digit-1",
  "2": "digit-2",
  "3": "digit-3",
  "4": "digit-4",
  "5": "digit-5",
  "6": "digit-6",
  "7": "digit-7",
  "8": "digit-8",
  "9": "digit-9",
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
  ["f", "Jump to a row: show its number, then type that number"],
  [
    "c",
    "Chat wherever the cursor is — update, lane, item or conversation",
  ],
  ["a", "Message the agent without choosing a section"],
  [SEND_CHORD_LABEL, "Send what you have written"],
  ["1–9", "Jump to a numbered row or choose a numbered reply"],
  ["n", "Jump to your next open chat"],
  ["m", "Reveal your chats, then restore the prior fold layout"],
  ["/", "Search items"],
  ["g / G", "Top / bottom"],
  ["?", "Show this key list"],
  ["Escape", "Collapse the selected row, or move to its parent"],
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
  if (event.shiftKey === true && /^[1-9]$/.test(event.key)) {
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
      "a[href], button, input, select, textarea, summary",
    ) !== null
  ) {
    return null;
  }
  return action;
}
