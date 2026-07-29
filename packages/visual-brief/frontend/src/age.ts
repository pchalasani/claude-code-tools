/**
 * A compact human reading of how long ago an update arrived.
 *
 * The timestamp remains visible beside this text, so the age is deliberately
 * approximate: it answers "is this new?" while the timestamp answers exactly
 * when.
 */

const RELATIVE = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

interface AgeUnit {
  unit: Intl.RelativeTimeFormatUnit;
  milliseconds: number;
}

const UNITS: AgeUnit[] = [
  { unit: "year", milliseconds: 365 * 24 * 60 * 60 * 1_000 },
  { unit: "month", milliseconds: 30 * 24 * 60 * 60 * 1_000 },
  { unit: "week", milliseconds: 7 * 24 * 60 * 60 * 1_000 },
  { unit: "day", milliseconds: 24 * 60 * 60 * 1_000 },
  { unit: "hour", milliseconds: 60 * 60 * 1_000 },
  { unit: "minute", milliseconds: 60 * 1_000 },
];

/**
 * Describe one timestamp relative to now.
 *
 * @param timestamp - The update's timestamp.
 * @param now - The instant from which to measure.
 * @returns A short relative age, or a clear fallback for legacy prose dates.
 */
export function humanAge(timestamp: string, now: number = Date.now()): string {
  const then = Date.parse(timestamp);
  if (!Number.isFinite(then)) {
    return "age unavailable";
  }
  const difference = then - now;
  if (Math.abs(difference) < 60_000) {
    return "just now";
  }
  const unit =
    UNITS.find((candidate) =>
      Math.abs(difference) >= candidate.milliseconds,
    ) ?? UNITS[UNITS.length - 1];
  if (unit === undefined) {
    return "just now";
  }
  return RELATIVE.format(
    Math.round(difference / unit.milliseconds),
    unit.unit,
  );
}
