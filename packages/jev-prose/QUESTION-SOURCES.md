# Question Sources and Coverage

Bank version: **1.0.0**. The bank contains **118 narrow questions**: 95 general,
117 formal (including all general questions), and one strict-only preference.
The strict profile includes all formal questions plus that one preference.

These are editorial diagnostics, not an authorship detector. A yes probability
estimates whether the specified pattern is present under the supplied context;
it does not estimate the probability that an AI wrote the text. The original
catalogs contain contested frequency, psychology, and stylometry claims. This
bank does not adopt those claims as measured facts.

## Provenance and Attribution

Read-only local source snapshots were used; upstreams were not updated.
Question wording and rewrite guidance are newly composed adaptations. No long
examples, benchmark claims, or third-party research passages are reproduced.

- **AS**: Yue Zhao and contributors, *The Elements of Agent Style*,
  [agent-style](https://github.com/yzhao062/agent-style), `RULES.md`.
  Commit: `e3f14369e66095e518f7b5b328a6cfdc9eb97163`.
  Rules are licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  Changes: split broad rules into yes/no diagnostics, added contextual
  exceptions, separated formal preferences, and deferred unverifiable claims.
  The adapted question bank and this coverage document are provided under
  CC BY 4.0. No endorsement by the source authors is implied.
- **AA**: Conor Bronsdon, *Avoid AI Writing*, version 3.22.1,
  [avoid-ai-writing](https://github.com/conorbronsdon/avoid-ai-writing),
  `SKILL.md`, commit `449a0b860b394f2a941f1b91f268ffb8badbb786`.
  Licensed MIT; the full notice is retained below.
- **HU**: blader and contributors,
  [humanizer](https://github.com/blader/humanizer), `SKILL.md` version 2.2.0,
  commit `8fee293ff87950f1b8be9b10c2a8659e0a21c204`.
  The checked snapshot has no separate license file. It was consulted for
  category ideas; its examples and expressive passages are not redistributed.
  Its numbered categories are mapped using `SKILL.md`, not the older numbering
  still present in its README. It attributes its ideas to Wikipedia's
  *Signs of AI writing*; that page was not an additional fetched source here.
- **Local wrappers**: the installed agent-style wrapper and remove-ai-patterns
  wrapper were consulted for scope, preservation, update policy, and the
  opt-in causal-so preference. Machine paths and personal names are excluded
  from the implementation. Wrapper hashes below identify the snapshots.

SHA-256 snapshot hashes:

```text
agent-style wrapper:
3b9c5b946d4307c781bad5ec527141f9f47d1ccd0abc82494608e5a20c2062b0
remove-ai-patterns wrapper:
f6e05240cab337fb7d283d18e3d454f95f2c38eb7492d3a6706ac772a09bca2b
humanizer SKILL.md:
cc093b4f5771a0238472a1411889eeb85d068732cd21f1131a6392bfa92dd372
```

## Interpretation Contract

Each question asks whether one defect is present; **yes means a candidate
editorial finding**. The full question contains the necessary meaning because
question IDs are bookkeeping, not model instructions. Profiles restrict style
preferences. Scope identifies the context required for a meaningful check;
a document question cannot be certified absent from a lone sentence.

Quoted speech, attributed text, code, deliberate bad examples, templates, and
explicitly requested rhetorical styles must be handled according to the
shared detector context. Text alone cannot reveal an unprovided audience,
source provenance, actual author feelings, or truth of an external citation.
Unknown context must not be filled in by the detector or the rewriting agent.

A finding is a request to inspect and revise when appropriate, not an order to
replace every matching word. Preserve facts, claims, scope, uncertainty,
negation, constraints, voice, citations, and meaningful formatting. Missing
specifics are gaps to report; they must never be fabricated. Strong source
claims about word frequencies have deliberately become contextual questions.
The bank is comprehensive at the source-category level, not a verbatim copy
of every word-list entry or a claim of perfect detection recall.

## Complete Source Map

Every agent-style rule, humanizer numbered category, and avoid-ai-writing
pattern category appears below. IDs beginning `AA-` are local stable aliases
for source headings, which do not have upstream numeric identifiers. A source
can map to several questions; one question can satisfy overlapping sources.
Items with no question are explicitly deferred with a reason.

### AS-01: RULE-01: reader knowledge and purpose

Questions:

- `undefined-coined-label`

- `acronym-pileup`

- `mechanics-without-purpose`

Audience knowledge is checked only when an audience is explicitly supplied. Naming a
reader is a writer responsibility, not a defect detectable from every isolated sentence.

### AS-02: RULE-02: active voice when the actor matters

Questions:

- `known-actor-passive`

### AS-03: RULE-03: concrete language

Questions:

- `vague-category-nouns`

- `unspecified-improvement`

- `unsupported-quality-label`

- `abstract-complexity`

### AS-04: RULE-04: needless words

Questions:

- `importance-windup`

- `wordy-purpose`

- `wordy-cause`

- `wordy-time`

- `wordy-condition`

- `wordy-ability`

- `redundant-hedges`

### AS-05: RULE-05: stale metaphors and stock phrases

Questions:

- `exploration-cliche`

- `abstract-scenery`

- `boundary-breakthrough`

### AS-06: RULE-06: plain vocabulary

Questions:

- `inflated-use-verbs`

- `corporate-nominalization`

- `ornate-start-verbs`

### AS-07: RULE-07: affirmative statements and meaningful contrasts

Questions:

- `negative-foil`

- `needless-double-negative`

Single negations and meaningful technical exclusions remain valid. Affirmative
substitutions must preserve strength: not large does not always mean small.

### AS-08: RULE-08: claim calibration

Questions:

- `reflexive-weasel`

- `proof-overclaim`

- `unbounded-superiority`

Only mismatches visible in the provided evidence are detectable. External truth and
benchmark completeness require separate verification.

### AS-09: RULE-09: parallel grammar

Questions:

- `nonparallel-coordinates`

### AS-10: RULE-10: related words together

Questions:

- `subject-verb-interruption`

- `misplaced-modifier`

### AS-11: RULE-11: emphasis and stress position

Questions:

- `buried-main-result`

Stress-position advice is contextual; moving every key fact to the end would create
another template.

### AS-12: RULE-12: sentence length and variation

Questions:

- `monotonous-sentence-rhythm`

- `overloaded-long-sentence`

Thirty words is a review cue, not an automatic fault. Long clear logical units remain
valid.

### AS-A: RULE-A: genuine lists rather than fragmented prose

Questions:

- `forced-triads`

- `bullet-fragmented-argument`

### AS-B: RULE-B: prose dashes

Questions:

- `prose-dash-overuse`

- `formal-prose-dash`

### AS-C: RULE-C: repeated sentence openings

Questions:

- `repeated-sentence-template`

### AS-D: RULE-D: transition overuse

Questions:

- `empty-additive-transitions`

### AS-E: RULE-E: redundant paragraph summaries

Questions:

- `summary-announcement`

- `redundant-paragraph-closer`

### AS-F: RULE-F: consistent terms and abbreviations

Questions:

- `synonym-cycling`

- `redefined-abbreviation`

### AS-G: RULE-G: title-case headings

Deferred: agent-style prefers title case, while both removal catalogs prefer sentence
case. Neither is a universal defect. Preserve the project or publication convention.

### AS-H: RULE-H: citation and evidence discipline

Questions:

- `vague-universal-authority`

- `vague-attribution`

- `unbounded-superiority`

External citation existence, source support, fabricated facts, and completeness are
deferred to source verification. The bank checks unnamed attribution and visible
evidential overreach only. Absence of a citation is not proof of falsity.

### AS-I: RULE-I: formal contractions

Questions:

- `formal-contractions`

### AA-formatting: Formatting

Questions:

- `bullet-fragmented-argument`

- `excessive-bold`

- `decorative-heading-emoji`

- `prose-dash-overuse`

Smart quotes, curly apostrophes, and immaculate typography are deferred as unreliable
stylometry. General dash checks require harmful repetition; the stricter single-dash
preference is formal-only. Emoji decoration is formal-only. Legitimate lists and
meaningful bold labels remain valid.

### AA-sentence: Sentence structure

Questions:

- `empty-intensifiers`

- `reflexive-weasel`

- `negative-foil`

- `split-negative-reveal`

- `tailing-negation`

- `forced-triads`

- `social-endorsement`

- `missing-argument-bridge`

Preserve distinct uncertainty, genuine purpose clauses, useful bridge sentences, real
triads, and meaningful exclusions. A sentence fragment is not automatically defective.

### AA-vocab-1a: Words and phrases: Tier 1A frequency-marker vocabulary

Questions:

- `inflated-use-verbs`

- `exploration-cliche`

- `abstract-scenery`

- `boundary-breakthrough`

- `unsupported-quality-label`

- `abstract-complexity`

Adapted as contextual semantic questions, not an unconditional denylist. Literal or
precise technical uses remain valid. No inherited frequency ratios or authorship claims
are accepted as measured evidence. The listed words are representative cues, not an
exhaustive lexical match inventory.

### AA-vocab-1b: Words and phrases: Tier 1B clarity vocabulary

Questions:

- `wordy-purpose`

- `wordy-cause`

- `copula-avoidance`

- `boastful-possession`

- `inflated-use-verbs`

- `corporate-nominalization`

- `ornate-start-verbs`

Clarity suggestions, never authorship evidence. Meaning-specific uses of a longer word
are preserved.

### AA-vocab-2: Words and phrases: Tier 2 clustered vocabulary

Questions:

- `business-buzzword-cluster`

Cluster plus semantic emptiness is required, not just the presence of two words from a
list. The examples represent the category rather than every inflected word.

### AA-vocab-3: Words and phrases: Tier 3 generic-praise density

Questions:

- `generic-praise-density`

- `unbounded-superiority`

Semantic overuse replaces a brittle percentage cutoff; legitimate technical uses are
preserved. This is not a calibrated vocabulary-density statistic.

### AA-vocab-phrases: Words and phrases: repeated/clustered Tier 3 phrases

Questions:

- `repeated-sector-boilerplate`

- `clustered-sector-boilerplate`

Repetition and clustering are separate narrow questions. Technical mechanisms remain
valid when actually explained.

### AA-templates: Template phrases

Questions:

- `step-forward-template`

- `false-audience-breadth`

- `pleasure-announcement`

### AA-transitions: Transition phrases

Questions:

- `empty-additive-transitions`

- `era-opener`

- `reader-steering`

- `summary-announcement`

- `topic-windup`

- `repeated-concession-transition`

### AA-structural: Structural issues

Questions:

- `era-opener`

- `uniform-paragraph-template`

Uniformity is checked only when repeated templates introduce redundancy. Suspiciously
clean grammar is deferred; deliberate typos are never introduced.

### AA-significance: Significance inflation

Questions:

- `significance-inflation`

### AA-aphorism: Aphorism formulas

Questions:

- `aphorism-formula`

### AA-future: Generic future-narrative closers

Questions:

- `future-narrative`

### AA-hedge-stacks: Hedge-stacked predictions

Questions:

- `redundant-hedges`

### AA-real-actual: Real/actual adjective inflation

Questions:

- `real-actual-inflation`

### AA-moral: Moral-adjective category errors

Questions:

- `moralized-technical-property`

- `vague-universal-authority`

Moral metaphors are flagged only when their technical meaning is unclear. The upstream
claim that assumptions cannot become false is not adopted: truth can depend on changing
conditions. Universal appeals are checked as rhetoric, not externally fact-checked.

### AA-hashtags: Hashtag stuffing

Deferred: counts alone do not establish a prose defect, and platform-specific tag
strategy is outside this prose detector. Do not infer authorship from tag counts.

### AA-bare-bullets: Bullet lists of bare noun phrases

Questions:

- `bare-praise-bullets`

### AA-copula: Copula avoidance

Questions:

- `copula-avoidance`

- `boastful-possession`

### AA-fragments: Subjectless fragments and agentless passives

Questions:

- `known-actor-passive`

- `subjectless-flowing-prose`

### AA-synonyms: Synonym cycling

Questions:

- `synonym-cycling`

### AA-attribution: Vague attributions

Questions:

- `vague-attribution`

### AA-filler: Filler phrases

Questions:

- `importance-windup`

- `topic-windup`

### AA-conclusions: Generic conclusions

Questions:

- `generic-upbeat-closer`

- `time-will-tell`

### AA-chatbot: Chatbot artifacts

Questions:

- `chatbot-preface`

- `chatbot-signoff`

### AA-lets: Let's constructions

Questions:

- `lets-transition`

### AA-notability: Notability name-dropping and historical analogy stacks

Questions:

- `notability-name-dropping`

- `historical-analogy-stack`

### AA-validation: Vague third-party validation

Questions:

- `faceless-validation`

### AA-ing: Superficial -ing analyses and declarative meaning-telling

Questions:

- `superficial-ing`

- `meaning-telling`

### AA-promotion: Promotional language

Questions:

- `promotional-description`

### AA-challenges: Formulaic challenges

Questions:

- `formulaic-resilience`

### AA-scenarios: Speculative scenario openers

Questions:

- `speculative-world-opener`

### AA-ranges: False ranges

Questions:

- `false-range`

### AA-inline-lists: Inline-header lists

Questions:

- `inline-label-repetition`

### AA-list-periods: List-label periods

Deferred: label periods versus colons are a formatting convention, not reliably an AI-
pattern defect. The related redundant-label issue is covered separately.

### AA-title-case: Title case headings

Deferred: conflicting style preference; see AS-G.

### AA-hyphen: Hyphenated-pair overuse

Questions:

- `stacked-compound-modifiers`

Compound-praise stacks are covered. Universal dehyphenation of predicate compounds is
deferred because lexicalized compounds and technical terms require dictionary or house-
style judgment.

### AA-cutoff: Cutoff disclaimers

Questions:

- `training-cutoff-artifact`

A truthful relevant limitation must never be hidden to make prose appear confident. Only
residual assistant narration in the artifact is targeted.

### AA-gap: Speculative gap-filling

Questions:

- `speculative-gap-fill`

Only a guess following an explicit knowledge gap is covered. Whether an otherwise
plausible claim was invented cannot be inferred from prose alone.

### AA-placeholder: Unfilled placeholders

Questions:

- `unfilled-placeholder`

Intentional templates, instructions, and quoted examples are exempt. An unresolved
placeholder is a publishing issue, not an authorship verdict.

### AA-citation: Chatbot citation markup leaks

Questions:

- `citation-markup-leak`

Markup cleanup must preserve meaningful attribution. The detector does not claim it can
validate the cited source.

### AA-url: AI-tool URL parameters

Questions:

- `ai-tracking-link`

Inspect only authored prose hyperlinks, not quoted URLs or code examples. Removing
tracking is optional editorial cleanup, not evidence of authorship.

### AA-novelty: Novelty inflation

Questions:

- `novelty-scarcity-hook`

- `undefined-coined-label`

Unsupported nobody-knows hooks and unexplained labels are covered. Verifying who coined
a concept requires external evidence and is deferred.

### AA-hooks: Infomercial engagement hooks

Questions:

- `infomercial-hook`

- `fake-candid-hook`

### AA-endorsement: Social endorsement closers

Questions:

- `social-endorsement`

### AA-emotion: Emotional flatline

Questions:

- `emotion-announcement`

### AA-attention: Lingering-attention claims

Questions:

- `lingering-attention`

### AA-concession: False concession structure

Questions:

- `false-concession`

### AA-mirroring: Invented contrast-pair mirroring

Questions:

- `mirrored-contrast`

The question checks an unexplained contrast. It cannot determine from unfamiliarity
alone that a term is fictitious.

### AA-rhetorical: Rhetorical question openers

Questions:

- `rhetorical-question-windup`

### AA-parenthetical: Parenthetical hedging

Questions:

- `parenthetical-hedging`

### AA-numbered: Numbered list inflation

Questions:

- `numbered-list-padding`

### AA-reasoning: Reasoning chain artifacts

Questions:

- `reasoning-scaffold`

### AA-sycophancy: Sycophantic tone

Questions:

- `sycophantic-validation`

### AA-candor: Narrated candor

Questions:

- `narrated-candor`

### AA-acknowledgment: Acknowledgment loops

Questions:

- `acknowledgment-loop`

### AA-confidence: Confidence calibration phrases and authority tropes

Questions:

- `reader-steering`

- `emphasis-stacking`

- `persuasive-authority`

### AA-self-label: Self-labeling significance

Questions:

- `self-labeling-significance`

### AA-wall-text: Wall-of-text replies

Deferred: a single coherent paragraph can be correct in a reply, email, or document.
Length and missing line breaks alone are unreliable signals; use the writer context to
choose formatting.

### AA-recap: Recap-flattery opener

Questions:

- `recap-flattery`

### AA-structure: Excessive structure

Questions:

- `heading-warmup`

- `fragmented-short-sections`

### AA-diff: Diff-anchored writing

Questions:

- `diff-anchored-docs`

### AA-staccato: Manufactured punchlines and staccato drama

Questions:

- `staccato-drama`

### AA-rhythm: Rhythm and uniformity

Questions:

- `repeated-sentence-template`

- `monotonous-sentence-rhythm`

- `uniform-paragraph-template`

Observable redundant rhythm is covered. Missing first person, emotional neutrality,
read-aloud impressions, and absence of quirks are not defects by themselves. Never
insert opinions, anecdotes, fragments, or errors to imitate a human.

### AA-stylometry: Vocabulary diversity and proposed stylometric signals

Deferred: type-token ratios, burstiness, function-word z-scores, and POS-bigram scores
need suitable reference data and genre-specific validation. They are not answerable
reliably as prose-only Jev questions and do not establish authorship.

### AA-reshuffle: Paragraph-reshuffle immunity

Questions:

- `missing-argument-bridge`

Only an argument requiring a missing logical bridge is checked. Independent reference
sections may be rearrangeable without being defective.

### AA-treadmill: Treadmill effect and information density

Questions:

- `treadmill-paragraph`

### HU-voice: Personality and soul

Questions:

- `repeated-sentence-template`

- `monotonous-sentence-rhythm`

Rhythm suggestions are adapted. Injecting personality, personal experience, humor,
opinions, or emotional reactions is rejected unless already provided by the author.

### HU-01: 1: significance, legacy, and broad trends

Questions:

- `significance-inflation`

- `meaning-telling`

### HU-02: 2: notability and media coverage

Questions:

- `notability-name-dropping`

### HU-03: 3: superficial -ing analyses

Questions:

- `superficial-ing`

### HU-04: 4: promotional language

Questions:

- `promotional-description`

### HU-05: 5: vague attribution

Questions:

- `vague-attribution`

### HU-06: 6: challenges and future prospects templates

Questions:

- `formulaic-resilience`

### HU-07: 7: overused AI vocabulary

Questions:

- `empty-intensifiers`

- `exploration-cliche`

- `abstract-scenery`

- `business-buzzword-cluster`

- `empty-additive-transitions`

### HU-08: 8: copula avoidance

Questions:

- `copula-avoidance`

- `boastful-possession`

### HU-09: 9: negative parallelism

Questions:

- `negative-foil`

- `split-negative-reveal`

### HU-10: 10: three-part groupings

Questions:

- `forced-triads`

### HU-11: 11: synonym cycling

Questions:

- `synonym-cycling`

### HU-12: 12: false ranges

Questions:

- `false-range`

### HU-13: 13: dash overuse

Questions:

- `prose-dash-overuse`

### HU-14: 14: boldface overuse

Questions:

- `excessive-bold`

### HU-15: 15: inline-header vertical lists

Questions:

- `bullet-fragmented-argument`

- `inline-label-repetition`

### HU-16: 16: title-case headings

Deferred: conflicting heading-case preference; see AS-G.

### HU-17: 17: decorative emoji

Questions:

- `decorative-heading-emoji`

### HU-18: 18: curly quotation marks

Deferred: smart typography is commonly generated by ordinary editors and is not a
reliable defect or authorship signal.

### HU-19: 19: staccato fragments

Questions:

- `staccato-drama`

### HU-20: 20: collaborative communication artifacts

Questions:

- `chatbot-preface`

- `chatbot-signoff`

### HU-21: 21: knowledge-cutoff disclaimers

Questions:

- `training-cutoff-artifact`

Relevant uncertainty and date limits remain. Do not remove an honest limitation merely
to make writing more confident.

### HU-22: 22: sycophantic tone

Questions:

- `sycophantic-validation`

### HU-23: 23: filler phrases

Questions:

- `importance-windup`

- `wordy-purpose`

- `wordy-cause`

- `wordy-time`

- `wordy-condition`

- `wordy-ability`

### HU-24: 24: excessive hedging

Questions:

- `redundant-hedges`

### HU-25: 25: generic positive conclusions

Questions:

- `generic-upbeat-closer`

### LOCAL-so: Local wrapper: causal so clause joins

Questions:

- `causal-so-splice`

Opt-in strict profile only. This local preference is not asserted to be a universal AI
tell; genuine purpose, intensifiers, and fixed expressions remain valid.

## Process Guidance That Is Not a Detection Question

The source sections on modes, severity tiers (P0/P1/P2), context and voice
profiles, output formats, tone calibration, the self-reference escape hatch,
and whether to rebuild versus patch are workflow policies. They do not define
additional prose predicates. The reusable writing skill should implement
preservation, contextual judgment, bounded revisions, and reporting of
remaining findings. The requested limit of three revision attempts supersedes
the upstream two-pass recommendation. No source severity label is treated as a
validated statistical risk score.

The local wrapper's instruction to use only one final style gate is respected
by one combined detector with explicit profiles. It does not run alternating
agent-style and removal passes. Wrapper update commands are maintenance
instructions, not runtime requirements. The bank is versioned independently;
source updates need an explicit source review, mapping update, and evaluation.

Humanizer's process and full before/after example are not copied. Its example
of adding anecdotes or citations is not permission to invent either. The
avoid-ai-writing `Never inject these` section is an editor constraint: changes
must not add fake first person, manufactured stakes, forced contrarianism,
performed candor, theatrical dashes, staccato fragments, or invented specifics.
Such failures require comparing original and revised text, not judging E alone.

The bank intentionally does not implement universal bans on ordinary words,
contractions in casual prose, correct passive voice, useful lists, or meaningful
negation. It does not automatically remove disclosures. It does not decide that
a text is AI-authored, dishonest, plagiarized, or factually false.

## Retained MIT Notice for Avoid AI Writing

Copyright (c) 2026 Conor Bronsdon

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
