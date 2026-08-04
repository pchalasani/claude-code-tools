/**
 * One small document the front-end tests navigate.
 *
 * It is deliberately shaped like the real thing: two updates so that newest
 * first matters, two lanes in the newest so lane movement has somewhere to
 * go, a thread that is answered and a thread that is not. It lives beside the
 * tests rather than in ``src`` because nothing ships it.
 */

import type { BriefDocument, Item, Lane, Update } from "../src/document";

export const SAMPLE_SUGGESTIONS = [
  {
    label: "Show proof",
    message: "Show me the concrete evidence behind this claim.",
  },
  {
    label: "Explain tradeoff",
    message: "Explain the tradeoff behind this choice in plain language.",
  },
  {
    label: "What next?",
    message: "Tell me what you recommend doing next about this item.",
  },
];

/**
 * Build an independent copy of the sample document.
 *
 * @returns A document the tests may mutate freely.
 */
export function sampleBrief(): BriefDocument {
  return {
    title: "Sample brief",
    summary: "A small document with two updates.",
    updates: [
      {
        id: "older",
        timestamp: "2026-07-24T10:00:00Z",
        headline: "The older update",
        summary: "What happened first.",
        lanes: [
          {
            id: "history",
            name: "History",
            items: [
              {
                id: "one",
                glance: "The first thing",
                explanation: "A parser was replaced.",
                trust: "verified-by-me",
                forensics: ["exit status 0"],
              },
            ],
          },
        ],
      },
      {
        id: "newest",
        timestamp: "2026-07-25T10:00:00Z",
        headline: "The newest update",
        summary: "What happened since.",
        lanes: [
          {
            id: "changed",
            name: "What changed",
            open: true,
            items: [
              {
                id: "alpha",
                glance: "Alpha moved forward",
                explanation: "The reader agrees with the reference parser.",
                trust: "verified-by-me",
                suggestions: SAMPLE_SUGGESTIONS.map((suggestion) => ({
                  ...suggestion,
                })),
                tables: [
                  {
                    caption: "Parity",
                    columns: ["case", "verdict"],
                    rows: [["nested", "WRONG before"]],
                  },
                ],
                questions: [
                  {
                    id: "q-answered",
                    anchor: {
                      kind: "element",
                      path: "newest/changed/alpha",
                    },
                    turns: [
                      {
                        author: "human",
                        text: "Is alpha checked?",
                        at: "2026-07-25T11:00:00Z",
                      },
                      {
                        author: "agent",
                        text: "Yes, against the reference.",
                        at: "2026-07-25T11:01:00Z",
                      },
                    ],
                  },
                ],
              },
              {
                id: "beta",
                glance: "Beta is still open",
                explanation: "Four edge cases remain.",
                trust: "unverified",
                questions: [
                  {
                    id: "q-open",
                    anchor: {
                      kind: "element",
                      path: "newest/changed/beta",
                    },
                    turns: [
                      {
                        author: "human",
                        text: "Which edge cases?",
                        at: "2026-07-25T12:00:00Z",
                      },
                    ],
                  },
                ],
              },
            ],
          },
          {
            id: "next",
            name: "What is next",
            items: [
              {
                id: "gamma",
                glance: "Gamma is queued",
                explanation: "Waiting on the parity fix.",
                trust: "reported-by-agent",
              },
            ],
          },
        ],
      },
    ],
  };
}

/**
 * Find one lane of the sample document.
 *
 * @param brief - The document to look in.
 * @param updateId - Id of the update holding it.
 * @param laneId - Id of the lane.
 * @returns The lane.
 */
export function laneOf(
  brief: BriefDocument,
  updateId: string,
  laneId: string,
): Lane {
  const update = brief.updates.find((one: Update) => one.id === updateId);
  const lane = update?.lanes.find((one: Lane) => one.id === laneId);
  if (lane === undefined) {
    throw new Error(`the sample document lost the lane ${laneId}`);
  }
  return lane;
}

/**
 * Find one item of the sample document.
 *
 * @param brief - The document to look in.
 * @param path - The item's row id.
 * @returns The item.
 */
export function itemOf(brief: BriefDocument, path: string): Item {
  const [updateId = "", laneId = "", itemId = ""] = path.split("/");
  const item = laneOf(brief, updateId, laneId).items.find(
    (one: Item) => one.id === itemId,
  );
  if (item === undefined) {
    throw new Error(`the sample document lost the item ${path}`);
  }
  return item;
}
