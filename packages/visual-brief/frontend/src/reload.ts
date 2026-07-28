/**
 * Keep an open page in step with the run it is showing.
 *
 * The agent rewrites the page whenever it publishes, so a page left open must
 * notice. It compares the generation baked into its own markup with the one
 * the local daemon reports, and when the two differ it fetches the new
 * document and hands it to the running application. The page stays alive and
 * changes. It used to throw itself away instead, which is why it flickered,
 * why content jumped under the reader, and why anything the reader was in the
 * middle of — a scroll position, a half-written message — was lost every time
 * the agent said anything.
 *
 * A reload is still the answer to two situations, and only these two:
 *
 * - the served page carries a DIFFERENT front-end bundle. Only a reload loads
 *   code, and a document patched into a tab running last week's code leaves
 *   that tab running last week's code forever.
 * - the page cannot be patched at all — no document endpoint, nothing
 *   readable, or an application that refused what it was given.
 *
 * Everything else about this watch is unchanged, and deliberately blunt:
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
 * - Healing is remembered, so a page that comes back exactly as it left does
 *   not reload forever. What is remembered is the STANDOFF — this page's own
 *   generation and the answer it could not reconcile with — and not merely the
 *   page. Remembering only the page was a way for a tab to go stale for good:
 *   a page carrying no generation at all can never match anything, so after
 *   one heal it treated every later answer as the same impasse and stopped
 *   noticing publishes for the life of the tab, still running whatever code it
 *   was serving. A changed answer is a changed situation and is worth exactly
 *   one more reload.
 */

import type { BriefDocument } from "./document";
import {
  isGeneration,
  readDocumentPayload,
  readServedDocument,
  readServedVersion,
  type DocumentPayload,
} from "./document-feed";
import { pageAssets, pageVersion, pollInterval } from "./page-meta";
import { readHealedStandoff, rememberHealedStandoff } from "./session-store";

/** Slowest the watch backs off to while the daemon is unreachable. */
export const MAX_POLL_INTERVAL_MS = 60_000;

/** What one poll cycle concluded. */
export type PollOutcome = "same" | "patched" | "reload" | "retry";

/** Everything one poll needs, injected so it can be driven in tests. */
export interface VersionWatch {
  /**
   * Generation the page is currently showing.
   *
   * Written by a successful patch, because after one the page is showing
   * something other than what it was rendered from.
   */
  current: string;
  /** Identity of the front-end bundle this page is running. */
  assets: string;
  /**
   * Read what the server would serve right now.
   *
   * A null answer means the question did not get through — no daemon, an
   * error status, a run that has gone away. Any other answer is the server
   * speaking, and is judged on whether it can be understood.
   */
  read: () => Promise<string | null>;
  /** Fetch the document the daemon is serving; throws when it cannot. */
  fetchPayload: () => Promise<unknown>;
  /** Show a newly delivered document; throws when it cannot be shown. */
  apply: (document: BriefDocument) => void;
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
}

/** How a real page answers the questions a watch asks. */
export interface WatchSources {
  /** Generation the loaded page was rendered from. */
  current: string;
  /** Identity of the front-end bundle the loaded page carries. */
  assets: string;
  /** How to ask the daemon what it would serve. */
  read: () => Promise<string | null>;
  /** How to fetch the document the daemon is serving. */
  fetchPayload: () => Promise<unknown>;
  /** What showing a newly delivered document does. */
  apply: (document: BriefDocument) => void;
  /** What replacing the loaded page does. */
  reload: () => void;
}

/**
 * Decide what one answer from the daemon means.
 *
 * "Reload" here means only that the page has fallen behind. Whether it is
 * brought up to date by patching or by replacing itself is settled afterwards,
 * by whether the new document can be fetched and shown.
 *
 * @param current - Generation the page is showing.
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
 * @param watch - Injected generation source, patcher and reload action.
 * @returns What the cycle concluded; never throws.
 */
