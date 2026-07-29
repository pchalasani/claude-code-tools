import { For, type JSX } from "solid-js";
import { Dynamic } from "solid-js/web";

import { parseMarkdown, type Block } from "./markdown-blocks";
import type { Inline } from "./markdown";

/**
 * Paint markdown as elements, never as markup.
 *
 * Every string that reaches the page arrives here as the child of an element,
 * which is a text node and can never be anything else. Nothing in this file
 * writes ``innerHTML``, sets a style or a handler from the document, or builds
 * an attribute out of anything but a value the grammar has already vetted.
 * That is the whole safety argument, and it is structural rather than a list
 * of things to remember to escape.
 *
 * @param props - The untrusted text to paint.
 * @returns The painted blocks.
 */
export function Markdown(props: { text: string }): JSX.Element {
  return (
    <For each={parseMarkdown(props.text)}>{(block) => blockView(block)}</For>
  );
}

/**
 * Paint one block.
 *
 * @param block - A parsed block.
 * @returns The painted block.
 */
function blockView(block: Block): JSX.Element {
  if (block.kind === "code") {
    return <pre class="md-code-block">{block.text}</pre>;
  }
  if (block.kind === "heading") {
    // Never an h1 or an h2: the page's own title and update headlines own
    // those levels, and a heading written inside one item must not outrank
    // the structure it is sitting in.
    return (
      <Dynamic
        component={`h${Math.min(6, block.level + 3)}`}
        class="md-heading"
      >
        {inlines(block.content)}
      </Dynamic>
    );
  }
  if (block.kind === "list") {
    const items = <For each={block.items}>{(item) => <li>{inlines(item)}</li>}</For>;
    return block.ordered ? (
      <ol class="md-list" start={block.start}>{items}</ol>
    ) : (
      <ul class="md-list">{items}</ul>
    );
  }
  return <p class="md-paragraph">{inlines(block.content)}</p>;
}

/**
 * Paint a run of inline nodes.
 *
 * @param nodes - The parsed inline nodes.
 * @returns The painted nodes.
 */
function inlines(nodes: Inline[]): JSX.Element {
  return <For each={nodes}>{(node) => inlineView(node)}</For>;
}

/**
 * Paint one inline node.
 *
 * @param node - A parsed inline node.
 * @returns The painted node.
 */
function inlineView(node: Inline): JSX.Element {
  switch (node.kind) {
    case "code":
      return <code class="md-code">{node.text}</code>;
    case "strong":
      return <strong>{inlines(node.children)}</strong>;
    case "emphasis":
      return <em>{inlines(node.children)}</em>;
    case "link":
      // The scheme was checked when the node was built, so the only thing
      // that can be here is one of the schemes the grammar allows.
      return (
        <a class="md-link" href={node.href} rel="noreferrer noopener">
          {inlines(node.children)}
        </a>
      );
    default:
      return node.text;
  }
}
