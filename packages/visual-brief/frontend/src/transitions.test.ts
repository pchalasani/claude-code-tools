import { afterEach, describe, expect, it } from "vitest";

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
});

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
