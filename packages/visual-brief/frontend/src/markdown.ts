/**
 * Markdown, parsed into a tree of things this page is willing to paint.
 *
 * Agents write markdown by habit, so the page has to read it. The text it
 * reads is untrusted — the human's own words come back through the daemon,
 * and the agent's words are only as careful as the agent — so the rule here
 * is not "sanitize the markup", it is that no markup is ever produced.
 *
 * This module turns text into a small, closed set of nodes. There is no node
 * for raw HTML, so there is no way for any input to become one; anything the
 * grammar does not recognize stays a text node and is painted as the
 * characters it is. The renderer next door builds elements from these nodes,
 * so escaping is not a step that can be forgotten: a text node cannot be
 * markup in the first place.
 *
 * The allowlist is deliberately small: emphasis, strong, inline code, fenced
 * code, lists, headings, and links whose scheme is one of a named few.
 */

/** One piece of a line, after the inline grammar has been read. */
export type Inline =
  | { kind: "text"; text: string }
  | { kind: "code"; text: string }
  | { kind: "strong"; children: Inline[] }
  | { kind: "emphasis"; children: Inline[] }
  | { kind: "link"; href: string; children: Inline[] };

/** One block of a document, after the block grammar has been read. */
export type Block =
  | { kind: "paragraph"; content: Inline[] }
  | { kind: "heading"; level: number; content: Inline[] }
  | { kind: "code"; text: string }
  | { kind: "list"; ordered: boolean; items: Inline[][] };

/**
 * The only link schemes a brief may paint as a link.
 *
 * Everything else — ``javascript:``, ``data:``, ``vbscript:``, a scheme
 * nobody has invented yet — falls through to being painted as the literal
 * characters the author wrote. The page never has to decide whether an
 * unusual scheme is safe, because it never builds a link out of one.
 */
const ALLOWED_SCHEME = /^(?:https?|mailto):[^\s]/i;

