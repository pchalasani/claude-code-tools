import { describe, expect, it } from "vitest";

import { neutralizeScript } from "./inline-safety";

describe("neutralizeScript", () => {
  it("removes absolute URLs without changing the runtime value", () => {
    const source = 'const ns = "http://www.w3.org/1998/Math/MathML";';

    const rewritten = neutralizeScript(source);

    expect(rewritten).not.toMatch(/https?:\/\//);
    expect(new Function(`${rewritten} return ns;`)()).toBe(
      "http://www.w3.org/1998/Math/MathML",
    );
  });

  it("keeps a literal closing script tag from ending the element", () => {
    const closing = "</" + "script>";
    const source = `const end = ${JSON.stringify(closing)};`;

    const rewritten = neutralizeScript(source);

    expect(rewritten.toLowerCase()).not.toContain("</script");
    expect(new Function(`${rewritten} return end;`)()).toBe(closing);
  });

  it("keeps a comment opener from escaping the element's parser", () => {
    const opener = "<!--" + "<script>";
    const source = `const trap = ${JSON.stringify(opener)};`;

    const rewritten = neutralizeScript(source);

    expect(rewritten).not.toContain("<!--");
    expect(new Function(`${rewritten} return trap;`)()).toBe(opener);
  });

  it("rewrites secure URLs too", () => {
    const rewritten = neutralizeScript('const u = "https://example.invalid";');

    expect(rewritten).not.toMatch(/https?:\/\//);
    expect(new Function(`${rewritten} return u;`)()).toBe(
      "https://example.invalid",
    );
  });
});
