# Visual Brief calm design variants

Visual Brief exposes nine named CSS comparison surfaces organized into six
themes:

- `?design=north-window` is a bright, paper-like direction with blue and mint
  bands.
- `?design=blue-margin` is a restrained editorial light direction organized by
  fine rules and open margins.
- `?design=dusk-margin` is the paired dark Blue Margin surface in a quiet
  blue-gray range.
- `?design=solarized-paper` is the paired light Solarized-inspired surface.
- `?design=solarized-slate` is the paired dark Solarized-inspired surface.
- `?design=catppuccin-latte` is the paired light Catppuccin-inspired surface.
- `?design=catppuccin-mocha` is the paired dark Catppuccin-inspired surface.
- `?design=dusk-ledger` is a blue-gray dark direction with clear ledger-like
  divisions.
- `?design=night-ledger` is a quieter near-black ledger with sparse accents.

Each URL renders the same Visual Brief application and the same content. The
query parameter selects only a design surface. Every surface must preserve all
application behavior, including navigation, state, live updates, and actions.

A tiny bootstrap runs before the stylesheet, validates the `design` query
parameter, and writes an accepted value to `data-design` on the document
element. It also writes `data-design-family`, `data-design-mode`, and
`data-design-paired` so the paired variants can share one geometry layer while
changing only palette tokens without a wrong first paint. The Solid entry point
revalidates the same choice before rendering. Missing, blank, and unsupported
values select Catppuccin Mocha, the default.

The frontend imports the base styles, one shared Blue Margin family stylesheet,
and each scoped palette stylesheet. The six paired surfaces share the same
layout geometry, spacing, widths, row hierarchy, and chat structure through the
Blue Margin family attribute. Toggling inside a pair updates the root
attributes, changes the query string with `history.replaceState`, and preserves
the same Solid application, DOM, cursor, folds, and drafts.

The masthead always shows a compact top-right theme selector. Each paired theme
appears once, labeled with both variants, such as `Catppuccin Latte/Mocha`.
Choosing another paired theme preserves the current light or dark mode. An
adjacent sun or moon button switches modes within the selected theme and
carries an accurate accessible label and title naming the destination variant.
The original comparison baseline is not a theme and does not appear in the
menu. The button does not appear on the unpaired studies: `north-window`,
`dusk-ledger`, and `night-ledger`.

Across all six paired variants, Atkinson Hyperlegible Next Variable remains the
reading and display face, while SF Mono remains reserved for utility labels,
shortcuts, timestamps, and code. Human-authored thread titles, turns, and draft
text stay blue. Agent output stays neutral. The conversation surface avoids a
broad tinted box and instead uses open space, a quiet inset rule, and the slim
blue margin cue as the signature element. The working shimmer and pulsing dot
remain visible in every paired variant.
