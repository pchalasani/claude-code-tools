/**
 * Jumping straight to a row by typing its label.
 *
 * Walking to a row costs one key press per row between here and there, which
 * on a long page is a lot of presses aimed at nothing in particular. Hint mode
 * turns every row the page is painting into a two-keystroke destination: one
 * key paints a short label onto each row, and typing a label goes there.
 *
 * Three rules keep it unambiguous. Labels are the same length across the whole
 * page, so no label is a prefix of another and no typed label ever has to wait
 * to see whether more is coming. The labels are a snapshot taken when the mode
 * is entered, so a row cannot renumber itself under the fingers that are
 * halfway through typing its label. And that snapshot describes one painted
 * page: the moment anything else repaints the page — an expand-all clicked
 * with the mouse, new content arriving — the labels go away and the keyboard
 * goes back to meaning what it usually means, because half a page of labels is
 * worse than none.
 *
 * Going away is permanent, not a state the page can wander back into. A row
 * opened with the mouse and then shut again paints the same list it painted
 * before, and if that were enough to bring the labels back the human would be
 * holding a keyboard that had silently stopped answering — including ``?``,
 * the one key that would have told them Escape was the way out.
 */

import { createSignal, type Accessor } from "solid-js";

import type { Row } from "./outline";

/**
 * The keys labels are built from, in the order they are handed out.
 *
 * The home row, and only the home row: a jump that needs the reader to look
 * at the keyboard is slower than pressing ``j`` a dozen times.
 */
export const HINT_KEYS = "asdfghjkl";

/** The hint layer's state, as the page paints and drives it. */
export interface Hints {
  /** Whether the labels are showing. */
  active: Accessor<boolean>;
  /** What has been typed at the labels so far. */
  typed: Accessor<string>;
  /** The label painted on one row, if it carries one. */
  labelFor: (id: string) => string | null;
  /** Show the labels. */
  enter: () => void;
  /** Take the labels away, leaving the cursor where it was. */
  leave: () => void;
  /**
   * Offer one key to the labels.
   *
   * @returns Whether the hint layer consumed it, in which case nothing else
   *     on the page may act on it.
   */
  handleKey: (key: string) => boolean;
}

/**
 * Build fixed-length labels for a number of destinations.
 *
 * Single keys are used while they last, then every label grows by one key at
 * once. Mixed lengths would make ``a`` both a label and the first half of
 * ``as``, and the page would have to guess which the human meant.
 *
 * @param count - How many rows need a label.
 * @param keys - The keys labels are built from.
 * @returns One label per row, in order.
 */
export function hintLabels(count: number, keys: string = HINT_KEYS): string[] {
  const alphabet = [...keys];
  if (count <= 0 || alphabet.length < 2) {
    return [];
  }
  let width = 1;
  while (alphabet.length ** width < count) {
    width += 1;
  }
  const labels: string[] = [];
  for (let index = 0; index < count; index += 1) {
    let remaining = index;
    let label = "";
    for (let place = 0; place < width; place += 1) {
      label = (alphabet[remaining % alphabet.length] ?? "") + label;
      remaining = Math.floor(remaining / alphabet.length);
    }
    labels.push(label);
  }
  return labels;
}

/**
 * Label a list of rows.
 *
 * @param rows - The rows the page is painting, in document order.
 * @param keys - The keys labels are built from.
 * @returns Each row's label, keyed by row id.
 */
export function labelRows(
  rows: Row[],
  keys: string = HINT_KEYS,
): Map<string, string> {
  const labels = hintLabels(rows.length, keys);
  const painted = new Map<string, string>();
  rows.forEach((row, index) => {
    const label = labels[index];
    if (label !== undefined) {
      painted.set(row.id, label);
    }
  });
  return painted;
}

/** What a page nobody is jumping around paints. */
const NO_LABELS: ReadonlyMap<string, string> = new Map();

/** The labels handed out, and the page they were handed out for. */
interface Snapshot {
  /** The rows painted when the labels were taken, in order. */
  ids: string[];
  /** Each row's label. */
  labels: ReadonlyMap<string, string>;
}

/**
 * Report whether a snapshot still describes what is on the screen.
 *
 * @param rows - The rows the page is painting now.
 * @param ids - The rows it was painting when the labels were taken.
 * @returns True when they are the same rows in the same order.
 */
function samePage(rows: Row[], ids: string[]): boolean {
  return (
    rows.length === ids.length
    && rows.every((row, index) => row.id === ids[index])
  );
}

/**
 * Build the hint layer for one page.
 *
 * @param deps - Where the labelled rows come from and what a jump does.
 * @returns The live hint state.
 */
export function createHints(deps: {
  /** The rows the page is painting right now. */
  rows: () => Row[];
  /** Put the cursor on one row. */
  select: (id: string) => void;
  /** The keys labels are built from. */
  keys?: string;
}): Hints {
  const keys = deps.keys ?? HINT_KEYS;
  const [snapshot, setSnapshot] = createSignal<Snapshot | null>(null);
  const [typed, setTyped] = createSignal("");
  // What the last comparison was made against. The rows come from a memo, so
  // the same painted page is the same array: the check is an identity test on
  // every read and a walk only when the page has actually been repainted.
  let checked: { rows: Row[]; current: boolean } | null = null;
  // Set once the page has moved on, and never cleared except by entering or
  // leaving the mode. A plain flag rather than a signal because it is written
  // from inside ``labels``, which runs while the page is being painted.
  let stale = false;

  const labels = (): ReadonlyMap<string, string> => {
    const taken = snapshot();
    if (taken === null || stale) {
      return NO_LABELS;
    }
    const rows = deps.rows();
    if (checked === null || checked.rows !== rows) {
      checked = { rows, current: samePage(rows, taken.ids) };
    }
    if (!checked.current) {
      stale = true;
      return NO_LABELS;
    }
    return taken.labels;
  };

  const leave = (): void => {
    checked = null;
    stale = false;
    setSnapshot(null);
    setTyped("");
  };

  const enter = (): void => {
    const rows = deps.rows();
    setTyped("");
    checked = { rows, current: true };
    stale = false;
    setSnapshot({
      ids: rows.map((row) => row.id),
      labels: labelRows(rows, keys),
    });
  };

  const active = (): boolean => labels().size > 0;

  const handleKey = (key: string): boolean => {
    if (!active()) {
      return false;
    }
    if (key === "Escape") {
      leave();
      return true;
    }
    if (!keys.includes(key) || key.length !== 1) {
      // Every other key is swallowed rather than obeyed: the labels are on the
      // screen, and a key that quietly moved the cursor underneath them would
      // send the next keystroke somewhere nobody chose.
      return true;
    }
    const wanted = typed() + key;
    const matches = [...labels()].filter(([, label]) =>
      label.startsWith(wanted),
    );
    const exact = matches.find(([, label]) => label === wanted);
    if (exact !== undefined) {
      leave();
      deps.select(exact[0]);
      return true;
    }
    if (matches.length > 0) {
      setTyped(wanted);
    }
    return true;
  };

  return {
    active,
    typed,
    labelFor: (id) => labels().get(id) ?? null,
    enter,
    leave,
    handleKey,
  };
}
