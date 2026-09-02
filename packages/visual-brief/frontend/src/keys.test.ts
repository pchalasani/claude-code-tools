import { describe, expect, it } from "vitest";

import {
  isApplePlatform,
  isSendChord,
  isTypingTarget,
  resolveAction,
  type Action,
} from "./keys";

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
    ["j", "next-row"],
    ["k", "previous-row"],
    ["J", "next-lane"],
    ["K", "previous-lane"],
    [" ", "toggle"],
    ["Enter", "toggle"],
    ["c", "compose"],
    ["a", "compose-global"],
    ["n", "next-awaiting"],
    ["/", "search"],
    ["g", "top"],
    ["G", "bottom"],
    ["?", "help"],
    ["Escape", "close"],
    ["1", "digit-1"],
    ["2", "digit-2"],
    ["3", "digit-3"],
    ["4", "digit-4"],
    ["5", "digit-5"],
    ["6", "digit-6"],
    ["7", "digit-7"],
    ["8", "digit-8"],
    ["9", "digit-9"],
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
    expect(resolveAction({ key: "ArrowDown" })).toBe("next-row");
    expect(resolveAction({ key: "ArrowUp" })).toBe("previous-row");
  });

  it("ignores browser and system shortcuts", () => {
    expect(resolveAction({ key: "j", metaKey: true })).toBeNull();
    expect(resolveAction({ key: "G", ctrlKey: true })).toBeNull();
    expect(resolveAction({ key: "a", altKey: true })).toBeNull();
    expect(resolveAction({ key: "1", metaKey: true })).toBeNull();
    expect(resolveAction({ key: "2", ctrlKey: true })).toBeNull();
    expect(resolveAction({ key: "3", altKey: true })).toBeNull();
    expect(resolveAction({ key: "1", shiftKey: true })).toBeNull();
  });

  it("leaves Enter native on a row disclosure", () => {
    // A keyboard reader who tabs to an evidence fold opens that exact fold.
    const fold = element(
      "<button class='row-toggle' aria-expanded='false'>Evidence</button>",
    );

    expect(resolveAction({ key: "Enter", target: fold })).toBeNull();
  });

  it("leaves Enter native on an ordinary focused button", () => {
    document.body.innerHTML =
      "<button class='submit' type='submit'>Send</button>";
    const submit = document.querySelector(".submit");
    expect(resolveAction({ key: "Enter", target: submit })).toBeNull();
  });

  it("leaves Enter native on a focused prose link", () => {
    const link = element(
      "<a href='https://example.test'><strong>spec</strong></a>",
    );

    expect(resolveAction({
      key: "Enter",
      target: link.querySelector("strong"),
    })).toBeNull();
  });

  it("leaves Enter native on the latest-update attention button", () => {
    const attention = element(
      "<button class='meta-attention'>2 need attention in latest update</button>",
    );

    expect(resolveAction({ key: "Enter", target: attention })).toBeNull();
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
      "<button class='chat-button'>Chat</button>",
      "<button class='fold-head' aria-expanded='false'>Evidence</button>",
    ];

    for (const html of controls) {
      const control = element(html);
      expect(resolveAction({ key: " ", target: control })).toBe("toggle");
      expect(resolveAction({ key: "j", target: control })).toBe("next-row");
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
    const keys = [
      "j", "k", "J", "K", "a", "c", "n", "g", "G", "?", "/", " ",
      "1", "2", "3",
    ];
    for (const key of keys) {
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
    expect(resolveAction({ key: "j", target })).toBe("next-row");
  });
});

describe("the chord that sends", () => {
  it("is Command on an Apple keyboard and Control everywhere else", () => {
    expect(isSendChord({ key: "Enter", metaKey: true }, true)).toBe(true);
    expect(isSendChord({ key: "Enter", ctrlKey: true }, true)).toBe(false);
    expect(isSendChord({ key: "Enter", ctrlKey: true }, false)).toBe(true);
    expect(isSendChord({ key: "Enter", metaKey: true }, false)).toBe(false);
  });

  it("leaves plain Enter to make a paragraph", () => {
    expect(isSendChord({ key: "Enter" }, true)).toBe(false);
    expect(isSendChord({ key: "Enter" }, false)).toBe(false);
  });

  it("is not any other chorded key", () => {
    expect(isSendChord({ key: "j", metaKey: true }, true)).toBe(false);
    expect(isSendChord({ key: " ", ctrlKey: true }, false)).toBe(false);
  });

  it("reads Apple platforms out of what the browser reports", () => {
    expect(isApplePlatform("MacIntel")).toBe(true);
    expect(isApplePlatform("iPhone")).toBe(true);
    expect(isApplePlatform("Linux x86_64")).toBe(false);
    expect(isApplePlatform("Win32")).toBe(false);
  });
});

describe("shift with an arrow", () => {
  it("moves by lane, exactly as the shifted letters do", () => {
    expect(
      resolveAction({ key: "ArrowDown", shiftKey: true }),
    ).toBe("next-lane");
    expect(resolveAction({ key: "ArrowUp", shiftKey: true })).toBe(
      "previous-lane",
    );
  });

  it("leaves the unshifted arrows moving by item", () => {
    expect(resolveAction({ key: "ArrowDown" })).toBe("next-row");
    expect(resolveAction({ key: "ArrowUp", shiftKey: false })).toBe(
      "previous-row",
    );
  });

  it("does not invent a shifted meaning for keys that lack one", () => {
    expect(resolveAction({ key: "/", shiftKey: true })).toBe("search");
    expect(resolveAction({ key: "Escape", shiftKey: true })).toBe("close");
  });
});
