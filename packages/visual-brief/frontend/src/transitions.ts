/**
 * Motion that explains what just happened.
 *
 * Moving the cursor and folding a section are changes the eye should be able
 * to follow, so both go through the View Transitions API: the browser holds
 * the old frame, applies the change, and animates between the two. Where the
 * API is missing, or where the human has asked for less motion, the change
 * still happens — just instantly.
 */

/** The name the cursor row carries so the browser can animate it moving. */
export const CURSOR_TRANSITION_NAME = "brief-cursor";

/**
 * Report whether the human has asked for reduced motion.
 *
 * @returns True when the system prefers reduced motion.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Apply a change, animated when that is welcome and possible.
 *
 * @param change - The state change to apply.
 */
export function withTransition(change: () => void): void {
  if (typeof document === "undefined" || prefersReducedMotion()) {
    change();
    return;
  }
  const start: unknown = document.startViewTransition;
  if (typeof start !== "function") {
    change();
    return;
  }
  document.startViewTransition(change);
}
