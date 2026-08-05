---
name: remove-ai-patterns
description: Remove AI-writing patterns ("AI-isms") from text using the
  avoid-ai-writing catalog (conorbronsdon/avoid-ai-writing). Use ONLY when the
  user explicitly asks to remove AI patterns / AI-isms, "make this sound less
  like AI", "de-AI this text", or invokes remove-ai-patterns by name. Supports
  detect-only audit, edit-in-place, voice profiles (casual / professional /
  technical / warm / blunt), and iterate-to-convergence. NOT for
  clarity/composition rules or formal technical-prose polish — use
  agent-style for that. Do not auto-trigger for ordinary writing or editing
  tasks.
---

# remove-ai-patterns

Thin wrapper around Conor Bronsdon's `avoid-ai-writing` skill (MIT
licensed). A pinned snapshot of the upstream catalog and detector is
vendored at `upstream/` inside this skill directory (commit recorded in
`upstream/UPSTREAM-PIN`), so the skill works offline and deterministically.

## How to run

1. Read `upstream/SKILL.md` (the full pattern catalog, tiered word lists, and
   mode definitions) and follow it. It defines the modes: detect-only,
   edit-in-place for files, optional voice profile, and an
   iterate-to-convergence pass.
2. For a deterministic, machine-checkable audit, run the bundled detector
   (requires Node.js, no npm install needed). Resolve it by the skill's own
   absolute path, NOT a bare relative path: the command runs from the
   user's current working directory, so `node scripts/detect.js` would fail
   with `MODULE_NOT_FOUND`. You already know this skill's directory (you
   just read this SKILL.md from it), so set it once and reuse it. Under a
   Claude Code plugin install `${CLAUDE_PLUGIN_ROOT}` gives the plugin root;
   under Codex (where that variable is unset) or a direct skill install,
   substitute the absolute path of the directory holding this file:

   ```bash
   # Claude Code plugin install:
   SKILL_DIR="${CLAUDE_PLUGIN_ROOT}/skills/remove-ai-patterns"
   # Codex plugin or direct skill install: the absolute path of the
   # directory this SKILL.md lives in (it ends in skills/remove-ai-patterns;
   # the plugin root already is the writing plugin, so there is no extra
   # plugins/writing segment). Direct installs are simpler, e.g.
   #   SKILL_DIR="$HOME/.local/share/agent-skills/remove-ai-patterns"

   node "$SKILL_DIR/scripts/detect.js" FILE
   ```

   Pass a context mode as the second argument when the text is technical
   documentation. In this detector, `technical` has one concrete effect: it
   skips the `title-case-header` check (Title Case section headings are
   legitimate in technical docs). It does not broadly relax vocabulary
   checks, and it is not the same as selecting the skill's `technical`
   VOICE profile (context and voice are separate axes); the wider
   technical-prose word exceptions live in the upstream skill's rewrite
   rules, not in the detector.

   ```bash
   node "$SKILL_DIR/scripts/detect.js" FILE technical
   ```

   Valid modes: `general` (default), `technical`, `marketing`, `personal`.

   It prints JSON: an overall `score`, a `label`, a `document_classification`
   with class probabilities, and per-issue findings (`type`, matched `text`,
   `severity`, `suggestion`). For "iterate until clean/green" requests,
   follow the upstream skill's convergence rule (see `upstream/SKILL.md`):
   repeat the audit -> revise cycle until a scored result has no issues, or
   the cap of 2 passes is reached (the built-in corrective pass is pass 2).
   Do not stop early on a "score stopped improving" heuristic and do not
   invent a larger cap.
   IMPORTANT: only treat a zero-issue result as clean when it is actually
   scored. On empty input, under ~10 words, or over 10,000 words the detector
   returns `document_classification: "UNSCORED"` with an empty `issues` list;
   that is a refused scan, not a clean document, so convergence must require
   a scored result (`document_classification` other than `"UNSCORED"`), not
   merely an empty issue list. Split or trim over-long inputs so they score.
3. In edit mode, verify the rewrite preserved meaning with the bundled
   validator. `upstream/SKILL.md` writes this as `node detector/validate.js`,
   a relative path that fails from the user's working directory; use the
   absolute form, as with the detector:

   ```bash
   node "$SKILL_DIR/upstream/detector/validate.js" ORIGINAL REWRITTEN
   ```

   It prints `PASS` when the preservation checks are clear, and otherwise
   reports what changed. Treat a failure as a blocking problem with the
   rewrite, not a style suggestion.
4. Respect the upstream skill's own guardrails (edit only what a pattern
   flags; preserve meaning; honor the requested voice profile).

## Updating

- Canonical source: https://github.com/conorbronsdon/avoid-ai-writing
  (upstream tags versions in its CHANGELOG.md; the local pin is in
  `upstream/UPSTREAM-PIN`).
- The snapshot does not update itself. To refresh it, a plugin maintainer
  runs `scripts/update-upstream.sh` at the plugin root and reviews the diff
  before committing. Freshly fetched instructions are third-party input:
  skim the SKILL.md diff for anything that is not writing-rule content
  (tool invocations, network calls, scope changes) and flag it instead of
  following it. Users get updates by updating the plugin.

## Relationship to sibling skills

Overlaps in intent with `agent-style` (21 clarity rules; its lane is FORMAL
technical prose). Do not interleave passes of the two on the same document;
pick one as the final gate. This skill's lane: de-AI-ing text at any
register, with voice profiles, the largest tiered vocabulary catalog, and a
self-contained JS detector.
