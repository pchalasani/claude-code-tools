import { describe, expect, it } from "vitest";

import {
  answerStates,
  conversationState,
  freshAnswers,
  rememberSeen,
} from "./freshness";
import { outline } from "./outline";
import { sampleBrief } from "../test/sample-brief";

const ROWS = outline(sampleBrief());

describe("describing the conversations on the page", () => {
  it("names only the conversation rows, and how each stands", () => {
    expect(answerStates(ROWS)).toEqual({
      "newest/changed/alpha#q-answered": conversationState(2, false),
      "newest/changed/beta#q-open": conversationState(1, true),
    });
  });

  it("changes a conversation's state when a turn is added to it", () => {
    expect(conversationState(1, true)).not.toBe(conversationState(2, false));
    expect(conversationState(2, false)).not.toBe(conversationState(3, false));
  });
});

describe("what arrived since the last look", () => {
  const answered = "newest/changed/alpha#q-answered";
  const open = "newest/changed/beta#q-open";
  const states = answerStates(ROWS);

  it("marks nothing at all on a first look", () => {
    expect(freshAnswers(states, null)).toEqual(new Set());
  });

  it("marks an answer that landed while the human was away", () => {
    const before = { [answered]: conversationState(1, true), [open]: states[open] };

    expect(freshAnswers(states, before as Record<string, string>)).toEqual(
      new Set([answered]),
    );
  });

  it("marks a conversation the fold gave a new identity", () => {
    // A pending question is folded into a saved conversation under a different
    // id, which is the ordinary way an answer arrives.
    const before = { "newest/changed/alpha#q-pending-abc": conversationState(1, true) };

    expect(freshAnswers(states, before)).toEqual(new Set([answered]));
  });

  it("never marks a question that is still waiting for its answer", () => {
    expect(freshAnswers(states, {})).toEqual(new Set([answered]));
  });

  it("marks nothing when the page has not moved since the last look", () => {
    expect(freshAnswers(states, states)).toEqual(new Set());
  });
});

describe("what is carried into the next reload", () => {
  const answered = "newest/changed/alpha#q-answered";
  const states = answerStates(ROWS);

  it("remembers everything the human can be said to have seen", () => {
    expect(rememberSeen(states, new Set())).toEqual(states);
  });

  it("leaves out what is still marked new, so the mark survives a reload", () => {
    const carried = rememberSeen(states, new Set([answered]));

    expect(carried[answered]).toBeUndefined();
    expect(freshAnswers(states, carried)).toEqual(new Set([answered]));
  });
});
