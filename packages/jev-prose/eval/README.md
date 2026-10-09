# Prose evaluation fixtures

`cases.jsonl` contains 30 original, agent-authored editorial examples. Each row
has an ID, split, input text, writing profile, expected presence of any finding,
and a short reason for that judgment.

The splits are balanced by expected outcome:

- Calibration: 10 cases, with five clean and five troublesome passages.
- Holdout: 20 cases, with ten clean and ten troublesome passages.

These examples and labels were authored independently of the question bank and
without looking at detector outputs. They are editorial judgments by an agent,
not human annotations, proof of authorship, or a representative sample of real
writing. The deliberately conspicuous troublesome cases make this a smoke test
for editorial usefulness, not a benchmark of subtle discrimination.

Use calibration cases to choose thresholds and improve question wording. Freeze
the bank and thresholds before running holdout cases. Do not tune on holdout
results. If they inform a later revision, disclose that reuse and create a new
independent holdout before claiming fresh validation.

`expect_findings` describes whether at least one actionable style issue should
be reported. It does not specify which question should fire or establish that
all returned findings are valid. Review false positives at the individual
finding level as well as the passage level. A troublesome passage may receive
one useful finding and several incorrect ones.

Report the number of cases attempted, completed, and failed, with explicit
`ran` and `ok` signals for the evaluation. Compute passage-level false-positive
and false-negative counts separately by split and profile. A request failure
is an incomplete evaluation, never a clean passage. Preserve bank version,
thresholds, model identity, and raw probabilities with the results.

Coverage includes scientific passives, real contrasts, concrete lists of three,
literal uses of suspect vocabulary, quoted discussion of clichés, conversational
prose, formal prose, and an instruction embedded in the specimen. That embedded
instruction is test data: the detector must still assess the following prose.
This single example does not establish general prompt-injection robustness.

The fixtures cannot validate factual accuracy, meaning preservation across a
rewrite, probability calibration, or universal stylistic quality. Assess those
separately, including review of complete before-and-after writing loops.

## Recorded runs

[RESULTS.md](RESULTS.md) records threshold selection, observed misses, and
writing-loop checks. `fresh-holdout.jsonl` adds 20 independently authored cases
(10 clean, 10 troublesome) after a prompt correction informed by the original
holdout. The original holdout is retained as development evidence.