/** A line that opens or closes a fenced code block. */
const FENCE = /^ {0,3}(?:`{3,}|~{3,})/;

/** A heading line, with its level and its text. */
const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*)$/;

/** A bullet line, with its text. */
const BULLET = /^ {0,3}[-*+][ \t]+(.*)$/;

/** A numbered line, with its text. */
const NUMBERED = /^ {0,3}\d{1,9}[.)][ \t]+(.*)$/;

/** Where the next inline mark might begin. */
const MARK = /`+|\*\*|[*_]|\[/;

/** A link, matched only at the position its bracket was found at. */
const LINK = /^\[([^\][]*)\]\(([^\s()]*)\)/;

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
 * Read one line into the inline nodes the page will paint.
 *
 * @param text - One line, or several lines of one paragraph.
 * @returns The inline nodes, in order.
 */
export function parseInline(text: string): Inline[] {
  const nodes: Inline[] = [];
  let rest = text;
  let plain = "";

  /** Flush whatever plain text has accumulated into a text node. */
  const flush = (): void => {
    if (plain !== "") {
      nodes.push({ kind: "text", text: plain });
      plain = "";
    }
  };

  // Remembered EXHAUSTION, and nothing else. When a search has actually
  // walked the remainder of the line and found no closer for an emphasis
  // mark, no later opener of that mark can find one either — the candidates
  // are a subset. Without this, "a *a *a *…" rescanned the tail per asterisk
  // and cost seconds at a few thousand marks.
  //
  // Two things this must NOT remember, both learned by getting them wrong.
  // A locally invalid opener — one followed by a space — says nothing about
  // the rest of the line: "* bad then *kept*" must still emphasise "kept".
  // And a link is not monotonic at all: one unclosed "[" must not silence
  // every later link on the line.
  const exhausted = new Set<string>();

  while (rest !== "") {
    const found = MARK.exec(rest);
    if (found === null || found.index === undefined) {
      plain += rest;
      break;
    }
    plain += rest.slice(0, found.index);
    const mark = found[0];
    const after = rest.slice(found.index);
    // An underscore inside a word is part of the word: snake_case names are
    // written in briefs constantly, and turning half of one into emphasis
    // would silently change what the agent said.
    const inWord = mark === "_" && /\w$/.test(plain);
    const read = inWord || exhausted.has(mark)
      ? null
      : readMark(mark, after);
    if (read === null) {
      // Only an emphasis mark that was searched to the end of the line, and
      // whose own opener was valid, has proved anything about what follows.
      if (!inWord && isEmphasis(mark) && opensValidly(mark, after)) {
        exhausted.add(mark);
      }
      plain += mark;
      rest = after.slice(mark.length);
      continue;
    }
    flush();
    nodes.push(read.node);
    rest = after.slice(read.length);
  }
  flush();
  return nodes;
}

/**
 * Report whether a mark is one whose closer search runs to end of line.
 *
 * @param mark - The mark just read.
 * @returns True for emphasis marks, whose failure is monotonic.
 */
function isEmphasis(mark: string): boolean {
  return mark === "*" || mark === "**" || mark === "_";
}

/**
 * Report whether a mark could open emphasis at all, ignoring any closer.
 *
 * A mark followed by whitespace is not an opener, so its failure says
 * nothing about the rest of the line.
 *
 * @param mark - The opening mark.
 * @param text - The text beginning at that mark.
 * @returns True when the opener itself is well formed.
 */
function opensValidly(mark: string, text: string): boolean {
  const body = text.slice(mark.length);
  return body !== "" && !/^\s/.test(body);
}

/** One inline node and how much of the text it consumed. */
interface Read {
  /** The node that was read. */
  node: Inline;
  /** How many characters it accounted for. */
  length: number;
}

/**
 * Read whichever inline form begins at the front of the text.
 *
 * @param mark - The mark that was found.
 * @param text - The text beginning at that mark.
 * @returns The node and its length, or null when the mark opens nothing.
 */
function readMark(mark: string, text: string): Read | null {
  if (mark.startsWith("`")) {
    return readCode(mark, text);
  }
  if (mark === "[") {
    return readLink(text);
  }
  return readWrapped(mark, text);
}

/**
 * Read a code span, which nothing inside it may reopen.
 *
 * @param fence - The run of backticks that opened it.
 * @param text - The text beginning at that run.
 * @returns The code node and its length, or null when it never closes.
 */
function readCode(fence: string, text: string): Read | null {
  const body = text.slice(fence.length);
  const closing = body.indexOf(fence);
  if (closing < 0) {
    return null;
  }
  const inner = body.slice(0, closing);
  return {
    node: { kind: "code", text: unpad(inner) },
    length: fence.length * 2 + closing,
  };
}

/**
 * Read emphasis or strong emphasis.
 *
 * @param mark - The opening mark, ``**``, ``*`` or ``_``.
 * @param text - The text beginning at that mark.
 * @returns The node and its length, or null when it never closes.
 */
function readWrapped(mark: string, text: string): Read | null {
  const body = text.slice(mark.length);
  // CommonMark's flanking rule, for the same reason the underscore has a
  // word-boundary rule: an opener followed by a space, or a closer preceded
  // by one, is not emphasis. Without this, "I checked *.py and *.ts files"
  // and "3 * 4 and 5 * 6" both lose their asterisks and italicise the wrong
  // span — file globs and arithmetic are ordinary content in a brief, and
  // the page must not alter what the agent said.
  if (body === "" || /^\s/.test(body)) {
    return null;
  }
  let closing = -1;
  let from = 0;
  while (true) {
    const found = body.indexOf(mark, from);
    if (found <= 0) {
      break;
    }
    if (!/\s$/.test(body.slice(0, found))) {
      closing = found;
      break;
    }
    from = found + mark.length;
  }
  if (closing <= 0) {
    return null;
  }
  const inner = body.slice(0, closing);
  return {
    node: {
      kind: mark === "**" ? "strong" : "emphasis",
      children: parseInline(inner),
    },
    length: mark.length * 2 + closing,
  };
}

/**
 * Read a link, and only one whose scheme is on the allowlist.
 *
 * @param text - The text beginning at the opening bracket.
 * @returns The link node and its length, or null when this is not a link the
 *     page will paint — in which case the characters stay characters.
 */
function readLink(text: string): Read | null {
  const found = LINK.exec(text);
  if (found === null) {
    return null;
  }
  const href = (found[2] ?? "").trim();
  const label = found[1] ?? "";
  if (!ALLOWED_SCHEME.test(href) || label.trim() === "") {
    return null;
  }
  return {
    node: { kind: "link", href, children: parseInline(label) },
    length: found[0].length,
  };
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
  while (cursor < lines.length && !closesFence(lines[cursor] ?? "", char, width)) {
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
  const ordered = NUMBERED.test(lines[at] ?? "");
  const pattern = ordered ? NUMBERED : BULLET;
  const items: Inline[][] = [];
  let cursor = at;
  while (cursor < lines.length) {
    const found = pattern.exec(lines[cursor] ?? "");
    if (found === null) {
      break;
    }
    items.push(parseInline(found[1] ?? ""));
    cursor += 1;
  }
  blocks.push({ kind: "list", ordered, items });
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

/**
 * Drop the one space a code span may use to hold a backtick away from a fence.
 *
 * @param text - The span's raw content.
 * @returns The content as it should be painted.
 */
function unpad(text: string): string {
  if (text.length > 2 && text.startsWith(" ") && text.endsWith(" ")) {
    return text.slice(1, -1);
  }
  return text;
}
