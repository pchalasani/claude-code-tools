import type { JSX } from "solid-js";
import { formatTimestamp } from "./age";
import type { Turn } from "./document";
import { Markdown } from "./markdown-view";
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
        <time dateTime={props.turn.at}>{formatTimestamp(props.turn.at)}</time>
      </div>
      <div class="turn-text">
        <Markdown text={props.turn.text} />
      </div>
    </div>
  );
}
