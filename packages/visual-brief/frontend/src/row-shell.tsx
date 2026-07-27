import { Show, type JSX } from "solid-js";

import type { Row } from "./outline";
import { pointerIsDriving } from "./pointer";
import type { BriefState } from "./state";
import { CURSOR_TRANSITION_NAME } from "./transitions";

/**
 * The chrome every navigable row shares.
 *
 * One element carries the row's identity, whether it is the cursor and
 * whether it is expanded. The cursor's paint hangs off ``data-cursor`` on the
 * head, so the thing the application believes and the thing the human sees
 * are the same attribute.
 *
 * @param props - The row, its state, its head line and its body.
 * @returns The rendered row.
 */
export function RowShell(props: {
  state: BriefState;
  row: Row;
  /** Content of the clickable head line. */
  head: JSX.Element;
  /** Controls shown at the right of the head line. */
  actions?: JSX.Element;
  /** Content revealed when the row is expanded. */
  children?: JSX.Element;
}): JSX.Element {
  const isCursor = () => props.state.nav.isCursor(props.row.id);
  const isOpen = () => props.state.nav.isOpen(props.row.id);
  const bodyId = () => `body:${props.row.id}`;
  return (
    <article
      class={`row row-${props.row.kind}`}
      data-row-id={props.row.id}
      data-row-kind={props.row.kind}
      data-cursor={isCursor() ? "true" : "false"}
      data-open={isOpen() ? "true" : "false"}
      data-awaiting={props.row.awaiting ? "true" : "false"}
      onPointerOver={(event) => {
        // Hover IS selection: the row under the pointer becomes the cursor, so
        // whatever you press next acts on the row you are looking at. Guarded
        // on pointerType so a stationary mouse cannot steal the cursor back
        // while the keyboard is driving, and on the row id so entering a child
        // element does not re-fire.
        if (event.pointerType !== "mouse" || !pointerIsDriving()) {
          // Not a real hover: the keyboard moved the cursor, the page scrolled,
          // and this row slid under a stationary mouse. Believing it would move
          // the cursor a second time for one key press.
          return;
        }
        // Rows nest, so the event bubbles through every ancestor row and the
        // outermost one would win. Stop it here: the innermost row under the
        // pointer is the one the reader is looking at.
        event.stopPropagation();
        props.state.nav.pointAt(props.row.id);
      }}
    >
      <div
        class="row-head"
        style={{
          "view-transition-name": isCursor()
            ? CURSOR_TRANSITION_NAME
            : undefined,
        }}
      >
        <button
          type="button"
          class="row-toggle"
          aria-expanded={isOpen()}
          aria-controls={bodyId()}
          onClick={() => props.state.nav.toggle(props.row.id)}
        >
          <span class="row-fold" aria-hidden="true">
            {isOpen() ? "▾" : "▸"}
          </span>
          {props.head}
        </button>
        <Show when={props.actions !== undefined}>
          <span class="row-actions">{props.actions}</span>
        </Show>
      </div>
      <Show when={isOpen()}>
        <div class="row-body" id={bodyId()}>
          {props.children}
        </div>
      </Show>
    </article>
  );
}

/**
 * Render one row only while the search leaves it on the page.
 *
 * @param props - The row id to resolve and what to render for it.
 * @returns The rendered row, or nothing when it is filtered out.
 */
export function VisibleRow(props: {
  state: BriefState;
  id: string;
  children: (row: Row) => JSX.Element;
}): JSX.Element {
  const resolved = () =>
    props.state.nav.isVisible(props.id)
      ? props.state.nav.row(props.id)
      : undefined;
  return <Show when={resolved()}>{(row) => props.children(row())}</Show>;
}

/**
 * The badge marking anything still waiting for an answer.
 *
 * @param props - Whether to show the badge.
 * @returns The badge, or nothing.
 */
export function AwaitingChip(props: { when: boolean }): JSX.Element {
  return (
    <Show when={props.when}>
      <span class="chip chip-awaiting">
        <span class="chip-mark" aria-hidden="true">
          ●
        </span>
        Awaiting answer
      </span>
    </Show>
  );
}
