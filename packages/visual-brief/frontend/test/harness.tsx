/**
 * The jsdom harness the rendering suites share.
 *
 * Every assertion in those suites is about paint, so the helpers here read
 * the page the way a human does — the marked row, the expanded rows — and
 * never the application's internals. The view-transition shims exist because
 * jsdom has none at all, which hides the one-frame ordering the real browser
 * imposes.
 */

import { render } from "solid-js/web";
import { afterEach, beforeEach, expect } from "vitest";

import { App } from "../src/app";
import type { BriefDocument } from "../src/document";
import { createLiveDocument } from "../src/live-document";
import { sampleBrief } from "./sample-brief";

let dispose: (() => void) | null = null;
let host: HTMLElement | null = null;
let pendingTransitions: (() => void)[] = [];

/**
 * Mount the interface the way the page does.
 *
 * @param brief - The document to open on.
 * @returns The element the application was mounted into.
 */
export function mount(brief = sampleBrief()): HTMLElement {
  return mountLive(brief).container;
}

/**
 * Mount the interface over a document a later publish can replace.
 *
 * This is the whole of what the poller does to a live page, minus the poll:
 * it hands a newly delivered document to the application and nothing else
 * happens. Everything a publish must leave alone is asserted by publishing
 * through this and then reading the page.
 *
 * @param brief - The document to open on.
 * @returns The mount point and the way to publish into it.
 */
export function mountLive(brief = sampleBrief()): {
  container: HTMLElement;
  publish: (next: BriefDocument) => void;
} {
  const live = createLiveDocument(brief);
  const container = document.createElement("div");
  document.body.append(container);
  host = container;
  dispose = render(() => <App brief={live.brief} />, container);
  return { container, publish: live.apply };
}

/** Tear the mounted interface back down, the way a reload does. */
export function unmount(): void {
  dispose?.();
  host?.remove();
  dispose = null;
  host = null;
}

/**
 * Press one key the way a keyboard does.
 *
 * @param key - The key value.
 */
export function press(key: string): void {
  document.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
}

/**
 * Press one key the way a keyboard does while a control holds focus.
 *
 * The browser delivers a key press to the focused element, and clicking a
 * button focuses it, so this is what every key looks like once a hand has
 * touched the page with the mouse.
 *
 * @param target - Element the browser would deliver the key to.
 * @param key - The key value.
 */
export function pressAt(target: Element, key: string): void {
  target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
}

/**
 * Read one row's element.
 *
 * @param id - Row id to look for.
 * @returns The element, or null when the page is not painting it.
 */
export function rowNode(id: string): Element | null {
  return document.querySelector(`[data-row-id="${id}"]`);
}

/**
 * Open the chat box at one row through the affordance a hand would use.
 *
 * @param id - Row to write against.
 */
export function composeAt(id: string): void {
  document
    .querySelector(`[data-row-id="${id}"] > .row-head .chat-button`)
    ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

/**
 * Write into whatever text box is open, the way a keyboard does.
 *
 * @param selector - The box to write into.
 * @param text - What to write.
 */
export function typeInto(selector: string, text: string): void {
  const box = document.querySelector<HTMLTextAreaElement | HTMLInputElement>(
    selector,
  );
  if (box === null) {
    throw new Error(`nothing to write into at ${selector}`);
  }
  box.value = text;
  box.dispatchEvent(new Event("input", { bubbles: true }));
}

/**
 * Read the row the interface says the cursor is on.
 *
 * @returns The row id, or null when nothing is marked.
 */
export function paintedCursor(): string | null {
  const marked = document.querySelectorAll('[data-cursor="true"]');
  expect(marked).toHaveLength(1);
  return marked[0]?.getAttribute("data-row-id") ?? null;
}

/**
 * Read whether the page paints one row as expanded.
 *
 * @param id - Row id to look at.
 * @returns The row's painted open state.
 */
export function paintedOpen(id: string): string | null {
  return (
    document
      .querySelector(`[data-row-id="${id}"]`)
      ?.getAttribute("data-open") ?? null
  );
}

/**
 * Make view transitions behave the way a real browser makes them behave.
 *
 * Chrome does not run the update callback until the next rendering update, so
 * anything a keypress needs in the DOM before the next microtask cannot come
 * from inside a transition. jsdom has no view transitions at all, which hides
 * exactly the ordering bug this emulates.
 */
export function deferTransitions(): void {
  document.startViewTransition = ((update: () => void) => {
    pendingTransitions.push(update);
    return {} as ViewTransition;
  }) as typeof document.startViewTransition;
}

/** Apply every change a deferred view transition is still holding. */
export function flushTransitions(): void {
  const held = pendingTransitions;
  pendingTransitions = [];
  for (const update of held) {
    update();
  }
}

/**
 * Click one row's head the way a hand on a mouse does.
 *
 * @param id - Row id to click.
 */
export function click(id: string): void {
  document
    .querySelector(`[data-row-id="${id}"] .row-toggle`)
    ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

/** Give every test in the importing suite a clean page. */
export function useHarness(): void {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.history.replaceState(null, "");
    pendingTransitions = [];
  });

  afterEach(() => {
    unmount();
    Reflect.deleteProperty(document, "startViewTransition");
    pendingTransitions = [];
  });
}
