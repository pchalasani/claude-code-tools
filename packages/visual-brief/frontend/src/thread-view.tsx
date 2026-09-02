import { For, Show, type JSX } from "solid-js";
import {
  ComposeBox,
  ComposeButton,
  PendingNotes,
  WorkingSign,
} from "./compose-view";
import type { Thread } from "./document";
import type { Row } from "./outline";
import { NewAnswerChip, RowShell } from "./row-shell";
import type { BriefState } from "./state";
import { TurnView } from "./turn-view";
export function ThreadView(props: {
  state: BriefState;
  row: Row;
  thread: Thread;
  showWorking?: boolean;
}): JSX.Element {
  return (
    <RowShell
      state={props.state}
      row={props.row}
      head={
        <>
          <span class="thread-title">{props.row.label}</span>
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
        {(turn) => <TurnView turn={turn} />}
      </For>
      <PendingNotes state={props.state} row={props.row} />
      <Show when={props.showWorking !== false}>
        <WorkingSign state={props.state} row={props.row} />
      </Show>
      <ComposeBox state={props.state} row={props.row} />
    </RowShell>
  );
}
