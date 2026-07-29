let lastPoint: { x: number; y: number } | null = null;
let pointerDriving = false;
export function pointerIsDriving(): boolean {
  return pointerDriving;
}
export function keyboardTookOver(): void {
  pointerDriving = false;
}
export function explicitSelectionTookOver(): void {
  pointerDriving = false;
}
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
    if (lastPoint !== null && lastPoint.x === x && lastPoint.y === y) {
      return;
    }
    lastPoint = { x, y };
    pointerDriving = true;
  };
  const onKey = (): void => {
    keyboardTookOver();
  };
  target.addEventListener("pointermove", onMove, {
    passive: true,
    capture: true,
  });
  target.addEventListener("keydown", onKey, true);
  return () => {
    target.removeEventListener("pointermove", onMove, true);
    target.removeEventListener("keydown", onKey, true);
  };
}
