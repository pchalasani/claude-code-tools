import { For, Show, type JSX } from "solid-js";

import { SIGNALS } from "./composer";
import type { Row } from "./outline";
import type { BriefState } from "./state";

/**
 * The affordance that points composition at one row.
 *
 * @param props - The row to compose against and its state.
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
      class="ask-button"
      aria-expanded={open()}
      aria-label={props.label}
      onClick={() => props.state.composeAt(props.row)}
    >
      {props.row.kind === "thread" ? "Reply" : "Ask"}
    </button>
  );
}

/**
 * The one composer, wherever it is currently pointed.
 *
 * It renders only under the row it targets, so the human writes where they
 * are looking, and it refuses to send twice while a request is in flight.
 *
 * @param props - The row this slot belongs to and its state.
 * @returns The composer when it is pointed here, otherwise nothing.
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
          {isReply() ? "Reply to this conversation" : "Ask about this section"}
        </label>
        <textarea
          id="brief-compose"
          class="composer-text"
          required
          rows="3"
          placeholder="What would you like clarified?"
          value={composer.text()}
          onInput={(event) => composer.setText(event.currentTarget.value)}
        />
        <div class="composer-actions">
          <button class="submit" type="submit" disabled={composer.sending()}>
            {composer.sending() ? "Sending…" : "Send"}
          </button>
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
 * Everything this page has sent from one row and not yet seen answered.
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
        <p class="pending">
          <span class="chip chip-awaiting">
            <span class="chip-mark" aria-hidden="true">
              ●
            </span>
            Sent
          </span>
          You asked: {note.text} — awaiting an answer
        </p>
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
