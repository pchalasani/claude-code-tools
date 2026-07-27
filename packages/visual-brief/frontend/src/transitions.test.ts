import { afterEach, describe, expect, it } from "vitest";

import { keyboardTookOver, pointerIsDriving, watchPointer } from "./pointer";
import { prefersReducedMotion, withTransition } from "./transitions";

type Motion = { matches: boolean };

const realMatchMedia = window.matchMedia;
const realStart = document.startViewTransition;

/**
 * Make the environment report a motion preference.
 *
 * @param reduce - Whether the environment asks for reduced motion.
 */
function prefer(reduce: boolean): void {
  window.matchMedia = ((query: string) =>
    ({
      matches: query.includes("reduce") ? reduce : false,
    }) as Motion as MediaQueryList) as typeof window.matchMedia;
}

/**
 * Give the environment a view-transition implementation that records use.
 *
 * @returns The list the implementation appends to when it is used.
 */
function recordTransitions(): string[] {
  const used: string[] = [];
  document.startViewTransition = ((update: () => void) => {
    used.push("started");
    update();
    return {} as ViewTransition;
  }) as typeof document.startViewTransition;
  return used;
}

afterEach(() => {
  window.matchMedia = realMatchMedia;
  document.startViewTransition = realStart;
  keyboardTookOver();
});

/**
 * Put the mouse in charge, the way a real mouse movement does.
 *
 * @returns A function that stops watching the pointer.
 */
function mouseTakesOver(): () => void {
  const target = new EventTarget();
  const stop = watchPointer(target);
  target.dispatchEvent(
    new MouseEvent("pointermove", { clientX: 41, clientY: 97 }),
  );
  return stop;
}

describe("motion", () => {
  it("animates the change when motion is welcome", () => {
    prefer(false);
    const used = recordTransitions();
    let applied = false;

    withTransition(() => {
      applied = true;
    });

    expect(prefersReducedMotion()).toBe(false);
    expect(used).toEqual(["started"]);
    expect(applied).toBe(true);
  });

  it("applies the change without animating it when motion is not", () => {
    prefer(true);
    const used = recordTransitions();
    let applied = false;

    withTransition(() => {
      applied = true;
    });

    expect(prefersReducedMotion()).toBe(true);
    expect(used).toEqual([]);
    expect(applied).toBe(true);
  });

  it("stands aside while the mouse is the thing driving", () => {
    // A running view transition captures the whole document, so nothing under
    // it can be clicked. Animating a change the mouse just made would cost the
    // human the click they are about to make.
    prefer(false);
    const used = recordTransitions();
    const stop = mouseTakesOver();
    let applied = 0;

    withTransition(() => {
      applied += 1;
    });

    expect(pointerIsDriving()).toBe(true);
    expect(used).toEqual([]);
    expect(applied).toBe(1);

    keyboardTookOver();
    withTransition(() => {
      applied += 1;
    });

    expect(used).toEqual(["started"]);
    expect(applied).toBe(2);
    stop();
  });

  it("applies the change where the browser has no view transitions", () => {
    prefer(false);
    Reflect.deleteProperty(document, "startViewTransition");
    let applied = false;

    withTransition(() => {
      applied = true;
    });

    expect(applied).toBe(true);
  });
});
