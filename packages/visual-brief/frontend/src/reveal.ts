/**
 * The two things the page asks the browser to do to itself.
 *
 * Everywhere else the application decides and paints; here it scrolls and it
 * focuses, which are the only two effects the browser will not take from a
 * value. Both are collected in one place so the rest of the code never reaches
 * for the DOM in passing.
 */

/**
 * Bring one row into comfortable reading position.
 *
 * The row carries a generous scroll margin, so the page moves around the
 * cursor rather than pinning the cursor to an edge of the window.
 *
 * @param id - Row id to scroll to.
 */
export function scrollRowIntoView(id: string): void {
  if (typeof document === "undefined") {
    return;
  }
  const row = document.querySelector(`[data-row-id=${JSON.stringify(id)}]`);
  const head = row?.querySelector(".row-head");
  if (head instanceof Element && typeof head.scrollIntoView === "function") {
    head.scrollIntoView({ block: "nearest" });
  }
}

/**
 * Bring one row into reading position and keep it there.
 *
 * A page that reloads itself is competing with the browser, which restores
 * the scroll offset of the page it is replacing — after this code has run,
 * and with no regard for where the human was actually writing. Rather than
 * turn that restoration off for every reload, which is worth having when
 * nothing in particular is being returned to, this simply has the last word:
 * once now, once after the next frame, and once after the browser has
 * finished loading and restoring.
 *
 * @param id - Row id to end up looking at.
 */
export function revealRowSoon(id: string): void {
  if (typeof window === "undefined") {
    return;
  }
  const bring = (): void => scrollRowIntoView(id);
  bring();
  window.requestAnimationFrame(bring);
  if (document.readyState !== "complete") {
    window.addEventListener(
      "load",
      () => window.requestAnimationFrame(bring),
      { once: true },
    );
  }
}

/**
 * Put the browser's text caret in a box once it exists.
 *
 * Typing needs the browser's focus; selection does not. This is the only
 * place the page asks for focus, and it asks only for text boxes.
 *
 * @param selector - Selector of the text box to focus.
 */
export function focusLater(selector: string): void {
  if (typeof document === "undefined") {
    return;
  }
  queueMicrotask(() => {
    const box = document.querySelector(selector);
    if (box instanceof HTMLElement) {
      box.focus();
    }
  });
}
