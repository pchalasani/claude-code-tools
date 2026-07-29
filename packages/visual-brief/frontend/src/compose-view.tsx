import { For, Show, type JSX } from "solid-js";

import { SIGNALS } from "./composer";
import { SEND_CHORD_LABEL, isSendChord } from "./keys";
import type { Row } from "./outline";
import type { BriefState } from "./state";
import { isWorking } from "./working";

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

/**
 * The sign that the other end of the conversation is busy.
 *
 * It sits where the answer will appear, and something in it moves, because
 * the whole question a waiting human has is whether anything is happening at
 * all. The words themselves never move and are never painted through
 * anything: they are one solid colour at every instant, so the sign cannot
 * fade out and come back while it is still true. The motion is carried by a
 * mark beside them, which is also the only part that stands still where
 * motion is unwelcome. The wording never names a product: whichever agent is
 * behind this page, it is "agent".
 *
 * @returns The indicator.
 */
export function AgentWorking(): JSX.Element {
  return (
    <p class="working" role="status">
      <span class="working-mark" aria-hidden="true">
        ●
      </span>
      <span class="working-text">agent is working</span>
    </p>
  );
}

/**
 * The one place any row says the agent is working.
 *
 * Every row that can be waiting on the agent renders exactly this, and the
 * decision behind it is made in one function, so two sources of the same
 * truth can neither stack up into two signs nor cancel each other into none.
 *
 * @param props - The row and its state.
 * @returns The sign, or nothing.
 */
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

/**
 * The sign a message that never arrived degrades to.
 *
 * Several polls without the human's words appearing adds a diagnostic beside
 * the continuous working sign. It must not replace that sign: doing so opened
 * the visible gap between the accepted submission and the eventual thread.
 *
 * @returns The still statement.
 */
export function SubmissionStalled(): JSX.Element {
  return (
    <p class="stalled" role="status">
      submitted — refresh if this persists
    </p>
  );
}

/**
 * Everything this page has sent from one row and not yet seen answered.
 *
 * The notes say what was sent; whether the agent is working is said once, by
 * the sign, and never from here. A note that has waited too long adds the
 * smaller diagnostic beside itself rather than replacing anything.
 *
 * @param props - The row and its state.
 * @returns The pending notes.
 */
export function PendingNotes(props: {
  state: BriefState;
  row: Row;
}): JSX.Element {
  return (
    <For each={props.state.composer.pendingAt(props.row.id)}>
      {(note) => (
        <>
          <p class="pending" data-stalled={note.stalled ? "true" : "false"}>
            <span class="pending-label">Sent</span> You asked: {note.text} —
            awaiting an answer
          </p>
          <Show when={note.stalled}>
            <SubmissionStalled />
          </Show>
        </>
      )}
    </For>
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
