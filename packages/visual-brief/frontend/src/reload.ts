/**
 * Keep an open page in step with the run it is showing.
 *
 * The agent rewrites the page whenever it publishes, so a page left open must
 * notice. It compares the generation baked into its own markup with the one
 * the local daemon reports and reloads when they diverge.
 *
 * The hard part is not the comparison, it is everything that can go wrong
 * around it. A tab left open across an upgrade of the daemon met a
 * ``render-version`` answer it could not read, swallowed it, and sat there
 * showing a working sign forever until it was reloaded by hand. So the rules
 * here are deliberately blunt:
 *
 * - An answer the client cannot interpret is not an error to swallow, it is
 *   an instruction to reload. The two ends no longer speak the same language,
 *   and the page is the end that can be replaced.
 * - Not reaching the daemon at all is different, and only backs off: a saved
 *   page opened without its daemon has to stay readable rather than reload
 *   itself in a loop.
 * - Nothing thrown anywhere in a poll may end the polling. Every cycle
 *   schedules the next one from a ``finally``.
 * - Healing is remembered, so a page that comes back exactly as
 *   unintelligible as it left does not reload forever.
 */

import {
  readHealedGeneration,
  rememberHealedGeneration,
} from "./session-store";

export const VERSION_META = "visual-brief-render-version";
export const POLL_META = "visual-brief-poll-ms";
export const VERSION_PATH = "render-version";
export const POLL_INTERVAL_MS = 5000;

/** Slowest the watch backs off to while the daemon is unreachable. */
export const MAX_POLL_INTERVAL_MS = 60_000;

/** Narrowest and widest poll interval a page may ask for. */
const POLL_BOUNDS = [100, 600_000] as const;

/** The shape every generation this client understands has. */
const GENERATION = /^[0-9a-f]{64}$/;

/** What one poll cycle concluded. */
export type PollOutcome = "same" | "reload" | "retry";

/** Everything one poll needs, injected so it can be driven in tests. */
export interface VersionWatch {
  /** Generation the loaded page was rendered from. */
  current: string;
  /**
   * Read what the server would serve right now.
   *
   * A null answer means the question did not get through — no daemon, an
   * error status, a run that has gone away. Any other answer is the server
   * speaking, and is judged on whether it can be understood.
   */
  read: () => Promise<string | null>;
  /** Replace the loaded page with the current one. */
  reload: () => void;
  /** Whether this page already reloaded itself out of this exact state. */
  healed: () => boolean;
  /** Remember that this page reloaded itself out of this state. */
  remember: () => void;
}

/**
 * Report whether a value is a generation this client can compare.
 *
 * @param value - Text from the page or from the daemon.
 * @returns True when it is a generation in the form this client speaks.
 */
export function isGeneration(value: string): boolean {
  return GENERATION.test(value);
}

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
 * Decide what one answer from the daemon means.
 *
 * @param current - Generation the loaded page was rendered from.
 * @param served - What the daemon answered, or null when it did not.
 * @param healed - Whether this page already reloaded itself out of this state.
 * @returns What the page should do about it.
 */
export function decidePoll(
  current: string,
  served: string | null,
  healed: boolean,
): PollOutcome {
  if (served === null) {
    return "retry";
  }
  if (!isGeneration(served) || !isGeneration(current)) {
    // The daemon answered, and what it said is not a generation this client
    // knows — or this page is one the daemon no longer speaks for. Either way
    // the two ends have drifted apart and only a fresh page can close the
    // gap. Once, though: a page that comes back just as unreadable is a page
    // that has to stay put and stay readable.
    return healed ? "same" : "reload";
  }
  return served === current ? "same" : "reload";
}

/**
 * Run one poll, whatever happens.
 *
 * @param watch - Injected generation source and reload action.
 * @returns What the cycle concluded; never throws.
 */
export async function pollOnce(watch: VersionWatch): Promise<PollOutcome> {
  let served: string | null = null;
  try {
    served = await watch.read();
  } catch {
    return "retry";
  }
  let outcome: PollOutcome = "retry";
  try {
    outcome = decidePoll(watch.current, served, watch.healed());
  } catch {
    return "retry";
  }
  if (outcome !== "reload") {
    return outcome;
  }
  try {
    if (served !== null && !isGeneration(served)) {
      watch.remember();
    } else if (!isGeneration(watch.current)) {
      watch.remember();
    }
  } catch {
    // Being unable to remember is not a reason to stay stranded.
  }
  watch.reload();
  return "reload";
}

/**
 * Choose how long to wait before the next poll.
 *
 * @param outcome - What the last cycle concluded.
 * @param delay - How long the last wait was.
 * @param base - The interval a healthy page polls at.
 * @param cap - The longest wait to back off to.
 * @returns The next wait, in milliseconds.
 */
export function nextDelay(
  outcome: PollOutcome,
  delay: number,
  base: number,
  cap: number = MAX_POLL_INTERVAL_MS,
): number {
  if (outcome !== "retry") {
    return base;
  }
  return Math.min(Math.max(delay * 2, base), cap);
}

/** Everything told when a poll cycle finishes. */
const listeners = new Set<(outcome: PollOutcome) => void>();

/**
 * Be told each time a poll cycle finishes.
 *
 * The waiting signs on the page are the audience: "still nothing" is a fact
 * about polls, and it is this watch that knows how many have gone by.
 *
 * @param listener - Told each completed cycle.
 * @returns A function that stops the listening.
 */
export function onPollCycle(
  listener: (outcome: PollOutcome) => void,
): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Tell every listener that a cycle finished.
 *
 * @param outcome - What the cycle concluded.
 */
export function announcePoll(outcome: PollOutcome): void {
  for (const listener of [...listeners]) {
    try {
      listener(outcome);
    } catch {
      // One listener's failure is not the watch's problem.
    }
  }
}

/**
 * Ask the local daemon which generation it would serve right now.
 *
 * @returns What the daemon said, or null when the question did not get
 *     through.
 */
export async function readServedVersion(): Promise<string | null> {
  const response = await fetch(VERSION_PATH, { cache: "no-store" });
  if (!response.ok) {
    return null;
  }
  return (await response.text()).trim();
}

/**
 * Start polling the local daemon for a newer generation of this page.
 *
 * @param root - Document holding the generation meta element.
 * @param intervalMs - Milliseconds between polls when all is well.
 * @returns A function that stops the watch.
 */
export function startVersionWatch(
  root: Document,
  intervalMs: number = pollInterval(root),
): () => void {
  const current = pageVersion(root);
  const watch: VersionWatch = {
    current,
    read: readServedVersion,
    reload: () => window.location.reload(),
    healed: () => readHealedGeneration() === current,
    remember: () => rememberHealedGeneration(current),
  };
  let timer = 0;
  let delay = intervalMs;
  let stopped = false;
  const cycle = async (): Promise<void> => {
    let outcome: PollOutcome = "retry";
    try {
      outcome = await pollOnce(watch);
    } finally {
      delay = nextDelay(outcome, delay, intervalMs);
      if (!stopped) {
        timer = window.setTimeout(() => void cycle(), delay);
      }
      announcePoll(outcome);
    }
  };
  void cycle();
  return () => {
    stopped = true;
    window.clearTimeout(timer);
  };
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
