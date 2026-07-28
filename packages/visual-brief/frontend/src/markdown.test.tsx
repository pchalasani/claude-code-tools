import { render } from "solid-js/web";
import { afterEach, describe, expect, it } from "vitest";

import { parseMarkdown } from "./markdown";
import { Markdown } from "./markdown-view";

let dispose: (() => void) | null = null;
let host: HTMLElement | null = null;

afterEach(() => {
  dispose?.();
  host?.remove();
  dispose = null;
  host = null;
});

/**
 * Paint one piece of text the way the page paints it.
 *
 * @param text - The text to render.
 * @returns The element it was painted into.
 */
function paint(text: string): HTMLElement {
  const container = document.createElement("div");
  document.body.append(container);
  host = container;
  dispose = render(() => <Markdown text={text} />, container);
  return container;
}

describe("reading markdown", () => {
  it("keeps ordinary prose as one paragraph, line breaks and all", () => {
    const painted = paint("First line\nsecond line");

    expect(painted.querySelectorAll("p.md-paragraph")).toHaveLength(1);
    expect(painted.textContent).toBe("First line\nsecond line");
  });

  it("reads emphasis, strong emphasis and inline code", () => {
    const painted = paint("A *little*, a **lot**, and `code`.");

    expect(painted.querySelector("em")?.textContent).toBe("little");
    expect(painted.querySelector("strong")?.textContent).toBe("lot");
    expect(painted.querySelector("code.md-code")?.textContent).toBe("code");
  });

  it("leaves an unclosed mark as the characters it is", () => {
    const painted = paint("2 * 3 and a stray ` backtick");

    expect(painted.querySelector("em")).toBeNull();
    expect(painted.querySelector("code")).toBeNull();
    expect(painted.textContent).toBe("2 * 3 and a stray ` backtick");
  });

  it("leaves an underscore inside a word alone", () => {
    const painted = paint("read_served_page is a function");

    expect(painted.querySelector("em")).toBeNull();
    expect(painted.textContent).toBe("read_served_page is a function");
  });

  it("reads bullet and numbered lists", () => {
    const painted = paint("- one\n- two\n\n1. first\n2. second");

    expect(
      [...painted.querySelectorAll("ul.md-list li")].map((li) => li.textContent),
    ).toEqual(["one", "two"]);
    expect(
      [...painted.querySelectorAll("ol.md-list li")].map((li) => li.textContent),
    ).toEqual(["first", "second"]);
  });

  it("reads headings, and never outranks the page's own", () => {
    const painted = paint("# Top\n\n### Deeper");

    expect(painted.querySelector("h4")?.textContent).toBe("Top");
    expect(painted.querySelector("h6")?.textContent).toBe("Deeper");
    expect(painted.querySelectorAll("h1, h2, h3")).toHaveLength(0);
  });

  it("reads a fenced block, and reads nothing inside it", () => {
    const painted = paint("before\n\n```py\nx = **not bold**\n```\n\nafter");

    expect(painted.querySelector("pre.md-code-block")?.textContent).toBe(
      "x = **not bold**",
    );
    expect(painted.querySelector("strong")).toBeNull();
    expect(painted.querySelectorAll("p.md-paragraph")).toHaveLength(2);
  });

  it("closes a fence the author forgot to close", () => {
    const blocks = parseMarkdown("```\nstill code");

    expect(blocks).toEqual([{ kind: "code", text: "still code" }]);
  });
});

describe("what a link is allowed to be", () => {
  it("paints a link whose scheme is on the allowlist", () => {
    const painted = paint("see [the spec](https:example.test/spec)");
    const link = painted.querySelector("a.md-link");

    expect(link?.getAttribute("href")).toBe("https:example.test/spec");
    expect(link?.textContent).toBe("the spec");
    expect(link?.getAttribute("rel")).toBe("noreferrer noopener");
  });

  it("refuses every other scheme, and shows the characters instead", () => {
    for (const href of [
      "javascript:alert(1)",
      "data:text/html,<script>alert(1)</script>",
      "vbscript:msgbox",
      "JaVaScRiPt:alert(1)",
    ]) {
      const painted = paint(`[click me](${href})`);

      expect(painted.querySelector("a")).toBeNull();
      expect(painted.textContent).toBe(`[click me](${href})`);
      dispose?.();
      host?.remove();
    }
  });
});

