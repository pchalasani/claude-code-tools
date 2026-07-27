/**
 * Keep an open page in step with the run it is showing.
 *
 * The agent rewrites the page whenever it publishes an update, so a page left
 * open must notice. It compares the generation baked into its own markup with
 * the one the local daemon reports and reloads when they diverge. Everything
 * the watch needs is injected so it can be driven directly in tests.
 */

export const VERSION_META = "visual-brief-render-version";
export const VERSION_PATH = "render-version";
export const POLL_INTERVAL_MS = 5000;

export interface VersionWatch {
  /** Generation the loaded page was rendered from. */
  current: string;
  /** Read the generation the server would serve right now. */
  read: () => Promise<string>;
  /** Replace the loaded page with the current one. */
  reload: () => void;
}

/**
 * Read the generation embedded in a rendered page.
 *
 * @param root - Document to read.
 * @returns The generation, or an empty string when the page has none.
 */
export function pageVersion(root: Document): string {
  const meta = root.querySelector<HTMLMetaElement>(
    `meta[name="${VERSION_META}"]`,
  );
  return meta?.content ?? "";
}

/**
 * Reload once when the served generation differs from the loaded one.
 *
 * Failures are swallowed: a saved page opened without its daemon has no
 * server to ask, and must stay readable rather than throwing on a timer.
 *
 * @param watch - Injected generation source and reload action.
 * @returns Whether a reload was triggered.
 */
export async function checkVersion(watch: VersionWatch): Promise<boolean> {
  let served: string;
  try {
    served = await watch.read();
  } catch {
    return false;
  }
  if (served === "" || served === watch.current) {
    return false;
  }
  watch.reload();
  return true;
}

/**
 * Ask the local daemon which generation it would serve right now.
 *
 * An answer that is not a success — an unknown run, a page whose directory
 * has gone away — counts as no answer at all rather than as a new
 * generation, which would otherwise reload the page every few seconds
 * forever.
 *
 * @returns The served generation, or an empty string when there is none.
 */
export async function readServedVersion(): Promise<string> {
  const response = await fetch(VERSION_PATH, { cache: "no-store" });
  if (!response.ok) {
    return "";
  }
  return await response.text();
}

/**
 * Start polling the local daemon for a newer generation of this page.
 *
 * @param root - Document holding the generation meta element.
 * @param intervalMs - Milliseconds between polls.
 * @returns A function that stops the watch.
 */
export function startVersionWatch(
  root: Document,
  intervalMs: number = POLL_INTERVAL_MS,
): () => void {
  const watch: VersionWatch = {
    current: pageVersion(root),
    read: readServedVersion,
    reload: () => {
      window.location.reload();
    },
  };
  void checkVersion(watch);
  const timer = window.setInterval(() => void checkVersion(watch), intervalMs);
  return () => window.clearInterval(timer);
}
