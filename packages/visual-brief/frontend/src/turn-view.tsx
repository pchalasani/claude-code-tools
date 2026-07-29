import type { JSX } from "solid-js";

import type { Turn } from "./document";
import { Markdown } from "./markdown-view";

/**
 * One human or agent turn, whether delivered or still awaiting its fold.
 *
 * A submitted human message uses this same rendering before and after it
 * appears in the document. The pending marker is data for reconciliation and
 * diagnostics, not a second visual treatment of the human's words.
 *
 * @param props - The turn and optional pending-state readings.
 * @returns The rendered turn.
 */
export function TurnView(props: {
  turn: Turn;
  pending?: boolean;
  stalled?: boolean;
}): JSX.Element {
  return (
    <div
      class={`turn turn-${props.turn.author}`}
      data-pending={props.pending ? "true" : undefined}
      data-stalled={props.pending ? String(props.stalled === true) : undefined}
    >
      <div class="turn-meta">
        <span class="turn-author">{props.turn.author}</span>
        <time>{props.turn.at}</time>
      </div>
      <div class="turn-text">
        <Markdown text={props.turn.text} />
      </div>
    </div>
  );
}
