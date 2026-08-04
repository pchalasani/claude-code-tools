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
  /**
   * What this note is called among its siblings, when the author says.
   *
   * Optional, and a note without one is named by a slug of its title. Either
   * way the name belongs to the note rather than to its slot, so a later
   * publish that inserts a note above it cannot hand its identity away.
   */
  id?: string;
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

export interface SuggestedReply {
  label: string;
  message: string;
}

export interface Item {
  id: string;
  glance: string;
  explanation: string;
  trust: TrustLevel;
  suggestions?: SuggestedReply[];
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

export interface LegacyCurrentState {
  updated_at: string;
  goal: string;
  focus: string;
  blocker: string | null;
  next: string;
}

export interface StructuredCurrentState {
  updated_at: string;
  headline: string;
  summary: string;
  lanes: Lane[];
  questions?: Thread[];
}

export type CurrentState = LegacyCurrentState | StructuredCurrentState;

const LEGACY_CURRENT_STATE_FIELDS = new Set(
  ["updated_at", "goal", "focus", "blocker", "next"],
);
const STRUCTURED_CURRENT_STATE_FIELDS = new Set(
  ["updated_at", "headline", "summary", "lanes", "questions"],
);

/** Report whether current state uses the detailed lane-and-item shape. */
export function isStructuredCurrentState(
  value: CurrentState,
): value is StructuredCurrentState {
  return "lanes" in value;
}

/** Report whether a value has the exact stored current-state shape. */
export function isCurrentState(value: unknown): value is CurrentState {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const state = value as Record<string, unknown>;
  const fields = Object.keys(state);
  const legacy = fields.length === LEGACY_CURRENT_STATE_FIELDS.size
    && fields.every((field) => LEGACY_CURRENT_STATE_FIELDS.has(field))
    && typeof state.updated_at === "string"
    && typeof state.goal === "string"
    && typeof state.focus === "string"
    && (typeof state.blocker === "string" || state.blocker === null)
    && typeof state.next === "string";
  if (legacy) {
    return true;
  }
  return (
    fields.every((field) => STRUCTURED_CURRENT_STATE_FIELDS.has(field))
    && fields.includes("updated_at")
    && fields.includes("headline")
    && fields.includes("summary")
    && fields.includes("lanes")
    && typeof state.updated_at === "string"
    && typeof state.headline === "string"
    && typeof state.summary === "string"
    && Array.isArray(state.lanes)
    && (state.questions === undefined || Array.isArray(state.questions))
  );
}

export interface BriefDocument {
  title: string;
  summary: string;
  current_state?: CurrentState;
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
  const brief = parsed as Record<string, unknown>;
  if (
    Object.hasOwn(brief, "current_state")
    && !isCurrentState(brief.current_state)
  ) {
    throw new Error("embedded brief document has invalid current state");
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
  const current = brief.current_state;
  if (current !== undefined && isStructuredCurrentState(current)) {
    shape.threads += (current.questions ?? []).length;
    for (const lane of current.lanes ?? []) {
      shape.lanes += 1;
      shape.threads += (lane.questions ?? []).length;
      for (const item of lane.items ?? []) {
        shape.items += 1;
        shape.threads += (item.questions ?? []).length;
      }
    }
  }
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
