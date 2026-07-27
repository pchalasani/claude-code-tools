import { For, Show, type JSX } from "solid-js";

import {
  AgentWorking,
  ComposeBox,
  ComposeButton,
  PendingNotes,
} from "./compose-view";
import { sentFromThisPage } from "./composer";
import type { Thread } from "./document";
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
            <p class="turn-text">{turn.text}</p>
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
      <Show
        when={
          props.row.awaiting &&
          !sentFromThisPage(props.state.composer, props.row.id)
        }
      >
        <AgentWorking />
      </Show>
      <ComposeBox state={props.state} row={props.row} />
    </RowShell>
  );
}
