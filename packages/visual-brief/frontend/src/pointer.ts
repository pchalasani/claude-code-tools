/**
 * Which input device is currently driving the cursor.
 *
 * Hover selects a row, and the keyboard scrolls the selected row into view.
 * Those two rules fight: moving the cursor with a key scrolls the page, a
 * different row slides under the stationary mouse, and the browser fires
 * ``pointerover`` for it. The hover rule then moves the cursor a second time,
 * so one key press jumps two rows — or, when the scroll puts the pointer back
 * over the row it started on, appears to do nothing but scroll.
 *
 * The fix is to believe hover only when the mouse has actually moved. A
 * ``pointerover`` that arrives without a preceding ``pointermove`` was caused
 * by the page moving, not by the human.
 */

/** Last cursor position seen from a real mouse move. */
let lastPoint: { x: number; y: number } | null = null;

/** Whether the pointer, rather than the keyboard, is currently in charge. */
let pointerDriving = false;

/**
 * Report whether hover should be allowed to move the cursor.
 *
 * @returns True when the human has moved the mouse since the last key press.
 */
export function pointerIsDriving(): boolean {
  return pointerDriving;
}

/**
 * Note that the keyboard took over, so hover stops claiming the cursor.
 */
export function keyboardTookOver(): void {
  pointerDriving = false;
}

/**
 * Watch the window for real mouse movement.
 *
 * @param target - Event target to listen on, defaulting to the window.
 * @returns A function that removes the listeners.
 */
export function watchPointer(target: EventTarget = window): () => void {
  const onMove = (event: Event): void => {
    const move = event as PointerEvent;
    if (move.pointerType !== undefined && move.pointerType !== "mouse") {
      return;
    }
    const x = move.clientX;
    const y = move.clientY;
    if (typeof x !== "number" || typeof y !== "number") {
      return;
    }
    // A scroll can also emit pointermove with unchanged coordinates; only a
    // genuine change of position counts as the human moving the mouse.
    if (lastPoint !== null && lastPoint.x === x && lastPoint.y === y) {
      return;
    }
    lastPoint = { x, y };
    pointerDriving = true;
  };
  const onKey = (): void => {
    keyboardTookOver();
  };
  target.addEventListener("pointermove", onMove, { passive: true });
  target.addEventListener("keydown", onKey, true);
  return () => {
    target.removeEventListener("pointermove", onMove);
    target.removeEventListener("keydown", onKey, true);
  };
}
