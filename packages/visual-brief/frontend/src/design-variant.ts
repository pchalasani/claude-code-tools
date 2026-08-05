export const DESIGN_VARIANTS = [
  "north-window",
  "blue-margin",
  "dusk-margin",
  "solarized-paper",
  "solarized-slate",
  "catppuccin-latte",
  "catppuccin-mocha",
  "dusk-ledger",
  "night-ledger",
] as const;

export type DesignVariant = (typeof DESIGN_VARIANTS)[number];
export type DesignMode = "light" | "dark";

export const DEFAULT_DESIGN_VARIANT: DesignVariant = "catppuccin-mocha";

type DesignTheme = {
  id: string;
  label: string;
  light: DesignVariant | null;
  dark: DesignVariant | null;
};

export const DESIGN_THEMES = [
  {
    id: "catppuccin",
    label: "Catppuccin Latte/Mocha",
    light: "catppuccin-latte",
    dark: "catppuccin-mocha",
  },
  {
    id: "margin",
    label: "Blue Margin/Dusk Margin",
    light: "blue-margin",
    dark: "dusk-margin",
  },
  {
    id: "solarized",
    label: "Solarized Paper/Slate",
    light: "solarized-paper",
    dark: "solarized-slate",
  },
  {
    id: "north-window",
    label: "North Window",
    light: "north-window",
    dark: null,
  },
  {
    id: "dusk-ledger",
    label: "Dusk Ledger",
    light: null,
    dark: "dusk-ledger",
  },
  {
    id: "night-ledger",
    label: "Night Ledger",
    light: null,
    dark: "night-ledger",
  },
] as const satisfies readonly DesignTheme[];

export type DesignThemeId = (typeof DESIGN_THEMES)[number]["id"];

type DesignVariantMeta = {
  family: string;
  label: string;
  mode: DesignMode;
  pairedWith: DesignVariant | null;
};

const DESIGN_VARIANT_META: Record<DesignVariant, DesignVariantMeta> = {
  "north-window": {
    family: "north-window",
    label: "North Window",
    mode: "light",
    pairedWith: null,
  },
  "blue-margin": {
    family: "blue-margin",
    label: "Blue Margin",
    mode: "light",
    pairedWith: "dusk-margin",
  },
  "dusk-margin": {
    family: "blue-margin",
    label: "Dusk Margin",
    mode: "dark",
    pairedWith: "blue-margin",
  },
  "solarized-paper": {
    family: "blue-margin",
    label: "Solarized Paper",
    mode: "light",
    pairedWith: "solarized-slate",
  },
  "solarized-slate": {
    family: "blue-margin",
    label: "Solarized Slate",
    mode: "dark",
    pairedWith: "solarized-paper",
  },
  "catppuccin-latte": {
    family: "blue-margin",
    label: "Catppuccin Latte",
    mode: "light",
    pairedWith: "catppuccin-mocha",
  },
  "catppuccin-mocha": {
    family: "blue-margin",
    label: "Catppuccin Mocha",
    mode: "dark",
    pairedWith: "catppuccin-latte",
  },
  "dusk-ledger": {
    family: "dusk-ledger",
    label: "Dusk Ledger",
    mode: "dark",
    pairedWith: null,
  },
  "night-ledger": {
    family: "night-ledger",
    label: "Night Ledger",
    mode: "dark",
    pairedWith: null,
  },
};

/**
 * Read a supported design variant from a URL query string.
 *
 * @param search - The URL query string, including its optional leading `?`.
 * @returns The selected design, or null when the selection is absent or invalid.
 */
export function designVariantFromSearch(search: string): DesignVariant | null {
  const candidate = new URLSearchParams(search).get("design");
  return DESIGN_VARIANTS.find((variant) => variant === candidate) ?? null;
}

/**
 * Read the variant already applied to the page root.
 *
 * @param root - The page's root HTML element.
 * @returns The selected design, or null when none is active.
 */
export function activeDesignVariant(root: HTMLElement): DesignVariant | null {
  const candidate = root.dataset.design;
  return DESIGN_VARIANTS.find((variant) => variant === candidate) ?? null;
}

/**
 * Read the shared geometry family for one variant.
 *
 * @param variant - The selected design.
 * @returns The family name.
 */
export function designVariantFamily(variant: DesignVariant): string {
  return DESIGN_VARIANT_META[variant].family;
}

