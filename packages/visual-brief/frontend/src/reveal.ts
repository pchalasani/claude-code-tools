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
