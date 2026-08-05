import { render } from "solid-js/web";
import "@fontsource-variable/atkinson-hyperlegible-next";
import { App } from "./app";
import { applyDesignVariant } from "./design-variant";
import { readEmbeddedDocument } from "./document";
import { createLiveDocument, type LiveDocument } from "./live-document";
import { watchPointer } from "./pointer";
import { startVersionWatch } from "./reload";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/current-state.css";
import "./styles/rows.css";
import "./styles/chrome.css";
import "./styles/marks.css";
import "./styles/prose.css";
import "./styles/variants/blue-margin-family.css";
import "./styles/variants/north-window.css";
import "./styles/variants/blue-margin.css";
import "./styles/variants/dusk-margin.css";
import "./styles/variants/solarized-paper.css";
import "./styles/variants/solarized-slate.css";
import "./styles/variants/catppuccin-latte.css";
import "./styles/variants/catppuccin-mocha.css";
import "./styles/variants/dusk-ledger.css";
import "./styles/variants/night-ledger.css";
export const ROOT_ID = "visual-brief-root";

applyDesignVariant(window.location.search, document.documentElement);

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
