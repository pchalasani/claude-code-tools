import { describe, expect, it } from "vitest";

type FileSystem = {
  readFileSync(path: string, encoding: "utf8"): string;
};

type ProcessWithBuiltins = {
  cwd(): string;
  getBuiltinModule(name: "node:fs"): FileSystem;
};

const processWithBuiltins = (
  globalThis as typeof globalThis & { process: ProcessWithBuiltins }
).process;
const { readFileSync } = processWithBuiltins.getBuiltinModule("node:fs");

function stylesheet(name: string): string {
  return readFileSync(
    `${processWithBuiltins.cwd()}/src/styles/${name}`,
    "utf8",
  );
}

const base = stylesheet("base.css");
const chrome = stylesheet("chrome.css");
const currentState = stylesheet("current-state.css");
const prose = stylesheet("prose.css");
const rows = stylesheet("rows.css");
const tokens = stylesheet("tokens.css");

const componentStyles = [base, chrome, currentState, prose, rows].join("\n");

describe("brief typography and authored-color contract", () => {
  it("adjusts the entire type scale from one reversible token", () => {
    expect(tokens).toContain("--font-size-adjustment: -0.0625rem");
    for (const size of ["xs", "sm", "base", "md", "lg"]) {
      expect(tokens).toMatch(
        new RegExp(`--font-size-${size}: calc\\([^;]+`
          + `var\\(--font-size-adjustment\\)\\)`),
      );
    }
    expect(tokens).toMatch(
      /--font-size-xl: clamp\([^;]+var\(--font-size-adjustment\)/s,
    );
  });

  it("reserves the display face for the single main page title", () => {
    expect(componentStyles.match(/font-family: var\(--font-display\)/g)).toEqual([
      "font-family: var(--font-display)",
    ]);
    expect(base).toMatch(
      /\.brief-title\s*{[^}]*font-family: var\(--font-display\)/s,
    );
  });

  it("uses one monospace family across the named briefing cases", () => {
    expect(tokens).toContain("--font-brief: SFMono-Regular");
    expect(tokens).not.toContain("--font-reading:");
    expect(tokens).not.toContain("--font-utility:");
    expect(base).toMatch(/body\s*{[^}]*font-family: var\(--font-brief\)/s);
    expect(base).toMatch(/kbd\s*{[^}]*font-family: var\(--font-brief\)/s);
    expect(chrome).toMatch(
      /\.composer-label\s*{[^}]*font-family: var\(--font-brief\)/s,
    );
    expect(rows).toMatch(
      /\.turn-meta\s*{[^}]*font-family: var\(--font-brief\)/s,
    );
  });

  it("colors human-authored titles, turns, and draft text blue", () => {
    expect(tokens.match(/--color-human-text:/g)).toHaveLength(2);
    expect(rows).toMatch(
      /\.thread-title\s*{[^}]*color: var\(--color-human-text\)/s,
    );
    expect(rows).toMatch(
      /\.turn-human \.turn-text\s*{[^}]*color: var\(--color-human-text\)/s,
    );
    expect(chrome).toMatch(
      /\.composer-text\s*{[^}]*color: var\(--color-human-text\)/s,
    );
  });

  it("keeps agent item prose neutral and ordinary body prose normal", () => {
    expect(rows).toMatch(
      /\.glance\s*{[^}]*color: var\(--color-agent-text\)/s,
    );
    expect(rows).toMatch(
      /\.explanation\s*{[^}]*color: var\(--color-agent-text-muted\)/s,
    );
    expect(componentStyles).not.toMatch(
      /\.(?:glance|explanation)[^{]*{[^}]*color: var\(--color-human-text\)/s,
    );
    expect(prose).toMatch(/\.md-paragraph\s*{[^}]*font-weight: 400/s);
    expect(prose).toMatch(/\.md-list\s*{[^}]*font-weight: 400/s);
  });
});
