/**
 * What this page asks the local daemon, and what it is willing to believe.
 *
 * Two questions are asked of one run. ``render-version`` is sixty-four bytes
 * and is asked every few seconds; ``document`` is the whole brief and is asked
 * only when the first answer says the page has fallen behind.
 *
 * Both are read defensively, and in the same spirit: a question that does not
 * get through is one thing, and an answer this client cannot make sense of is
 * another. The first is a daemon that is not there, which a saved page must
 * survive. The second means the two ends have drifted apart, and the page is
 * the end that can be replaced.
 */

import { isCurrentState, type BriefDocument } from "./document";

/** Run-relative endpoint answering the generation being served. */
export const VERSION_PATH = "render-version";

/** Run-relative endpoint answering the document being served. */
export const DOCUMENT_PATH = "document";

/** Longest one question may hang before it counts as unanswered. */
export const VERSION_TIMEOUT_MS = 10_000;

/** The shape every generation this client understands has. */
const GENERATION = /^[0-9a-f]{64}$/;

/** What the daemon says the page it is serving is made of. */
export interface DocumentPayload {
  /** Generation of the page this document came out of. */
  generation: string;
  /** Identity of the front-end bundle that page carries. */
  assets: string;
  /** Identity of the physical run that page carries. */
  instance: string;
  /** The brief itself, exactly as that page embeds it. */
  document: BriefDocument;
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
 * Read one answer from the document endpoint, if it is one at all.
 *
 * Nothing here re-validates the brief: Python owns validation, and by the time
 * a document is embedded in a page it has been through it. What is checked is
 * only that this is the answer to the question that was asked — the four
 * fields, in the shapes the page acts on. Anything else is a daemon speaking a
 * language this page does not, which is a reason to reload rather than to
 * patch.
 *
 * @param value - Whatever the endpoint answered, already parsed.
 * @returns The payload, or null when it is not one.
 */
export function readDocumentPayload(value: unknown): DocumentPayload | null {
  if (value === null || typeof value !== "object") {
    return null;
  }
  const answer = value as Record<string, unknown>;
  const { generation, assets, instance, document } = answer;
  if (typeof generation !== "string" || !isGeneration(generation)) return null;
  if (typeof assets !== "string" || typeof instance !== "string") return null;
  if (document === null || typeof document !== "object") return null;
  const brief = document as Record<string, unknown>;
  if (
    typeof brief.title !== "string"
    || typeof brief.summary !== "string"
    || !Array.isArray(brief.updates)
    || (
      Object.hasOwn(brief, "current_state")
      && !isCurrentState(brief.current_state)
    )
  ) {
    return null;
  }
  return { generation, assets, instance, document: document as BriefDocument };
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
  try {
    return (await ask(VERSION_PATH, timeoutMs)).trim();
  } catch {
    // Refused, aborted, or out of time: all of them mean the page learned
    // nothing this cycle, and none of them mean it should reload.
    return null;
  }
}

/**
 * Ask the local daemon for the document it is serving.
 *
 * Unlike the version question, failure here is not swallowed. This is only
 * ever asked once the page already knows it has fallen behind, so a daemon
 * that cannot answer is a daemon this page cannot be patched from — and the
 * caller's fallback is the reload that has always worked.
 *
 * @param timeoutMs - How long to wait before giving up on this question.
 * @returns The parsed answer.
 * @throws Error when the endpoint is missing, unreachable or unparseable.
 */
export async function readServedDocument(
  timeoutMs: number = VERSION_TIMEOUT_MS,
): Promise<unknown> {
  return JSON.parse(await ask(DOCUMENT_PATH, timeoutMs)) as unknown;
}

/**
 * Ask one run-relative endpoint, under a deadline of its own.
 *
 * @param path - Run-relative endpoint to ask.
 * @param timeoutMs - How long to wait before giving up.
 * @returns The response body as text.
 * @throws Error when the request fails or the daemon refuses.
 */
async function ask(path: string, timeoutMs: number): Promise<string> {
  const giveUp = new AbortController();
  const deadline = setTimeout(() => giveUp.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      cache: "no-store",
      signal: giveUp.signal,
    });
    if (!response.ok) {
      throw new Error(`the daemon answered ${response.status}`);
    }
    return await response.text();
  } finally {
    clearTimeout(deadline);
  }
}
