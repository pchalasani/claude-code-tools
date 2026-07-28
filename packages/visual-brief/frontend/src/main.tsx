import { render } from "solid-js/web";

import { App } from "./app";
import { readEmbeddedDocument } from "./document";
import { createLiveDocument, type LiveDocument } from "./live-document";
import { watchPointer } from "./pointer";
import { startVersionWatch } from "./reload";
import "./styles/base.css";
import "./styles/rows.css";
import "./styles/chrome.css";
import "./styles/marks.css";
import "./styles/prose.css";

export const ROOT_ID = "visual-brief-root";

/**
 * Mount the brief application into the page emitted by the Python renderer.
 *
 * The embedded document is read once, as the page's starting point, and then
 * held in state that a later publish can replace. That is the whole of the
 * live-patching arrangement from this end: the watch fetches new documents and
 * hands them here, and the application decides what actually has to change.
 *
 * A failure is written into the page rather than only into the console: this
 * page is the human's only surface, so a dead bundle must be visible.
 *
 * @param root - Document to mount into.
 */
export function mount(root: Document): void {
  const host = root.getElementById(ROOT_ID);
  if (host === null) {
    throw new Error(`no mount point with id ${ROOT_ID}`);
  }
  let live: LiveDocument | null = null;
  try {
    const held = createLiveDocument(readEmbeddedDocument(root));
    live = held;
    render(() => <App brief={held.brief} />, host);
  } catch (error) {
    host.textContent = `visual brief failed to start: ${String(error)}`;
    host.className = "brief-error";
    throw error;
  } finally {
    // Started even when the document above failed to parse. Such a page has
    // nothing to patch into and says so by having no way to apply one, which
    // leaves it exactly one way of catching up: replacing itself with a page
    // that works.
    watchPointer();
    const patch = live;
    startVersionWatch(
      root,
      patch === null ? undefined : (document) => patch.apply(document),
    );
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => mount(document), {
    once: true,
  });
} else {
  mount(document);
}
