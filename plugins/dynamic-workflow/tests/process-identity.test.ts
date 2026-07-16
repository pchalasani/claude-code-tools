import { describe, expect, it } from "vitest";

import {
  isDeadPosixProcessState,
  isPidRunning,
  processIdentityMatches,
  processStartIdentity,
} from "../src/utils.js";

describe("process identity", () => {
  it("observes the current Darwin or Linux process without ps", () => {
    if (process.platform !== "darwin" && process.platform !== "linux") {
      return;
    }

    expect(isPidRunning(process.pid)).toBe(true);
  });

  it.each(["Z", "X", "x"])(
    "treats POSIX process state %s as dead",
    (state) => {
      expect(isDeadPosixProcessState(state)).toBe(true);
      expect(isDeadPosixProcessState(`${state}+`)).toBe(true);
    },
  );

  it.each(["", "R", "S", "D", "T", "t", "I"])(
    "does not treat POSIX process state %s as dead",
    (state) => {
      expect(isDeadPosixProcessState(state)).toBe(false);
    },
  );

  it("binds Linux kernel start ticks to the current boot", () => {
    if (process.platform !== "linux") {
      return;
    }

    const identity = processStartIdentity(process.pid);

    expect(identity).toMatch(
      /^linux:[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}:\d+$/,
    );
    if (identity === undefined) {
      throw new Error("Expected a Linux process identity");
    }
    const startTicks = identity.split(":")[2];
    expect(processIdentityMatches(process.pid, identity)).toBe(true);
    expect(
      processIdentityMatches(
        process.pid,
        `linux:00000000-0000-0000-0000-000000000000:${startTicks}`,
      ),
    ).toBe(false);
    expect(processIdentityMatches(process.pid, "linux:0")).toBe(false);
  });

  it("uses the microsecond Darwin process start time", () => {
    if (process.platform !== "darwin") {
      return;
    }

    const identity = processStartIdentity(process.pid);

    expect(identity).toMatch(/^darwin:\d+:\d+$/);
    expect(processIdentityMatches(process.pid, identity)).toBe(true);
    expect(processIdentityMatches(process.pid, "darwin:0:0")).toBe(false);
  });

  it("rejects invalid process IDs", () => {
    expect(processStartIdentity(0)).toBeUndefined();
    expect(processStartIdentity(-1)).toBeUndefined();
    expect(processStartIdentity(2_147_483_647)).toBeUndefined();
  });
});
