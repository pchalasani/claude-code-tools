import { Show, type JSX } from "solid-js";
import type { Row } from "./outline";
import { pointerIsDriving } from "./pointer";
import type { BriefState } from "./state";
export function RowShell(props: {
  state: BriefState;
  row: Row;
  head: JSX.Element;
  separateHead?: boolean;
  actions?: JSX.Element;
  children?: JSX.Element;
}): JSX.Element {
  const isCursor = () => props.state.nav.isCursor(props.row.id);
  const isOpen = () => props.state.nav.isOpen(props.row.id);
  const bodyId = () => `body:${props.row.id}`;
  const waitingId = () => `waiting:${props.row.id}`;
  const hint = () => props.state.hints.labelFor(props.row.id);
  const ordinal = () => props.state.nav.ordinal(props.row.id);
  const waiting = (): "direct" | "contained" | undefined => {
    if (
      (props.row.kind === "thread" && props.row.awaiting)
      || props.state.composer.pendingAt(props.row.id).length > 0
    ) {
      return "direct";
    }
    return props.row.awaiting || props.state.pending.within(props.row.id)
      ? "contained"
      : undefined;
  };
  const waitingDescription = (): string | undefined => {
    if (waiting() === "direct") {
      return "Waiting for an agent answer.";
    }
    return waiting() === "contained"
      ? "Contains a conversation waiting for an agent answer."
      : undefined;
  };
  return (
    <article
      class={`row row-${props.row.kind}`}
      data-row-id={props.row.id}
      data-row-kind={props.row.kind}
      data-cursor={isCursor() ? "true" : "false"}
      data-open={isOpen() ? "true" : "false"}
      data-awaiting={props.row.awaiting ? "true" : "false"}
      data-waiting={waiting()}
      data-fresh={props.state.nav.isFresh(props.row.id) ? "true" : "false"}
      onPointerMove={(event) => {
        if (event.pointerType !== "mouse" || !pointerIsDriving()) {
          return;
        }
        event.stopPropagation();
        props.state.nav.pointAt(props.row.id);
      }}
    >
      <div
        class="row-head"
        style={{
          "view-transition-name": isCursor()
            ? "brief-cursor"
            : undefined,
        }}
      >
        <Show when={hint()}>
          {(label) => (
            <HintLabel label={label()} typed={props.state.hints.typed()} />
          )}
        </Show>
        <button
          type="button"
          class="row-toggle"
          aria-expanded={isOpen()}
          aria-controls={bodyId()}
          aria-label={
            props.separateHead ? `Toggle ${props.row.label}` : undefined
          }
          aria-describedby={waiting() === undefined ? undefined : waitingId()}
          onClick={() => props.state.nav.toggle(props.row.id)}
        >
          <span class="row-fold" aria-hidden="true">
            {isOpen() ? "▾" : "▸"}
          </span>
          <Show when={!props.separateHead}>{props.head}</Show>
        </button>
        <Show when={props.separateHead}>
          <div class="row-static-head">{props.head}</div>
        </Show>
        <Show when={waitingDescription()}>
          {(description) => (
            <span id={waitingId()} class="visually-hidden">
              {description()}
            </span>
          )}
        </Show>
        <Show when={ordinal() !== null}>
          <span class="ordinal" aria-hidden="true">
            {ordinal()}
          </span>
        </Show>
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
function HintLabel(props: { label: string; typed: string }): JSX.Element {
  const done = () =>
    props.label.startsWith(props.typed) ? props.typed : "";
  return (
    <span class="hint" data-hint={props.label} aria-hidden="true">
      <b class="hint-done">{done()}</b>
      {props.label.slice(done().length)}
    </span>
  );
}
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
export function NewAnswerChip(props: { when: boolean }): JSX.Element {
  return (
    <Show when={props.when}>
      <span class="chip chip-new">
        <span class="chip-mark" aria-hidden="true">
          ★
        </span>
        New answer
      </span>
    </Show>
  );
}
