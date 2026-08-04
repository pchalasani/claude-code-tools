/** Human-readable absolute and relative timestamps. */

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

interface AgeUnit {
  milliseconds: number;
  suffix: (count: number) => string;
}

const UNITS: AgeUnit[] = [
  {
    milliseconds: 365 * 24 * 60 * 60 * 1_000,
    suffix: (count) => count === 1 ? "y" : "yrs",
  },
  {
    milliseconds: 30 * 24 * 60 * 60 * 1_000,
    suffix: (count) => count === 1 ? "mo" : "mos",
  },
  { milliseconds: 7 * 24 * 60 * 60 * 1_000, suffix: () => "w" },
  { milliseconds: 24 * 60 * 60 * 1_000, suffix: () => "d" },
  { milliseconds: 60 * 60 * 1_000, suffix: () => "h" },
  { milliseconds: 60 * 1_000, suffix: () => "min" },
];

const MACHINE_TIMESTAMP =
  /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2}| [A-Za-z]{2,5})?$/;

function parseMachineTimestamp(timestamp: string): number | undefined {
  if (!MACHINE_TIMESTAMP.test(timestamp)) {
    return undefined;
  }
  const milliseconds = Date.parse(timestamp);
  return Number.isFinite(milliseconds) ? milliseconds : undefined;
}

/**
 * Format a timestamp for people in the browser's local timezone.
 *
 * @param timestamp - A machine timestamp or legacy prose value.
 * @returns Local time in `2-Aug-26 3:22 PM` form, or the original prose.
 */
export function formatTimestamp(timestamp: string): string {
  const milliseconds = parseMachineTimestamp(timestamp);
  if (milliseconds === undefined) {
    return timestamp;
  }
  const date = new Date(milliseconds);
  const hour = date.getHours();
  const clockHour = hour % 12 || 12;
  const minute = String(date.getMinutes()).padStart(2, "0");
  const year = String(date.getFullYear() % 100).padStart(2, "0");
  const meridiem = hour < 12 ? "AM" : "PM";
  const datePart = `${date.getDate()}-${MONTHS[date.getMonth()]}-${year}`;
  return `${datePart} ${clockHour}:${minute} ${meridiem}`;
}

/**
 * Describe one timestamp relative to now.
 *
 * @param timestamp - The update's timestamp.
 * @param now - The instant from which to measure.
 * @returns A short relative age, or a clear fallback for legacy prose dates.
 */
export function humanAge(timestamp: string, now: number = Date.now()): string {
  const then = parseMachineTimestamp(timestamp);
  if (then === undefined) {
    return "age unavailable";
  }
  const difference = Math.abs(now - then);
  if (Math.abs(difference) < 60_000) {
    return "just now";
  }
  const unit =
    UNITS.find((candidate) =>
      difference >= candidate.milliseconds,
    ) ?? UNITS[UNITS.length - 1];
  if (unit === undefined) {
    return "just now";
  }
  const count = Math.max(1, Math.round(difference / unit.milliseconds));
  return `${count}${unit.suffix(count)}`;
}
