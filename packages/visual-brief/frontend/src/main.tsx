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
