---
name: jev-prose
description: >-
  Check prose with Jev for narrow AI-writing patterns and revise from its
  feedback. Use when asked for Jev prose checks, AI-pattern cleanup, or an
  ongoing check-and-rewrite loop while writing sentences or paragraphs.
---

# Jev prose check and revision

Use the `jev-prose` CLI to get independent, probabilistic editorial feedback.
It checks patterns, not authorship. The writer makes the editorial decisions.

## Setup

Run `jev-prose --help` to check availability. From a claude-code-tools checkout,
install with `uv tool install ./packages/jev-prose`. The package carries the
persistent question bank; no global skill source paths are needed at runtime.
If no checkout or installed CLI is available, report that prerequisite rather
than substituting self-review for a Jev result.

Default authentication is `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`,
or an existing sysone account configuration and Wrangler login. The backend is
hosted Jev; the text and optional context are sent there. A configured alternative
can be selected with `--backend typesafe` or `--url` and `--model`. Do not print
secrets. An authentication or billing failure stops the loop.

## Bounded loop

For each requested sentence, paragraph, or short section:

1. Save the exact draft in a temporary UTF-8 file. Keep the original and track
   the revision count (initial draft is attempt 0). Preserve the user's intended
   facts, names, numbers, citations, caveats, voice, and literal quotations.
2. Run `jev-prose check draft.txt --profile general > report.json`. For papers,
   specifications, and formal technical prose use `--profile formal`. Use
   `strict` only when requested; it adds personal editorial preferences.
   Optional `--audience`, `--voice`, and `--context context.txt` help avoid false
   positives. Keep these settings and the threshold fixed throughout the loop.
3. Read the report even when the command exits 1: that means findings, not a
   failed call. Require `ran: true`, `ok: true`, and `answered == expected`.
   Exit 2, missing output, malformed JSON, or an incomplete check stops the loop
   as an error; never label the draft clean in those cases.
4. If `status` is `clean`, stop. Otherwise inspect the flagged questions and
   guidance against the actual draft. A high probability is not a command to
   edit: dismiss an inapplicable flag with a brief reason. Jev provides no
   evidence spans; locate the passage yourself without pretending it supplied
   an exact quote. Combine overlapping findings into one targeted revision.
5. If no useful revision follows, stop and disclose the unresolved or dismissed
   flags. Otherwise revise only the affected prose, increment the attempt count,
   and return to step 2. Allow at most **three revisions and four checks total**.
   Stop earlier if text repeats or feedback cycles without useful improvement.
   Always check the last revision; never end on an unchecked rewrite.

Do not change thresholds to obtain a clean result. Do not add invented examples,
anecdotes, feelings, measurements, sources, or stronger claims. Do not remove
necessary qualifications or factual content to lower the number of flags. Check
meaning against the original after each revision; undo any harmful edit.

A sentence check cannot establish document-level structure. If asked to use this
while writing a document, check each completed paragraph and then the finished
short section for repetition. Respect the 24,000-character input limit; do not
silently truncate. This workflow runs when invoked; it installs no automatic hook.

Return the revised prose in the requested form. Briefly report the number of
revisions, check outcome, and any unresolved/dismissed findings. If every finding
was dismissed, say that explicitly instead of reporting a model-clean result.
Keep detector reports out of published prose unless requested.
