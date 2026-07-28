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
 * - A question that hangs is not an answer either. Every version request
 *   carries its own deadline, because the next cycle is scheduled only once
 *   this one settles and one frozen daemon must not end the watch for the
 *   life of the tab.
 * - Healing is remembered, so a page that comes back exactly as
 *   unintelligible as it left does not reload forever. What is remembered is
 *   the STANDOFF — this page's own generation and the answer it could not
 *   reconcile with — and not merely the page. Remembering only the page was a
 *   way for a tab to go stale for good: a page carrying no generation at all
 *   can never match anything, so after one heal it treated every later answer
 *   as the same impasse and stopped noticing publishes for the life of the
 *   tab, still running whatever code it was serving. A changed answer is a
 *   changed situation and is worth exactly one more reload.
 */

import {
  markSelfReload,
  readHealCount,
  readHealedStandoff,
  rememberHealCount,
  rememberHealedStandoff,
} from "./session-store";

export const VERSION_META = "visual-brief-render-version";
export const POLL_META = "visual-brief-poll-ms";
export const VERSION_PATH = "render-version";
export const POLL_INTERVAL_MS = 5000;

/** Slowest the watch backs off to while the daemon is unreachable. */
export const MAX_POLL_INTERVAL_MS = 60_000;

/** Reloads a tab may spend healing before it stays put and stays readable. */
export const MAX_HEALS = 3;

/** Longest one version question may hang before it counts as unanswered. */
export const VERSION_TIMEOUT_MS = 10_000;

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
  /**
   * Whether this page already reloaded itself out of this exact standoff.
   *
   * The daemon's answer is part of the standoff: coming back to the same
   * impasse is a reason to stay put, but a DIFFERENT answer means something
   * changed at the other end, and a page that cannot read the answer cannot
   * tell whether what changed is the very thing it is showing.
   */
  healed: (served: string | null) => boolean;
  /** Remember that this page reloaded itself out of this standoff. */
  remember: (served: string | null) => void;
  /** Note that the two ends understand each other again. */
  recovered: () => void;
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
    outcome = decidePoll(watch.current, served, watch.healed(served));
  } catch {
    return "retry";
  }
  if (outcome !== "reload") {
    if (outcome === "same" && served !== null && comparable(watch.current, served)) {
      // The two ends speak the same language again. Whatever budget earlier
      // trouble spent is returned, so a long-lived tab can still heal a
      // genuine upgrade months later.
      try {
        watch.recovered();
      } catch {
        // Forgetting is not worth failing a poll over.
      }
    }
    return outcome;
  }
  try {
    if (served !== null && !comparable(watch.current, served)) {
      watch.remember(served);
    }
  } catch {
    // Being unable to remember is not a reason to stay stranded.
  }
  watch.reload();
  return "reload";
}

/**
 * Report whether two ends are speaking about generations at all.
 *
 * @param current - Generation the loaded page was rendered from.
 * @param served - What the daemon answered.
 * @returns True when both are generations this client can compare.
 */
function comparable(current: string, served: string): boolean {
  return isGeneration(served) && isGeneration(current);
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
 * The question is given a deadline, because ``fetch`` has none: a daemon that
 * accepts the connection and then never answers would otherwise leave this
 * promise pending forever, and the next cycle is only scheduled once this one
 * settles. One hung request would end the watch for the life of the tab. A
 * request that runs out of time is no answer at all, which is the case the
 * poller already knows how to back off from.
 *
 * @param timeoutMs - How long to wait before giving up on this question.
 * @returns What the daemon said, or null when the question did not get
 *     through.
 */
export async function readServedVersion(
  timeoutMs: number = VERSION_TIMEOUT_MS,
): Promise<string | null> {
  const giveUp = new AbortController();
  const deadline = setTimeout(() => giveUp.abort(), timeoutMs);
  try {
    const response = await fetch(VERSION_PATH, {
      cache: "no-store",
      signal: giveUp.signal,
    });
    if (!response.ok) {
      return null;
    }
    return (await response.text()).trim();
  } catch {
    // Refused, aborted, or out of time: all of them mean the page learned
    // nothing this cycle, and none of them mean it should reload.
    return null;
  } finally {
    clearTimeout(deadline);
  }
}

/**
 * Build the watch a real page polls on, memory and all.
 *
 * The memory is the whole reason this is a named thing rather than an object
 * literal inside the watch: "reload once out of a state you cannot read" is
 * only true if the page can still say, after the reload, that it was here
 * before. That claim is worth testing against the store it is actually kept
 * in, so this is what the tests drive too.
 *
 * @param current - Generation the loaded page was rendered from.
 * @param read - How to ask the daemon what it would serve.
 * @param reload - What replacing the loaded page does.
 * @returns The watch.
 */
export function healingWatch(
  current: string,
  read: () => Promise<string | null>,
  reload: () => void,
): VersionWatch {
  return {
    current,
    read,
    reload,
    // Two guards, because they answer different questions. The standoff
    // stops a page bouncing on the SAME impasse; the budget stops a daemon
    // whose unreadable answer keeps changing — an uptime counter, a clock —
    // from presenting a brand-new impasse on every poll and earning a fresh
    // reload forever. A genuine upgrade still heals; an endless one cannot.
    healed: (served) =>
      readHealedStandoff() === standoffName(current, served)
      || readHealCount() >= MAX_HEALS,
    remember: (served) => {
      rememberHealedStandoff(standoffName(current, served));
      rememberHealCount(readHealCount() + 1);
    },
    recovered: () => {
      if (readHealCount() !== 0) {
        rememberHealCount(0);
      }
    },
  };
}

/**
 * Name one impasse between a loaded page and the daemon's answer.
 *
 * Both halves are unreadable by definition — that is what an impasse is — so
 * the name is built by a encoder that cannot be confused by their contents
 * rather than by joining them with a separator one of them might contain.
 *
 * @param current - Generation the loaded page was rendered from.
 * @param served - What the daemon answered, or null when it did not.
 * @returns A name no other impasse answers to.
 */
export function standoffName(
  current: string,
  served: string | null,
): string {
  return JSON.stringify([current, served]);
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
  const watch = healingWatch(
    pageVersion(root),
    readServedVersion,
    () => {
      markSelfReload();
      window.location.reload();
    },
  );
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
