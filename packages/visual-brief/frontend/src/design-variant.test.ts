import { describe, expect, it } from "vitest";

import {
  DESIGN_VARIANTS,
  DEFAULT_DESIGN_VARIANT,
  activeDesignVariant,
  applyDesignVariant,
  designThemeForVariant,
  designVariantFamily,
  designVariantFromSearch,
  designVariantMode,
  designVariantForTheme,
  pairedDesignVariant,
  searchWithDesignVariant,
  setDesignVariant,
} from "./design-variant";

describe("design variant selection", () => {
  it.each(DESIGN_VARIANTS)("accepts %s", (variant) => {
    expect(designVariantFromSearch(`?design=${variant}`)).toBe(variant);
  });

  it("reads the design alongside unrelated query parameters", () => {
    expect(designVariantFromSearch("?run=brief&design=blue-margin")).toBe(
      "blue-margin",
    );
  });

  it("removes the design while preserving unrelated query parameters", () => {
    expect(searchWithDesignVariant("?run=brief&design=blue-margin", null))
      .toBe("?run=brief");
  });

  it.each(["", "?run=brief", "?design=", "?design=unknown"])(
    "rejects an absent, blank, or invalid selection in %s",
    (search) => {
      expect(designVariantFromSearch(search)).toBeNull();
    },
  );

  it("places a valid selection on the document element", () => {
    const page = document.implementation.createHTMLDocument("test");

    applyDesignVariant("?design=solarized-slate", page.documentElement);

    expect(page.documentElement.dataset.design).toBe("solarized-slate");
    expect(page.documentElement.dataset.designFamily).toBe("blue-margin");
    expect(page.documentElement.dataset.designMode).toBe("dark");
    expect(page.documentElement.dataset.designPaired).toBe("true");
  });

  it.each(["", "?design=", "?design=unknown"])(
    "uses the default selection for %s",
    (search) => {
      const page = document.implementation.createHTMLDocument("test");
      page.documentElement.dataset.design = "north-window";

      applyDesignVariant(search, page.documentElement);

      expect(page.documentElement.dataset.design).toBe(DEFAULT_DESIGN_VARIANT);
      expect(page.documentElement.dataset.designFamily).toBe("blue-margin");
      expect(page.documentElement.dataset.designMode).toBe("dark");
    },
  );

  it("maps each paired variant to its partner", () => {
    expect(pairedDesignVariant("blue-margin")).toBe("dusk-margin");
    expect(pairedDesignVariant("dusk-margin")).toBe("blue-margin");
    expect(pairedDesignVariant("solarized-paper")).toBe("solarized-slate");
    expect(pairedDesignVariant("solarized-slate")).toBe("solarized-paper");
    expect(pairedDesignVariant("catppuccin-latte")).toBe(
      "catppuccin-mocha",
    );
    expect(pairedDesignVariant("catppuccin-mocha")).toBe(
      "catppuccin-latte",
    );
    expect(pairedDesignVariant("north-window")).toBeNull();
  });

  it("gives the six paired variants one shared family attribute", () => {
    expect(designVariantFamily("blue-margin")).toBe("blue-margin");
    expect(designVariantFamily("dusk-margin")).toBe("blue-margin");
    expect(designVariantFamily("solarized-paper")).toBe("blue-margin");
    expect(designVariantFamily("solarized-slate")).toBe("blue-margin");
    expect(designVariantFamily("catppuccin-latte")).toBe("blue-margin");
    expect(designVariantFamily("catppuccin-mocha")).toBe("blue-margin");
  });

  it("groups paired variants into one human-facing theme", () => {
    expect(designThemeForVariant("catppuccin-latte").id).toBe("catppuccin");
    expect(designThemeForVariant("catppuccin-mocha").id).toBe("catppuccin");
    expect(designThemeForVariant("catppuccin-mocha").label).toBe(
      "Catppuccin Latte/Mocha",
    );
  });

  it("keeps the current mode when choosing another paired theme", () => {
    expect(designVariantForTheme("solarized", "dark")).toBe(
      "solarized-slate",
    );
    expect(designVariantForTheme("solarized", "light")).toBe(
      "solarized-paper",
    );
    expect(designVariantForTheme("north-window", "dark")).toBe(
      "north-window",
    );
  });

  it("can apply a validated design directly to the root", () => {
    const page = document.implementation.createHTMLDocument("test");

    setDesignVariant("catppuccin-latte", page.documentElement);

    expect(activeDesignVariant(page.documentElement)).toBe(
      "catppuccin-latte",
    );
    expect(page.documentElement.dataset.designFamily).toBe("blue-margin");
    expect(page.documentElement.dataset.designMode).toBe("light");
    expect(page.documentElement.dataset.designPaired).toBe("true");
    expect(designVariantMode("catppuccin-mocha")).toBe("dark");
  });

  it("updates a query string in place when switching variants", () => {
    expect(
      searchWithDesignVariant("?run=brief&design=blue-margin", "dusk-margin"),
    ).toBe("?run=brief&design=dusk-margin");
    expect(searchWithDesignVariant("?run=brief", "solarized-paper")).toBe(
      "?run=brief&design=solarized-paper",
    );
  });
});
