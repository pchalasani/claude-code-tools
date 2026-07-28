import { describe, expect, it } from "vitest";

import {
  MAX_POLL_INTERVAL_MS,
  announcePoll,
  decidePoll,
  nextDelay,
  onPollCycle,
} from "./reload";

const HERE = "a".repeat(64);
const THERE = "c".repeat(64);

describe("what one answer from the daemon means", () => {
  it("says the page has fallen behind when the generation moved on", () => {
    expect(decidePoll(HERE, THERE, false)).toBe("reload");
  });

  it("stays put when the generation is unchanged", () => {
    expect(decidePoll(HERE, HERE, false)).toBe("same");
  });

  it("backs off rather than acting when nothing answered", () => {
    expect(decidePoll(HERE, null, false)).toBe("retry");
  });

  it("acts on an answer it cannot read at all", () => {
    // The daemon was upgraded under an open tab and now says something this
    // page does not understand. Swallowing that is what stranded the tab.
    expect(decidePoll(HERE, "version 2 ok", false)).toBe("reload");
    expect(decidePoll(HERE, "", false)).toBe("reload");
  });

  it("acts on a page whose own generation the daemon cannot speak", () => {
    expect(decidePoll("", THERE, false)).toBe("reload");
  });

  it("acts on a mismatch once, and then stays readable", () => {
    expect(decidePoll(HERE, "version 2 ok", true)).toBe("same");
    expect(decidePoll("", THERE, true)).toBe("same");
  });
});

describe("backing off", () => {
  it("doubles the wait while nothing answers, up to a ceiling", () => {
    expect(nextDelay("retry", 5000, 5000)).toBe(10_000);
    expect(nextDelay("retry", 40_000, 5000)).toBe(MAX_POLL_INTERVAL_MS);
  });

  it("returns to the normal rhythm the moment anything answers", () => {
    expect(nextDelay("same", 40_000, 5000)).toBe(5000);
    expect(nextDelay("reload", 40_000, 5000)).toBe(5000);
    expect(nextDelay("patched", 40_000, 5000)).toBe(5000);
  });
});

describe("telling the page about polls", () => {
  it("delivers every cycle, and one bad listener spoils nothing", () => {
    const heard: string[] = [];
    const stopFirst = onPollCycle(() => {
      throw new Error("a listener that fails");
    });
    const stopSecond = onPollCycle((outcome) => heard.push(outcome));

    announcePoll("same");
    stopFirst();
    announcePoll("retry");
    stopSecond();
    announcePoll("reload");

    expect(heard).toEqual(["same", "retry"]);
  });
});
