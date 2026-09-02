import { For, Show, type JSX } from "solid-js";
import type { SuggestedReply } from "./document";
import { SEND_CHORD_LABEL, isSendChord } from "./keys";
import type { Row } from "./outline";
import type { BriefState } from "./state";
import { TurnView } from "./turn-view";
import { isWorking } from "./working";
const CHAT_LABEL = "Chat";
export function ComposeButton(props: {
  state: BriefState;
  row: Row;
  label: string;
}): JSX.Element {
  const open = () => props.state.composer.isOpenAt(props.row.id);
  return (
    <button
      type="button"
      class="chat-button"
      aria-expanded={open()}
      aria-label={props.label}
      onClick={() => props.state.composeAt(props.row)}
    >
      {CHAT_LABEL}
    </button>
  );
}
export function ComposeBox(props: {
  state: BriefState;
  row: Row;
  label?: string;
  placeholder?: string;
}): JSX.Element {
  const composer = props.state.composer;
  const isReply = () => composer.target()?.parentId !== undefined;
  return (
    <Show when={composer.isOpenAt(props.row.id)}>
      <form
        class="composer"
        data-anchor-id={composer.target()?.anchorId}
        data-parent-id={composer.target()?.parentId}
        onSubmit={(event) => {
          event.preventDefault();
          void composer.submit();
        }}
      >
        <label class="composer-label" for="brief-compose">
          {props.label
            ?? (isReply()
              ? "Chat in this conversation"
              : "Chat about this section")}
        </label>
        <textarea
          id="brief-compose"
          class="composer-text"
          required
          rows="3"
          placeholder={props.placeholder ?? "What would you like to say?"}
          value={composer.text()}
          onInput={(event) => composer.setText(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (!isSendChord(event)) {
              return;
            }
            event.preventDefault();
            void composer.submit();
          }}
        />
        <Show when={composer.draftWarning()}>
          {(warning) => (
            <p class="draft-warning" role="alert">
              {warning()}
            </p>
          )}
        </Show>
        <div class="composer-actions">
          <button class="submit" type="submit" disabled={composer.sending()}>
            {composer.sending() ? "Sending…" : "Send"}
          </button>
          <span class="chord-hint">
            <kbd>{SEND_CHORD_LABEL}</kbd>
          </span>
          <button
            type="button"
            class="quiet"
            onClick={() => composer.discard()}
          >
            Discard draft
          </button>
          <span class="status" aria-live="polite">
            {composer.status()}
          </span>
        </div>
      </form>
    </Show>
  );
}
export function AgentWorking(): JSX.Element {
  return (
    <p class="working" role="status">
      <span class="working-mark" aria-hidden="true">
        ●
      </span>
      <span class="working-text-wrap">
        <span class="working-text">agent is working</span>
        <span class="working-text-wave" aria-hidden="true">
          agent is working
        </span>
      </span>
    </p>
  );
}
export function WorkingSign(props: {
  state: BriefState;
  row: Row;
}): JSX.Element {
  return (
    <Show when={isWorking(props.state, props.row)}>
      <AgentWorking />
    </Show>
  );
}
export function SubmissionStalled(): JSX.Element {
  return (
    <p class="stalled" role="status">
      submitted — refresh if this persists
    </p>
  );
}
export function PendingNotes(props: {
  state: BriefState;
  row: Row;
}): JSX.Element {
  return (
    <For each={props.state.composer.pendingAt(props.row.id)}>
      {(note) => (
        <>
          <TurnView
            turn={{ author: "human", text: note.text, at: note.at }}
            pending
            stalled={note.stalled}
          />
          <Show when={note.stalled}>
            <SubmissionStalled />
          </Show>
        </>
      )}
    </For>
  );
}
export function SignalBar(props: {
  state: BriefState;
  row: Row;
  suggestions: SuggestedReply[];
}): JSX.Element {
  const numbers = () => props.suggestions.flatMap((_, index) => {
    const number = props.state.suggestionShortcutNumber(props.row.id, index);
    return number === null ? [] : [number];
  });
  return (
    <div class="signals">
      <div class="signals-heading">
        <span class="signals-label">Tell the agent</span>
        <span class="signals-hint">
          ({signalShortcutHint(numbers())})
        </span>
      </div>
      <div class="signal-choices">
        <For each={props.suggestions}>
          {(suggestion, index) => {
            const number = () => props.state.suggestionShortcutNumber(
              props.row.id,
              index(),
            );
            return (
              <button
                type="button"
                class="signal"
                data-suggestion={suggestion.message}
                aria-keyshortcuts={number() === null
                  ? undefined
                  : String(number())}
                aria-pressed={
                  props.state.composer.selectedSignalAt(props.row.id)
                    === suggestion.message
                }
                onClick={() => {
                  props.state.nav.select(props.row.id, { scroll: false });
                  void props.state.composer.sendSignal(
                    props.row.id,
                    props.row.anchorId,
                    suggestion,
                  );
                }}
              >
                <Show when={number()}>
                  {(shown) => <kbd aria-hidden="true">{shown()}</kbd>}
                </Show>
                <span>{suggestion.label}</span>
              </button>
            );
          }}
        </For>
      </div>
      <span class="status" aria-live="polite">
        {props.state.composer.signalStatus(props.row.id)}
      </span>
    </div>
  );
}

export function signalShortcutHint(numbers: readonly number[]): string {
  if (numbers.length < 1) {
    return "c to chat";
  }
  const first = numbers[0];
  const last = numbers.at(-1);
  const range = numbers.length === 1 ? String(first) : `${first}–${last}`;
  return `type ${range} to select, c to chat`;
}