/**
 * Read whether one variant is light or dark.
 *
 * @param variant - The selected design.
 * @returns The variant mode.
 */
export function designVariantMode(variant: DesignVariant): DesignMode {
  return DESIGN_VARIANT_META[variant].mode;
}

/**
 * Read the human-facing label for one variant.
 *
 * @param variant - The selected design.
 * @returns The display label.
 */
export function designVariantLabel(variant: DesignVariant): string {
  return DESIGN_VARIANT_META[variant].label;
}

/**
 * Read the theme containing one concrete light or dark design.
 *
 * @param variant - The concrete design variant.
 * @returns The containing theme.
 */
export function designThemeForVariant(
  variant: DesignVariant,
): (typeof DESIGN_THEMES)[number] {
  const theme = DESIGN_THEMES.find(
    (candidate) => candidate.light === variant || candidate.dark === variant,
  );
  if (theme === undefined) {
    throw new Error(`design variant ${variant} has no theme`);
  }
  return theme;
}

/**
 * Choose a concrete variant from a theme without changing mode unnecessarily.
 *
 * @param themeId - The theme selected in the menu.
 * @param preferredMode - The current light or dark mode.
 * @returns The theme's preferred variant, or null for an unknown theme.
 */
export function designVariantForTheme(
  themeId: string,
  preferredMode: DesignMode,
): DesignVariant | null {
  const theme = DESIGN_THEMES.find((candidate) => candidate.id === themeId);
  if (theme === undefined) {
    return null;
  }
  return theme[preferredMode] ?? theme.light ?? theme.dark;
}

/**
 * Read the paired variant for one design, when it has one.
 *
 * @param variant - The selected design.
 * @returns The paired design, or null when it is unpaired.
 */
export function pairedDesignVariant(
  variant: DesignVariant,
): DesignVariant | null {
  return DESIGN_VARIANT_META[variant].pairedWith;
}

/**
 * Report whether one design participates in a light-dark pair.
 *
 * @param variant - The selected design.
 * @returns True when the design is paired.
 */
export function isPairedDesignVariant(variant: DesignVariant): boolean {
  return pairedDesignVariant(variant) !== null;
}

/**
 * Apply one already validated design selection to the page root.
 *
 * @param variant - The design to apply, or null to clear it.
 * @param root - The page's root HTML element.
 */
export function setDesignVariant(
  variant: DesignVariant | null,
  root: HTMLElement,
): void {
  if (variant === null) {
    root.removeAttribute("data-design");
    root.removeAttribute("data-design-family");
    root.removeAttribute("data-design-mode");
    root.removeAttribute("data-design-paired");
    return;
  }
  root.setAttribute("data-design", variant);
  root.setAttribute("data-design-family", designVariantFamily(variant));
  root.setAttribute("data-design-mode", designVariantMode(variant));
  root.setAttribute(
    "data-design-paired",
    isPairedDesignVariant(variant) ? "true" : "false",
  );
}

/**
 * Apply the selected design to the page root before the application renders.
 *
 * @param search - The URL query string to inspect.
 * @param root - The page's root HTML element.
 */
export function applyDesignVariant(search: string, root: HTMLElement): void {
  setDesignVariant(
    designVariantFromSearch(search) ?? DEFAULT_DESIGN_VARIANT,
    root,
  );
}

/**
 * Replace the current search string's design selection.
 *
 * @param search - The URL query string, including its optional leading `?`.
 * @param variant - The design to place in the query string.
 * @returns The updated query string.
 */
export function searchWithDesignVariant(
  search: string,
  variant: DesignVariant | null,
): string {
  const params = new URLSearchParams(search);
  if (variant === null) {
    params.delete("design");
  } else {
    params.set("design", variant);
  }
  const next = params.toString();
  return next === "" ? "" : `?${next}`;
}

/**
 * Build the accessible label for the theme-pair switch.
 *
 * @param current - The active design.
 * @param next - The design the switch will activate.
 * @returns The accessible label and title text.
 */
export function pairedVariantSwitchLabel(
  current: DesignVariant,
  next: DesignVariant,
): string {
  const direction = designVariantMode(next) === "dark" ? "dark" : "light";
  return `Switch from ${designVariantLabel(
    current,
  )} to ${designVariantLabel(next)} (${direction})`;
}
