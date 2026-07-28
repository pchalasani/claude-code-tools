import { For, type JSX } from "solid-js";

import {
  ComposeBox,
  ComposeButton,
  PendingNotes,
  WorkingSign,
} from "./compose-view";
import type { Thread } from "./document";
import { Markdown } from "./markdown-view";
import type { Row } from "./outline";
import { AwaitingChip, NewAnswerChip, RowShell } from "./row-shell";
import type { BriefState } from "./state";

/**
 * One conversation, oldest turn first, with its reply slot underneath.
 *
 * @param props - The thread, its row and the page state.
 * @returns The rendered conversation.
 */
export function ThreadView(props: {
  state: BriefState;
  row: Row;
  thread: Thread;
}): JSX.Element {
  return (
    <RowShell
      state={props.state}
      row={props.row}
      head={
        <>
          <span class="thread-title">{props.row.label}</span>
          <AwaitingChip when={props.row.awaiting} />
          <NewAnswerChip when={props.state.nav.isFresh(props.row.id)} />
          <span class="row-count">
            {props.thread.turns.length}{" "}
            {props.thread.turns.length === 1 ? "turn" : "turns"}
          </span>
        </>
      }
      actions={
        <ComposeButton
          state={props.state}
          row={props.row}
          label={`Chat in ${props.row.label}`}
        />
      }
    >
      <For each={props.thread.turns}>
        {(turn) => (
          <div class={`turn turn-${turn.author}`}>
            <div class="turn-meta">
              <span class="turn-author">{turn.author}</span>
              <time>{turn.at}</time>
            </div>
            {/*
              Both authors' words go through the same renderer, which builds
              elements out of a closed grammar and can produce no markup at
              all. There is therefore no unescaped path for either of them to
              travel down, and one path is easier to keep safe than two.
            */}
            <div class="turn-text">
              <Markdown text={turn.text} />
            </div>
          </div>
        )}
      </For>
      <PendingNotes state={props.state} row={props.row} />
      {/*
        The document, not this page load, is what keeps the sign up. A human
        who asks a question and then watches the agent republish something
        else must not see the reassurance vanish: the conversation is still
        awaiting, so it still says so, reload after reload.
      */}
      <WorkingSign state={props.state} row={props.row} />
      <ComposeBox state={props.state} row={props.row} />
    </RowShell>
  );
}
