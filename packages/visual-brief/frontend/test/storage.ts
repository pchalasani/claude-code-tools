/**
 * Taking the browser's stores away, the way a real browser takes them away.
 *
 * A browser that has session storage switched off — a private window, a
 * policy, a quota that has run out — does not answer null. It throws, and it
 * throws on the read as much as on the write. Anything the page promises to
 * remember has to be asserted against that, because it is the case the page
 * gets wrong silently.
 */

/**
 * Forget everything this page has remembered about itself.
 *
 * Both stores, because a test that cleared only one would be handed the other
 * one's leftovers and pass for the wrong reason.
 */
export function forgetStores(): void {
  window.sessionStorage.clear();
  window.history.replaceState(null, "");
}

/**
 * Run one body with session storage refusing every request.
 *
 * @param body - What to run while the store is unavailable.
 * @returns Whatever the body settles to.
 */
export async function withoutSessionStorage(
  body: () => Promise<void> | void,
): Promise<void> {
  const original = Object.getOwnPropertyDescriptor(window, "sessionStorage");
  Object.defineProperty(window, "sessionStorage", {
    configurable: true,
    get() {
      throw new Error("session storage is unavailable");
    },
  });
  try {
    await body();
  } finally {
    if (original === undefined) {
      Reflect.deleteProperty(window, "sessionStorage");
    } else {
      Object.defineProperty(window, "sessionStorage", original);
    }
  }
}
