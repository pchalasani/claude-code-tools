import { parseInline, type Inline } from "./markdown";

/** One block of a document, after the block grammar has been read. */
export type Block =
  | { kind: "paragraph"; content: Inline[] }
  | { kind: "heading"; level: number; content: Inline[] }
  | { kind: "code"; text: string }
  | { kind: "list"; ordered: boolean; start?: number; items: Inline[][] };

/** A line that opens or closes a fenced code block. */
const FENCE = /^ {0,3}(?:`{3,}|~{3,})/;

/** A heading line, with its level and its text. */
const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*)$/;

/** A bullet line, with its text. */
const BULLET = /^ {0,3}[-*+][ \t]+(.*)$/;

/** A numbered line, with its written number and text. */
const NUMBERED = /^ {0,3}(\d{1,9})[.)][ \t]+(.*)$/;

/**
 * Read text into the blocks the page will paint.
 *
 * @param text - Untrusted text written by an agent or a human.
 * @returns The blocks, in document order. Text that matches nothing in the
 *     grammar comes back as paragraphs of plain text.
 */
export function parseMarkdown(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let at = 0;
  while (at < lines.length) {
    const line = lines[at] ?? "";
    if (line.trim() === "") {
      at += 1;
      continue;
    }
    if (FENCE.test(line)) {
      at = readFence(lines, at, blocks);
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading !== null) {
      blocks.push({
        kind: "heading",
        level: (heading[1] ?? "#").length,
        content: parseInline(stripClosingHashes(heading[2] ?? "")),
      });
      at += 1;
      continue;
    }
    if (BULLET.test(line) || NUMBERED.test(line)) {
      at = readList(lines, at, blocks);
      continue;
    }
    at = readParagraph(lines, at, blocks);
  }
  return blocks;
}

/**
 * Read a fenced code block, ending at its fence or at the end of the text.
 *
 * @param lines - Every line of the document.
 * @param at - Index of the opening fence.
 * @param blocks - Blocks read so far, appended to.
 * @returns The index of the first line after the block.
 */
function readFence(lines: string[], at: number, blocks: Block[]): number {
  // A block closes only on its OWN fence: the same character, at least as
  // long, with nothing after it. Closing on any three backticks meant a
  // tilde-fenced block ended at a line of backticks inside it, and the rest
  // of the code was painted as prose.
  const opener = FENCE.exec(lines[at] ?? "")?.[0].trim() ?? "```";
  const char = opener[0] ?? "`";
  const width = opener.length;
  const body: string[] = [];
  let cursor = at + 1;
  while (
    cursor < lines.length
    && !closesFence(lines[cursor] ?? "", char, width)
  ) {
    body.push(lines[cursor] ?? "");
    cursor += 1;
  }
  blocks.push({ kind: "code", text: body.join("\n") });
  return cursor < lines.length ? cursor + 1 : cursor;
}

/**
 * Report whether one line closes a fence opened with a given marker.
 *
 * @param line - The line to judge.
 * @param char - The fence character the block was opened with.
 * @param width - How many of them opened it.
 * @returns True when this line is that block's closing fence.
 */
function closesFence(line: string, char: string, width: number): boolean {
  const indent = (/^ */.exec(line)?.[0] ?? "").length;
  if (indent !== line.length - line.trimStart().length || indent > 3) {
    // Four spaces makes it content, not a fence — which is exactly how a
    // fence character appears inside an indented code sample.
    return false;
  }
  const body = line.trim();
  if (body.length < width) {
    return false;
  }
  return [...body].every((one) => one === char);
}

/**
 * Read one run of list lines of the same kind.
 *
 * @param lines - Every line of the document.
 * @param at - Index of the first list line.
 * @param blocks - Blocks read so far, appended to.
 * @returns The index of the first line after the list.
 */
function readList(lines: string[], at: number, blocks: Block[]): number {
  const firstNumbered = NUMBERED.exec(lines[at] ?? "");
  const ordered = firstNumbered !== null;
  const pattern = ordered ? NUMBERED : BULLET;
  const items: Inline[][] = [];
  let cursor = at;
  while (cursor < lines.length) {
    const found = pattern.exec(lines[cursor] ?? "");
    if (found === null) {
      break;
    }
    items.push(parseInline(found[ordered ? 2 : 1] ?? ""));
    cursor += 1;
  }
  blocks.push({
    kind: "list",
    ordered,
    start: ordered ? Number(firstNumbered[1]) : undefined,
    items,
  });
  return cursor;
}

/**
 * Read one paragraph, up to a blank line or the start of another block.
 *
 * @param lines - Every line of the document.
 * @param at - Index of the paragraph's first line.
 * @param blocks - Blocks read so far, appended to.
 * @returns The index of the first line after the paragraph.
 */
function readParagraph(lines: string[], at: number, blocks: Block[]): number {
  const body: string[] = [];
  let cursor = at;
  while (cursor < lines.length) {
    const line = lines[cursor] ?? "";
    if (
      line.trim() === ""
      || FENCE.test(line)
      || HEADING.test(line)
      || BULLET.test(line)
      || NUMBERED.test(line)
    ) {
      break;
    }
    body.push(line);
    cursor += 1;
  }
  blocks.push({ kind: "paragraph", content: parseInline(body.join("\n")) });
  return cursor;
}

/**
 * Drop the optional closing hashes of an ATX heading.
 *
 * @param text - The heading's text.
 * @returns The text without its trailing hashes.
 */
function stripClosingHashes(text: string): string {
  return text.replace(/[ \t]+#+[ \t]*$/, "").trim();
}
