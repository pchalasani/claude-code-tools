import { describe, expect, it } from "vitest";

import { isTypingTarget, resolveAction, type Action } from "./keys";

/**
 * Build an element the way the browser would deliver a key press to it.
 *
 * @param html - Markup of the element.
 * @returns The element.
 */
function element(html: string): Element {
  const host = document.createElement("div");
  host.innerHTML = html;
  const child = host.firstElementChild;
  if (child === null) {
    throw new Error("no element in markup");
  }
  document.body.append(host);
  return child;
}

describe("every binding", () => {
  const expected: [string, Action][] = [
    ["j", "next-item"],
    ["k", "previous-item"],
    ["J", "next-lane"],
    ["K", "previous-lane"],
    [" ", "toggle"],
    ["a", "compose"],
    ["n", "next-awaiting"],
    ["/", "search"],
    ["g", "top"],
    ["G", "bottom"],
    ["?", "help"],
    ["Escape", "close"],
  ];

  it.each(expected)("resolves %s", (key, action) => {
    expect(resolveAction({ key })).toBe(action);
  });

  it("keeps the shifted keys alive alongside their lowercase twins", () => {
    for (const [lower, upper] of [
      ["j", "J"],
      ["k", "K"],
      ["g", "G"],
    ]) {
      const shifted = resolveAction({ key: upper as string });
      expect(shifted).not.toBeNull();
      expect(shifted).not.toBe(resolveAction({ key: lower as string }));
    }
    expect(resolveAction({ key: "?" })).toBe("help");
  });

  it("ignores unbound keys", () => {
    expect(resolveAction({ key: "q" })).toBeNull();
    expect(resolveAction({ key: "PageDown" })).toBeNull();
  });

  it("accepts arrow keys as an alias for j and k", () => {
    // A browser extension such as Vimium binds j/k globally and wins, so there
    // has to be a route to the cursor that no extension is likely to claim.
    expect(resolveAction({ key: "ArrowDown" })).toBe("next-item");
    expect(resolveAction({ key: "ArrowUp" })).toBe("previous-item");
  });

  it("ignores browser and system shortcuts", () => {
    expect(resolveAction({ key: "j", metaKey: true })).toBeNull();
    expect(resolveAction({ key: "G", ctrlKey: true })).toBeNull();
    expect(resolveAction({ key: "a", altKey: true })).toBeNull();
  });

  it("gives Enter to the cursor row, not to whatever holds browser focus", () => {
    // Enter used to fall through to the browser so a tabbed-to control could
    // open natively. In practice the browser focuses a button when it is
    // clicked, and the cursor is deliberately not the browser's focus, so
    // Enter opened whichever row the mouse last touched instead of the row the
    // reader is on. Enter is now a synonym for Space: it acts on the cursor.
    const fold = element(
      "<button class='fold-head' aria-expanded='false'>Evidence</button>",
    );

    expect(resolveAction({ key: "Enter", target: fold })).toBe("toggle");
    expect(resolveAction({ key: "Enter" })).toBe("toggle");
  });

  it("still leaves Enter alone while the human is typing", () => {
    const box = element("<textarea></textarea>");

    expect(resolveAction({ key: "Enter", target: box })).toBeNull();
  });

  it("keeps Space for the page even when a control holds focus", () => {
    const controls = [
      "<button class='row-toggle' aria-expanded='false'>Item</button>",
      "<button class='key-control' data-action='next-item'>j</button>",
      "<button class='map-lane'>Lane</button>",
      "<button class='ask-button'>Ask</button>",
      "<button class='fold-head' aria-expanded='false'>Evidence</button>",
    ];

    for (const html of controls) {
      const control = element(html);
      expect(resolveAction({ key: " ", target: control })).toBe("toggle");
      expect(resolveAction({ key: "j", target: control })).toBe("next-item");
    }
  });

  it("still folds the cursor row with Space from the page itself", () => {
    const row = element("<article><p>text</p></article>");

    expect(resolveAction({ key: " ", target: row.firstElementChild })).toBe(
      "toggle",
    );
    expect(resolveAction({ key: " " })).toBe("toggle");
  });
});

describe("while the human is typing", () => {
  const boxes = [
    "<textarea></textarea>",
    "<input type='search'>",
    "<div contenteditable='true'><span>inside</span></div>",
  ];

  it.each(boxes)("stays inert inside %s", (html) => {
    const box = element(html);
    const target = box.querySelector("span") ?? box;

    expect(isTypingTarget(target)).toBe(true);
    for (const key of ["j", "k", "J", "K", "a", "n", "g", "G", "?", "/", " "]) {
      expect(resolveAction({ key, target })).toBeNull();
    }
  });

  it("still leaves the text box on Escape", () => {
    const box = element("<textarea></textarea>");

    expect(resolveAction({ key: "Escape", target: box })).toBe("close");
  });

  it("does not treat ordinary content as a text box", () => {
    const row = element("<article contenteditable='false'><p>text</p></article>");
    const target = row.querySelector("p");

    expect(isTypingTarget(target)).toBe(false);
    expect(resolveAction({ key: "j", target })).toBe("next-item");
  });
});
