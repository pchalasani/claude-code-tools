# Brief v2: Signal Desk Visual Direction

## Subject and Job

The subject is a keyboard-first briefing surface shared by one person and a
coding agent. The person needs to understand what is true now, see what just
changed, and ask a precise follow-up without reconstructing a terminal log.

The page has one job: make current state and recent change understandable at a
glance while preserving exact chat anchors and keyboard control.

## Direction

The visual direction is **Signal Desk**: a quiet, cool-toned working surface
that borrows from an editor's briefing board rather than a terminal or an
analytics dashboard.

The page separates two kinds of information:

- Current state is a spacious field of addressable lane cards.
- Dated changes form a quieter chronological ledger below it.

The signature element is one blue-to-teal signal spine on the current-state
container. It marks the boundary between what is true now and what entered
history. No other decorative flourish competes with it.

## Palette

The core light palette has six named colors:

| Token | Value | Role |
| --- | --- | --- |
| Fog | `#f4f7fb` | Page ground |
| Paper | `#ffffff` | Reading surfaces |
| Mist | `#e9f0f7` | Quiet grouping |
| Ink | `#132238` | Primary text |
| Cobalt | `#3157d5` | Selection and current state |
| Teal | `#087565` | Verified and active signals |

Coral is reserved for limitations, and amber is reserved for uncertainty.
Dark mode uses the same cool hue relationships rather than a separate visual
identity.

## Type

- Display: `Avenir Next`, then modern system-display fallbacks. It is used for
  the page title and major state and update headlines.
- Reading: `Avenir Next`, then system sans fallbacks. A generous line height
  and constrained measure carry the calmness rather than a decorative face.
- Utility: `SFMono-Regular`, then common monospace fallbacks. It is limited to
  dates, counts, keyboard labels, and small status language.

The restrained family choice is intentional. A serif-and-cream editorial look
would be an attractive but generic answer, and a neon terminal look would
misrepresent the page as an execution console.

## Layout Exploration

### Rejected: single reading column

```text
map | heading
    | current state
    | lane
    | item
    | lane
    | item
    | update
    | lane
```

This preserves the current density and does not use the page to explain the
difference between state and history.

### Chosen: state field and change ledger

```text
map | session title and compact controls
    |
    | ╭ signal spine ─ CURRENT STATE ───────╮
    | │ summary                                           │
    | │ ┌ lane card ───┐  ┌ lane card ───┐ │
    | │ │ items and chat  │  │ items and chat          │ │
    | │ └───────────────┘  └───────────────┘ │
    | ╰───────────────────────────────────────╯
    |
    | DATED CHANGES
    | date  headline and summary
    |       lanes, items, evidence, and chat
```

State cards collapse to one column on narrow screens. History remains a single
reading stream because its order is meaningful.

## Interaction Treatment

The redesign must not change interaction semantics.

- Every existing row id, chat anchor, fold, keyboard command, search result,
  cursor rule, pending sign, and live-patch invariant remains intact.
- Chat controls become quiet text actions instead of prominent white chips.
- Trust marks use quiet tinted labels whose text keeps at least 4.5:1 contrast;
  only active warnings use stronger color.
- Keyboard controls remain visible but sit in a subdued utility rail.
- Focus is unmistakable through a cobalt rail and soft wash, not a large box.
- Motion is limited to the existing cursor and working indicators and still
  respects reduced-motion preferences.

## Configuration Contract

Shared adjustable visual parameters belong in one imported token file.
Component styles consume semantic variables and do not repeat raw colors or
meaningful component dimensions where CSS custom properties are practical.

The central controls include:

- light and dark palette values;
- display, reading, and utility font stacks;
- page, map, content, and reading widths;
- state-card minimum width and grid gap;
- composer, search, help, row, and compact-control dimensions;
- spacing scale and row density;
- surface and control radii;
- border and signal-spine widths;
- shadow strength; and
- motion duration.

Changing those values must not require editing Solid components. Structural
mode changes, such as switching the state field from two columns to one, may
use a documented root data attribute if one becomes necessary.

Media-query thresholds remain beside the selectors they govern. CSS custom
properties cannot be used in media-query conditions, so presenting those
thresholds as centralized tokens would make the configuration contract
misleading. Incidental one-pixel accessibility geometry and animation offsets
also remain local because they are implementation details, not meaningful
visual controls.

## Responsive and Accessibility Floor

- The design must remain usable at desktop and narrow mobile widths.
- Keyboard focus must remain visible against every surface.
- Text and controls must maintain readable contrast in light and dark modes.
- Horizontal overflow is allowed only for tables and the narrow map strip.
- Reduced-motion mode must remove nonessential transitions.

## Build Boundary

This round is a visual variant, not an information-model rewrite. Small wrapper
elements or class names may be added to express the layout, but state, history,
navigation, chat behavior, persistence, and publishing contracts stay unchanged.
