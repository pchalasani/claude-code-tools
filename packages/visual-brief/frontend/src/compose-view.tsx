import { For, Show, type JSX } from "solid-js";

import { SIGNALS } from "./composer";
import { SEND_CHORD_LABEL, isSendChord } from "./keys";
import type { Row } from "./outline";
import type { BriefState } from "./state";

/** The word this page uses for writing to the agent, wherever it writes it. */
const CHAT_LABEL = "Chat";

/**
 * The affordance that points the chat box at one row.
 *
 * One word covers every direction the conversation can go: the human may be
 * asking, answering something the agent asked, or steering it somewhere else.
 * "Ask" only covered the first.
 *
 * @param props - The row to write against and its state.
 * @returns The button.
 */
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

/**
 * The one chat box, wherever it is currently pointed.
 *
 * It renders only under the row it targets, so the human writes where they
 * are looking, and it refuses to send twice while a request is in flight.
 *
 * @param props - The row this slot belongs to and its state.
 * @returns The chat box when it is pointed here, otherwise nothing.
 */
export function ComposeBox(props: {
  state: BriefState;
  row: Row;
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
          {isReply() ? "Chat in this conversation" : "Chat about this section"}
        </label>
        <textarea
          id="brief-compose"
          class="composer-text"
          required
          rows="3"
          placeholder="What would you like to say?"
          value={composer.text()}
          onInput={(event) => composer.setText(event.currentTarget.value)}
          onKeyDown={(event) => {
            // Plain Enter still makes a paragraph. The chord is the one way
            // to send without reaching for the mouse.
            if (!isSendChord(event)) {
              return;
            }
            event.preventDefault();
            void composer.submit();
          }}
        />
        <div class="composer-actions">
          <button class="submit" type="submit" disabled={composer.sending()}>
            {composer.sending() ? "Sending…" : "Send"}
          </button>
          <span class="chord-hint">
            <kbd>{SEND_CHORD_LABEL}</kbd>
          </span>
          <button type="button" class="quiet" onClick={() => composer.close()}>
            <kbd>Esc</kbd> Cancel
          </button>
          <span class="status" aria-live="polite">
            {composer.status()}
          </span>
        </div>
      </form>
    </Show>
  );
}

/**
 * The sign that the other end of the conversation is busy.
 *
 * It sits where the answer will appear, and it moves, because the whole
 * question a waiting human has is whether anything is happening at all. The
 * wording never names a product: whichever agent is behind this page, it is
 * "agent". Where motion is unwelcome the same words stay put.
 *
 * @returns The indicator.
 */
export function AgentWorking(): JSX.Element {
  return (
    <p class="working" role="status">
      <span class="working-text">agent is working</span>
    </p>
  );
}

/**
 * Everything this page has sent from one row and not yet seen answered.
 *
 * @param props - The row and its state.
 * @returns The pending notes, each with the sign that an answer is coming.
 */
export function PendingNotes(props: {
  state: BriefState;
  row: Row;
}): JSX.Element {
  const composer = props.state.composer;
  return (
    <>
      <Show when={composer.sendingAt(props.row.id)}>
        <AgentWorking />
      </Show>
      <For each={composer.pendingAt(props.row.id)}>
        {(note) => (
          <>
            <p class="pending">
              <span class="chip chip-awaiting">
                <span class="chip-mark" aria-hidden="true">
                  ●
                </span>
                Sent
              </span>
              You asked: {note.text} — awaiting an answer
            </p>
            <AgentWorking />
          </>
        )}
      </For>
    </>
  );
}

/**
 * The fixed-vocabulary feedback an item accepts in one click.
 *
 * @param props - The item row and its state.
 * @returns The signal buttons and their status line.
 */
export function SignalBar(props: {
  state: BriefState;
  row: Row;
}): JSX.Element {
  return (
    <div class="signals">
      <span class="signals-label">Tell the agent</span>
      <For each={SIGNALS}>
        {([signal, label]) => (
          <button
            type="button"
            class="signal"
            data-signal={signal}
            onClick={() =>
              void props.state.composer.sendSignal(
                props.row.id,
                props.row.anchorId,
                signal,
              )
            }
          >
            {label}
          </button>
        )}
      </For>
      <span class="status" aria-live="polite">
        {props.state.composer.signalStatus(props.row.id)}
      </span>
    </div>
  );
}
