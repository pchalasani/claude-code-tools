/**
 * What a served page says about itself.
 *
 * Three facts are baked into the markup by the Python renderer, and all three
 * are about the page rather than about the brief it carries: which generation
 * it was rendered from, which front-end bundle it is running, and how often it
 * would like to be checked. They are read here, from the page, so nothing else
 * has to know the shape of a meta element.
 */

export const VERSION_META = "visual-brief-render-version";
export const ASSETS_META = "visual-brief-assets-version";
export const POLL_META = "visual-brief-poll-ms";

/** How often a page checks itself when it does not say otherwise. */
export const POLL_INTERVAL_MS = 5000;

/** Narrowest and widest poll interval a page may ask for. */
const POLL_BOUNDS = [100, 600_000] as const;

/**
 * Read the generation embedded in a rendered page.
 *
 * @param root - Document to read.
 * @returns The generation, or an empty string when the page has none.
 */
export function pageVersion(root: Document): string {
  return metaContent(root, VERSION_META) ?? "";
}

/**
 * Read which front-end bundle a rendered page carries.
 *
 * A page from before bundle stamps existed carries none, and an empty string
 * matches nothing a daemon can answer — so such a page reloads rather than
 * patching a document into code it cannot vouch for.
 *
 * @param root - Document to read.
 * @returns The bundle's identity, or an empty string when the page has none.
 */
export function pageAssets(root: Document): string {
  return metaContent(root, ASSETS_META) ?? "";
}

/**
 * Read how often this page asks to be checked.
 *
 * @param root - Document to read.
 * @param fallback - Interval to use when the page does not say.
 * @returns The interval in milliseconds.
 */
export function pollInterval(
  root: Document,
  fallback: number = POLL_INTERVAL_MS,
): number {
  const asked = Number.parseInt(metaContent(root, POLL_META) ?? "", 10);
  if (!Number.isFinite(asked)) {
    return fallback;
  }
  return Math.min(Math.max(asked, POLL_BOUNDS[0]), POLL_BOUNDS[1]);
}

/**
 * Read one meta element's content.
 *
 * @param root - Document to read.
 * @param name - Name of the meta element.
 * @returns Its content, or null when the page carries none.
 */
function metaContent(root: Document, name: string): string | null {
  const meta = root.querySelector<HTMLMetaElement>(`meta[name="${name}"]`);
  return meta?.content ?? null;
}
