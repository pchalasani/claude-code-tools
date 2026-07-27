import type { Plugin } from "vite";

const ABSOLUTE_URL = /(https?):\/\//g;
const CLOSING_TAG = /<\/(script|style)/gi;
const COMMENT_OPEN = /<!--/g;

/**
 * Rewrite one JavaScript bundle so it is safe to inline verbatim.
 *
 * Three textual hazards survive bundling. Solid's runtime carries the MathML
 * namespace as a string literal, which trips the "no absolute URL anywhere in
 * the page" guarantee; any literal `</script` would end the inline script
 * element early; and a literal `<!--` puts the HTML parser into the escaped
 * script-data state, where the next `</script>` no longer closes the element.
 * Each offending character becomes its JavaScript escape sequence, which is
 * legal in string literals, template literals and regular expressions alike,
 * so the value the code sees is unchanged.
 *
 * @param code - Bundle source emitted by the build.
 * @returns The bundle with every hazard neutralized.
 */
export function neutralizeScript(code: string): string {
  return code
    .replace(ABSOLUTE_URL, "$1:\\u002F\\u002F")
    .replace(CLOSING_TAG, "<\\u002F$1")
    .replace(COMMENT_OPEN, "\\u003C!--");
}

/**
 * Vite plugin enforcing that the built bundles can be inlined into one page.
 *
 * JavaScript is rewritten; CSS is only checked, because a URL in CSS would be
 * a real external request rather than an inert string, and a stylesheet has no
 * escape that keeps a request local. Either way the build fails loudly rather
 * than shipping a page that reaches off the machine.
 *
 * @returns The configured plugin.
 */
export function inlineSafety(): Plugin {
  return {
    name: "visual-brief-inline-safety",
    apply: "build",
    generateBundle(_options, bundle) {
      for (const [fileName, output] of Object.entries(bundle)) {
        if (output.type === "chunk") {
          output.code = neutralizeScript(output.code);
          assertInlinable(fileName, output.code);
          compiles(fileName, output.code);
          continue;
        }
        if (typeof output.source !== "string") {
          throw new Error(`${fileName}: binary assets are not inlinable`);
        }
        assertInlinable(fileName, output.source);
      }
    },
  };
}

/**
 * Fail the build when text still carries an inlining hazard.
 *
 * @param fileName - Emitted file the text belongs to.
 * @param text - Emitted text.
 */
function assertInlinable(fileName: string, text: string): void {
  const url = text.match(/https?:\/\//);
  if (url !== null) {
    throw new Error(
      `${fileName}: absolute URL ${url[0]} would leave the page; ` +
        "the generated page must make zero external requests",
    );
  }
  const tag = text.match(/<\/(script|style)/i);
  if (tag !== null) {
    throw new Error(
      `${fileName}: literal ${tag[0]} would terminate the inline element`,
    );
  }
  if (text.includes("<!--")) {
    throw new Error(
      `${fileName}: literal <!-- would escape the inline element's parser`,
    );
  }
}

/**
 * Fail the build when the rewritten JavaScript no longer parses.
 *
 * @param fileName - Emitted file the code belongs to.
 * @param code - Rewritten bundle source.
 */
function compiles(fileName: string, code: string): void {
  try {
    new Function(code);
  } catch (error) {
    throw new Error(`${fileName}: rewritten bundle does not parse: ${error}`);
  }
}
