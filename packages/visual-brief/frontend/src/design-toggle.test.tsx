import { describe, expect, it } from "vitest";

import {
  composeAt,
  mount,
  paintedCursor,
  press,
  typeInto,
  useHarness,
} from "../test/harness";
import { sampleBrief } from "../test/sample-brief";
import { setDesignVariant } from "./design-variant";

const BETA = "newest/changed/beta";

useHarness();

describe("paired design switching", () => {
  it("shows one theme entry for each light-dark pair", () => {
    setDesignVariant("catppuccin-mocha", document.documentElement);
    mount(sampleBrief());

    const picker = document.querySelector<HTMLSelectElement>(
      ".design-picker select",
    );
    expect(picker).not.toBeNull();
    expect(picker?.getAttribute("aria-label")).toBe("Theme");
    expect(picker?.value).toBe("catppuccin");
    expect(picker?.querySelectorAll("option")).toHaveLength(6);
    expect(picker?.textContent).toContain("Catppuccin Latte/Mocha");
    expect(picker?.textContent).not.toContain("Original");
    expect(document.querySelector(".design-pair-toggle")).not.toBeNull();
  });

  it("shows the masthead switch on paired variants", () => {
    setDesignVariant("blue-margin", document.documentElement);
    mount(sampleBrief());
    expect(document.querySelector(".design-pair-toggle")).not.toBeNull();
  });

  it("hides the masthead switch on unpaired variants", () => {
    setDesignVariant("dusk-ledger", document.documentElement);
    mount(sampleBrief());
    expect(document.querySelector(".design-picker select")).not.toBeNull();
    expect(document.querySelector(".design-pair-toggle")).toBeNull();
  });

  it("chooses a theme in place and preserves dark mode", () => {
    window.history.replaceState(null, "", "?run=brief");
    setDesignVariant("catppuccin-mocha", document.documentElement);
    mount(sampleBrief());
    const shell = document.querySelector(".shell");
    const picker = document.querySelector<HTMLSelectElement>(
      ".design-picker select",
    );
    if (shell === null || picker === null) {
      throw new Error("the design selector was not rendered");
    }

    picker.focus();
    expect(document.activeElement).toBe(picker);
    picker.value = "solarized";
    picker.dispatchEvent(new Event("change", { bubbles: true }));

    expect(document.documentElement.dataset.design).toBe(
      "solarized-slate",
    );
    expect(window.location.search).toBe(
      "?run=brief&design=solarized-slate",
    );
    expect(document.querySelector(".shell")).toBe(shell);
    expect(document.querySelector(".design-pair-toggle")).not.toBeNull();
    expect(document.activeElement).not.toBe(picker);

    const before = paintedCursor();
    press("j");
    expect(paintedCursor()).not.toBe(before);
  });

  it("switches variants in place and keeps the same live composer", () => {
    window.history.replaceState(
      null,
      "",
      "?run=brief&design=blue-margin",
    );
    setDesignVariant("blue-margin", document.documentElement);
    mount(sampleBrief());
    press("E");
    composeAt(BETA);
    typeInto(".composer textarea", "Keep this draft while the design flips.");

    const before = document.querySelector<HTMLTextAreaElement>(
      ".composer textarea",
    );
    const shell = document.querySelector(".shell");
    const toggle = document.querySelector<HTMLButtonElement>(
      ".design-pair-toggle",
    );
    if (before === null || shell === null || toggle === null) {
      throw new Error("the paired design controls were not rendered");
    }

    toggle.click();

    const after = document.querySelector<HTMLTextAreaElement>(
      ".composer textarea",
    );
    expect(document.documentElement.dataset.design).toBe("dusk-margin");
    expect(document.documentElement.dataset.designFamily).toBe("blue-margin");
    expect(document.documentElement.dataset.designMode).toBe("dark");
    expect(window.location.search).toBe("?run=brief&design=dusk-margin");
    expect(document.querySelector(".shell")).toBe(shell);
    expect(after).toBe(before);
    expect(after?.value).toBe("Keep this draft while the design flips.");
    expect(paintedCursor()).toBe(BETA);
  });

  it("updates the accessible label and title to the destination variant", () => {
    setDesignVariant("solarized-paper", document.documentElement);
    mount(sampleBrief());

    const toggle = document.querySelector<HTMLButtonElement>(
      ".design-pair-toggle",
    );
    if (toggle === null) {
      throw new Error("the paired design controls were not rendered");
    }
    expect(toggle.getAttribute("aria-label")).toContain("Solarized Slate");
    expect(toggle.title).toContain("Solarized Slate");

    toggle.click();

    expect(toggle.getAttribute("aria-label")).toContain("Solarized Paper");
    expect(toggle.title).toContain("Solarized Paper");
  });
});
