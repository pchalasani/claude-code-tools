# Evaluation results

Run on 2026-09-26 against Cloudflare Jev, returned model `jev-1.13.0`.
All labels are agent-authored editorial judgments. These small, deliberately
constructed sets test usefulness; they do not establish production accuracy,
calibration, authorship detection, or coverage of every question.

## Threshold selection and prompt revision

The initial calibration run evaluated thresholds 0.65, 0.75, 0.85, 0.90, 0.95,
and 0.98. We chose **0.90**, the highest tested threshold that detected all five
problematic passages without flagging the five clean ones. At 0.95, two
problematic passages were missed. This is an operating-point choice, not a
calibration of individual probabilities or a confidence interval.

The original holdout then detected 7/10 problematic passages with 0/10 clean
passages flagged. Reviewing those results exposed missing prompt context: the
selected formal profile was not explicitly communicated to Jev. The final prompt
states the selected profile and distinguishes drafts intended for use from
intentionally requested templates. The question bank and threshold were unchanged.

The original holdout is therefore development evidence, not untouched validation
of the final prompt. We commissioned a **new independent holdout** without showing
its author results or the revised prompt, froze the prompt, and ran it once.
No threshold or wording changes followed that run.

| Set | Completed | True positive | False positive | True negative | False negative |
| --- | ---: | ---: | ---: | ---: | ---: |
| Final calibration | 10/10 | 5 | 0 | 5 | 0 |
| Fresh holdout | 20/20 | 8 | 0 | 10 | 2 |

Fresh holdout by profile: general had 6 true positives, 5 true negatives, and
2 false negatives; formal had 2 true positives and 5 true negatives. All 2,054
selected question judgments completed. Median complete-check latency was 0.425 s
(range 0.356–0.808 s), excluding authentication setup, on this small run. Network,
model, text length, question count, and service load affect latency.

## Misses and individual-finding review

The two fresh-holdout misses were repeated café marketing contrasts and a manual
containing assistant delivery chatter. Their relevant scores stayed below 0.90.
The writing-loop test also retained promotional wording below threshold. A clean
report means only that no selected score reached the threshold.

We inspected the individual findings, not only passage labels. Several
`step-forward-template` flags overlap significance inflation more broadly than
their narrow wording. The report's unspecified-improvement finding on a heavily
nominalized records-access sentence is defensible but misses the main grammatical
problem. These are reasons for the writer to inspect a flag before acting on it.
The fixture labels do not provide exhaustive per-question ground truth, so no
per-question precision, recall, or calibration claim is made.

## Real writing loops

`writing-loops.json` records an independent agent following the skill on three
passages, using the final prompt and a fixed 0.90 threshold. Four real checks
completed all 468 judgments:

- Export announcement: one revision; retained the 500-row CSV limit and the
  exclusion of archived rows. Final model-clean text still contains promotional
  wording, recorded explicitly as a residual editorial problem.
- Worker retry behavior: zero revisions; clean input remained byte-for-byte
  unchanged, preserving the 30-second interval and two-failure policy.
- Eight-user pilot: zero revisions; retained eight participants, five unassisted
  completions, and the substantive conclusion. The proof-overclaim finding
  remains unresolved at 0.98 because changing that conclusion was not authorized.

An earlier test narrowed the conclusion while keeping the observed counts. We
retained that evidence in `pre-preservation-writing-loops.json` and tightened
the skill to preserve substantive claims, then ran the final test from the
originals. A disagreement about a claim is now reported instead of silently
rewritten.

`claude-writing-loop.json` records a separate real Claude Code CLI run using the
globally installed skill and command from a temporary directory. Two checks
answered 234/234 judgments; one revision removed a filler lead-in while keeping
the 60-second refresh interval, two-snapshot limit, memory rationale, and failure
behavior unchanged. That run began before the final claim-preservation wording
clarification; it did not change any substantive claim.

No loop exceeded three revisions. Unit tests cover transport failure, missing
answers, malformed probabilities, model changes, and CLI exit semantics; they
are not substitutes for these live calls.

## Reproduce and inspect

From the repository root after installing the package:

```bash
python packages/jev-prose/eval/evaluate.py --split calibration \
  --output /tmp/jev-calibration.json
python packages/jev-prose/eval/evaluate.py --split holdout \
  --cases packages/jev-prose/eval/fresh-holdout.jsonl \
  --threshold 0.90 --output /tmp/jev-holdout.json
```

The evaluator requires every selected case to complete before emitting metrics.
Reports retain full judgments, bank hash, returned model, prompt hash for final
runs, and elapsed time. Initial runs predate prompt hashing and are labeled as
historical evidence here. Repeated calls can differ; changing model, bank, prompt,
profile, or thresholds requires reassessment. Use human-reviewed examples from
your actual writing before treating this as a publication gate.

The bank hash for these runs is:

```text
4016262a5640221f4fe567ef06ae1d5d7eb16ca9e175501ed0d5baa1112c8d56
```