describe("text that is trying to become markup", () => {
  // The three cases the contract names, planted verbatim.
  const IMAGE = '<img src=x onerror=alert(1)>';
  const LINK = "[click](javascript:alert(1))";
  const FENCED = "```\n<img src=x onerror=alert(1)>\n```";

  it("never builds an element out of markup that was written as text", () => {
    const painted = paint(`${IMAGE}\n\n${LINK}\n\n${FENCED}`);

    expect(painted.querySelectorAll("img")).toHaveLength(0);
    expect(painted.querySelectorAll("script")).toHaveLength(0);
    expect(painted.querySelectorAll("a")).toHaveLength(0);
    expect(painted.querySelector("[onerror]")).toBeNull();
    // Every character the author wrote is still there — as characters.
    expect(painted.textContent).toContain(IMAGE);
    expect(painted.textContent).toContain(LINK);
    expect(painted.querySelector("pre.md-code-block")?.textContent).toBe(
      IMAGE,
    );
  });

  it("puts the markup in a text node, which cannot be anything else", () => {
    const painted = paint(IMAGE);
    const paragraph = painted.querySelector("p.md-paragraph");

    expect(paragraph?.childNodes).toHaveLength(1);
    expect(paragraph?.firstChild?.nodeType).toBe(Node.TEXT_NODE);
    expect(paragraph?.innerHTML).toBe(
      "&lt;img src=x onerror=alert(1)&gt;",
    );
  });
});

describe("characters an engineer actually writes", () => {
  it("leaves file globs and arithmetic exactly as written", () => {
    // Before the flanking rule, both asterisks vanished and the wrong span
    // was italicised — the page silently altering what the agent said.
    for (const line of [
      "I checked *.py and *.ts files",
      "3 * 4 and 5 * 6 checks",
    ]) {
      const [block] = parseMarkdown(line);
      expect(block?.kind).toBe("paragraph");
      const text = block?.kind === "paragraph"
        ? block.content.map((n) => (n.kind === "text" ? n.text : "?")).join("")
        : "";
      expect(text).toBe(line);
    }
  });

  it("still emphasises when the marks hug their words", () => {
    const [block] = parseMarkdown("this is *emphatic* indeed");
    const kinds = block?.kind === "paragraph"
      ? block.content.map((n) => n.kind)
      : [];
    expect(kinds).toContain("emphasis");
  });

  it("closes a fence only on its own marker", () => {
    const blocks = parseMarkdown(
      "~~~~\n<tag>\n```not-a-close\nstill code\n~~~~",
    );
    expect(blocks).toHaveLength(1);
    expect(blocks[0]?.kind).toBe("code");
    const text = blocks[0]?.kind === "code" ? blocks[0].text : "";
    expect(text).toContain("still code");
    expect(text).toContain("```not-a-close");
  });
});

describe("pathological and indented input", () => {
  it("stays fast on a line of many non-closing marks", () => {
    // Rescanning the tail per rejected opener made this quadratic: about a
    // second at eight thousand marks, enough to freeze the tab.
    const line = "a *".repeat(8000);
    const started = Date.now();
    parseMarkdown(line);
    expect(Date.now() - started).toBeLessThan(250);
  });

  it("does not end a block at an indented fence inside it", () => {
    const blocks = parseMarkdown(
      "```\ncode\n    ```\nstill code\n```",
    );
    expect(blocks).toHaveLength(1);
    const text = blocks[0]?.kind === "code" ? blocks[0].text : "";
    expect(text).toContain("still code");
    expect(text).toContain("    ```");
  });

  it("closes on a fence carrying only trailing spaces", () => {
    const blocks = parseMarkdown("```\ncode\n```   \nafter");
    expect(blocks.map((b) => b.kind)).toEqual(["code", "paragraph"]);
  });
});
