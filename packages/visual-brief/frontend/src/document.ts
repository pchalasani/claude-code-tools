/**
 * The brief document as the Python renderer embeds it in the page.
 *
 * Python owns validation, thread normalization, legacy conversion and
 * counting. By the time this module sees a document it has already been
 * validated, so the types below describe the delivered shape rather than
 * re-checking it. Reading is still defensive about the one thing the front
 * end controls: whether the embedded blob is present and parseable.
 */

export type TrustLevel =
  | "verified-by-me"
  | "reported-by-agent"
  | "unverified"
  | "known-limitation";

export interface Turn {
  author: "human" | "agent";
  text: string;
  at: string;
}

export interface Anchor {
  kind: "element";
  path: string;
}

export interface Thread {
  id: string;
  anchor: Anchor;
  turns: Turn[];
}

export interface NestedNote {
  title: string;
  body: string;
  children?: NestedNote[];
}

export type Forensic = string | NestedNote;

export interface Table {
  caption: string;
  columns: string[];
  rows: string[][];
}

export interface Item {
  id: string;
  glance: string;
  explanation: string;
  trust: TrustLevel;
  forensics?: Forensic[];
  tables?: Table[];
  questions?: Thread[];
}

export interface Lane {
  id: string;
  name: string;
  open?: boolean;
  items: Item[];
  questions?: Thread[];
}

export interface Update {
  id: string;
  timestamp: string;
  headline: string;
  summary: string;
  lanes: Lane[];
}

export interface BriefDocument {
  title: string;
  summary: string;
  updates: Update[];
}

/** Human-readable name of each trust level. */
export const TRUST_LABELS: Record<TrustLevel, string> = {
  "verified-by-me": "Verified by me",
  "reported-by-agent": "Reported by agent",
  unverified: "Unverified",
  "known-limitation": "Known limitation",
};

/**
 * Non-colour mark carried by each trust chip.
 *
 * Trust is the one place on this page where colour means something, so each
 * chip also carries a glyph. A reader who cannot separate the hues still
 * reads the level.
 */
export const TRUST_MARKS: Record<TrustLevel, string> = {
  "verified-by-me": "✓",
  "reported-by-agent": "~",
  unverified: "?",
  "known-limitation": "!",
};

/** Trust levels in the order the legend lists them. */
export const TRUST_ORDER: TrustLevel[] = [
  "verified-by-me",
  "reported-by-agent",
  "unverified",
  "known-limitation",
];

/**
 * Report whether a thread's newest turn is still waiting for the agent.
 *
 * @param thread - A delivered question thread.
 * @returns True when the newest turn was written by the human.
 */
export function threadIsAwaiting(thread: Thread): boolean {
  const newest = thread.turns[thread.turns.length - 1];
  return newest !== undefined && newest.author === "human";
}

/** Counts of the structure carried by one document. */
export interface DocumentShape {
  updates: number;
  lanes: number;
  items: number;
  threads: number;
}

export const DOCUMENT_SCRIPT_ID = "visual-brief-document";

/**
 * Read the embedded document blob out of the page.
 *
 * @param root - Document holding the embedded JSON script element.
 * @param elementId - Id of the `application/json` script element.
 * @returns The parsed brief document.
 * @throws Error when the blob is missing or is not parseable JSON.
 */
export function readEmbeddedDocument(
  root: Document,
  elementId: string = DOCUMENT_SCRIPT_ID,
): BriefDocument {
  const holder = root.getElementById(elementId);
  if (holder === null) {
    throw new Error(`no embedded brief document with id ${elementId}`);
  }
  const parsed: unknown = JSON.parse(holder.textContent ?? "");
  if (parsed === null || typeof parsed !== "object") {
    throw new Error("embedded brief document is not an object");
  }
  return parsed as BriefDocument;
}

/**
 * Count the structural pieces of a document.
 *
 * @param brief - A delivered brief document.
 * @returns Counts of updates, lanes, items and question threads.
 */
export function describeShape(brief: BriefDocument): DocumentShape {
  const shape: DocumentShape = { updates: 0, lanes: 0, items: 0, threads: 0 };
  for (const update of brief.updates ?? []) {
    shape.updates += 1;
    for (const lane of update.lanes ?? []) {
      shape.lanes += 1;
      shape.threads += (lane.questions ?? []).length;
      for (const item of lane.items ?? []) {
        shape.items += 1;
        shape.threads += (item.questions ?? []).length;
      }
    }
  }
  return shape;
}
