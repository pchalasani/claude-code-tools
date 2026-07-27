import { render } from "solid-js/web";

import { App } from "./app";
import { readEmbeddedDocument } from "./document";
import { watchPointer } from "./pointer";
import { startVersionWatch } from "./reload";
import "./styles/base.css";
import "./styles/rows.css";
import "./styles/chrome.css";

export const ROOT_ID = "visual-brief-root";

/**
 * Mount the brief application into the page emitted by the Python renderer.
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
  try {
    const brief = readEmbeddedDocument(root);
    render(() => <App brief={brief} />, host);
  } catch (error) {
    host.textContent = `visual brief failed to start: ${String(error)}`;
    host.className = "brief-error";
    throw error;
  } finally {
    watchPointer();
startVersionWatch(root);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => mount(document), {
    once: true,
  });
} else {
  mount(document);
}
