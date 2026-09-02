import { createSignal, type Accessor } from "solid-js";
import type { Row } from "./outline";
export const HINT_KEYS = "asdfghjkl";
export interface Hints {
  active: Accessor<boolean>;
  typed: Accessor<string>;
  labelFor: (id: string) => string | null;
  enter: () => void;
  leave: () => void;
  handleKey: (key: string) => boolean;
}
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
const NO_LABELS: ReadonlyMap<string, string> = new Map();
interface Snapshot {
  ids: string[];
  labels: ReadonlyMap<string, string>;
}
function samePage(rows: Row[], ids: string[]): boolean {
  return (
    rows.length === ids.length
    && rows.every((row, index) => row.id === ids[index])
  );
}
export function createHints(deps: {
  rows: () => Row[];
  select: (id: string) => void;
  keys?: string;
}): Hints {
  const keys = deps.keys ?? HINT_KEYS;
  const [snapshot, setSnapshot] = createSignal<Snapshot | null>(null);
  const [typed, setTyped] = createSignal("");
  let checked: { rows: Row[]; current: boolean } | null = null;
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