export async function pollOnce(watch: VersionWatch): Promise<PollOutcome> {
  let served: string | null = null;
  try {
    served = await watch.read();
  } catch {
    return "retry";
  }
  let healed = false;
  let outcome: PollOutcome = "retry";
  try {
    healed = watch.healed(served);
    outcome = decidePoll(watch.current, served, healed);
  } catch {
    return "retry";
  }
  if (outcome !== "reload") {
    return outcome;
  }
  if (served !== null && comparable(watch.current, served)) {
    if (await patchInPlace(watch)) {
      return "patched";
    }
    // The page has fallen behind and cannot be brought forward without being
    // replaced. A page that already replaced itself out of this exact standoff
    // and came back to it stays put, and stays readable.
    if (healed) {
      return "same";
    }
  }
  try {
    watch.remember(served);
  } catch {
    // Being unable to remember is not a reason to stay stranded.
  }
  watch.reload();
  return "reload";
}

/**
 * Bring the running page up to date without replacing it.
 *
 * @param watch - The watch to patch through.
 * @returns True when the page is now showing the served document.
 */
async function patchInPlace(watch: VersionWatch): Promise<boolean> {
  let payload: DocumentPayload | null = null;
  try {
    payload = readDocumentPayload(await watch.fetchPayload());
  } catch {
    return false;
  }
  if (payload === null || payload.assets !== watch.assets) {
    // Either the daemon cannot be understood, or what it is serving is built
    // from different code. Code arrives only by loading a page.
    return false;
  }
  try {
    watch.apply(payload.document);
  } catch {
    return false;
  }
  // The generation adopted is the one the payload carried, not the one the
  // poll reported. A second publish landing between the two would otherwise
  // leave the page believing it is showing something it is not.
  watch.current = payload.generation;
  return true;
}

/**
 * Report whether two ends are speaking about generations at all.
 *
 * @param current - Generation the page is showing.
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
 * Build the watch a real page polls on, memory and all.
 *
 * The memory is the whole reason this is a named thing rather than an object
 * literal inside the watch: "reload once out of a state you cannot get out of"
 * is only true if the page can still say, after the reload, that it was here
 * before. That claim is worth testing against the store it is actually kept
 * in, so this is what the tests drive too.
 *
 * @param sources - How this page answers the watch's questions.
 * @returns The watch.
 */
export function healingWatch(sources: WatchSources): VersionWatch {
  const watch: VersionWatch = {
    current: sources.current,
    assets: sources.assets,
    read: sources.read,
    fetchPayload: sources.fetchPayload,
    apply: sources.apply,
    reload: sources.reload,
    // Both read `watch.current` rather than the generation this page was
    // rendered from: a patched page is showing something else, and a standoff
    // is about what is on the screen now.
    healed: (served) =>
      readHealedStandoff() === standoffName(watch.current, served),
    remember: (served) =>
      rememberHealedStandoff(standoffName(watch.current, served)),
  };
  return watch;
}

/**
 * Name one impasse between a loaded page and the daemon's answer.
 *
 * Both halves may be unreadable — that is what an impasse is — so the name is
 * built by an encoder that cannot be confused by their contents rather than by
 * joining them with a separator one of them might contain.
 *
 * @param current - Generation the page is showing.
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
 * Refuse to show a document, for a page that has no application to show it.
 *
 * A page whose bundle failed to read its own embedded document has nothing to
 * patch into, and is exactly the page that has to replace itself with one that
 * works.
 */
function refuseToPatch(): never {
  throw new Error("this page has no live document to patch");
}

/**
 * Start watching the local daemon for a newer generation of this page.
 *
 * @param root - Document holding the generation and bundle meta elements.
 * @param apply - How to show a newly delivered document.
 * @param intervalMs - Milliseconds between polls when all is well.
 * @returns A function that stops the watch.
 */
export function startVersionWatch(
  root: Document,
  apply: (document: BriefDocument) => void = refuseToPatch,
  intervalMs: number = pollInterval(root),
): () => void {
  const watch = healingWatch({
    current: pageVersion(root),
    assets: pageAssets(root),
    read: readServedVersion,
    fetchPayload: readServedDocument,
    apply,
    reload: () => window.location.reload(),
  });
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
