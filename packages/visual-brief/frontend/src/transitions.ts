/**
 * Motion that explains what just happened.
 *
 * Moving the cursor and folding a section are changes the eye should be able
 * to follow, so both go through the View Transitions API: the browser holds
 * the old frame, applies the change, and animates between the two. Where the
 * API is missing, or where the human has asked for less motion, the change
 * still happens — just instantly.
 *
 * There is a third case, and it is not about taste. While a view transition
 * runs, the browser captures the whole document into the ``root`` snapshot and
 * paints that snapshot over the page: the live elements underneath stop being
 * hit-testable, so every mouse event lands on the document element instead of
 * on the button the human aimed at. A page that animates on hover therefore
 * spends its life unclickable — worse, the transition itself re-fires
 * ``pointerover`` as the snapshot appears and disappears, which starts the
 * next transition, forever.
 *
 * So motion belongs to the keyboard. When the mouse is the thing driving, the
 * change is applied instantly and the very next click lands where it was
 * aimed.
 */

import { pointerIsDriving } from "./pointer";

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
  if (
    typeof document === "undefined"
    || prefersReducedMotion()
    || pointerIsDriving()
  ) {
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
