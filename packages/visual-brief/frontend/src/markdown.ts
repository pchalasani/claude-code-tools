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

/**
 * The only link schemes a brief may paint as a link.
 *
 * Everything else — ``javascript:``, ``data:``, ``vbscript:``, a scheme
 * nobody has invented yet — falls through to being painted as the literal
 * characters the author wrote. The page never has to decide whether an
 * unusual scheme is safe, because it never builds a link out of one.
 */
const ALLOWED_SCHEME = /^(?:https?|mailto):[^\s]/i;

/** Where the next inline mark might begin. */
const MARK = /`+|\*\*|[*_]|\[/;

/** A link, matched only at the position its bracket was found at. */
const LINK = /^\[([^\][]*)\]\(([^\s()]*)\)/;

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
