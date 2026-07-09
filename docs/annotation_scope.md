# Annotation Scope Decisions

This document records current project decisions about which records should and
should not be sent to human annotation. These rules are intentionally separate
from implementation details so they can be reconsidered later.

## Project Goal

The project is building a training dataset of pairs:

```text
Tatar Cyrillic sentence -> Zamanalif Latin sentence
```

The rule-based converter is a helper. Human annotation should focus on words
where the conversion choice is not deterministic enough for the current rules.

## Project 1: Word Dictionary Review

Project 1 is for unique word-form review. Annotators review a normalized word
once, and the approved result is stored in `reviewed_words`.

### Excluded From Project 1

- **Not mostly Tatar sentences.** If Gemini marks a sentence as
  `"tatar": false`, ignore it.
- **Already reviewed words.** If a normalized word is already present in
  `reviewed_words`, do not export it again.
- **Already exported words, when tracking is enabled.** With
  `--track-exported`, exported words are remembered and skipped in later real
  exports. Without tracking, dry-run exports may show the same words again.
- **Gemini-marked homonyms.** Words with `homonym: true` need sentence context,
  so they are deferred to a later contextual annotation project.
- **Native-looking mixed-harmony words.** If Gemini labels a word `N` and the
  word has mixed front/back vowels, skip it for Project 1. Keep matching `RL`
  and `U` words.
- **Origin-independent words.** If native and Russian-loanword conversion
  branches produce the same Zamanalif result, skip the word because origin
  annotation cannot change the target text.
- **Deterministic words.** Words whose conversion is already clear should not
  be sent to Label Studio.
- **Punctuation-only or empty normalized tokens.** Ignore them.
- **Below minimum frequency.** If an export uses `--min-frequency`, skip words
  below that threshold.

## Final Training Dataset Policy

The final training dataset should contain plain Zamanalif text, not DSL syntax.
DSL variants are an internal review/storage mechanism and must be resolved
before training export.

Current preferred dataset policy excludes these PDF-reference policies:

- deliberate vowel-harmony restoration for words that are disharmonic in
  Cyrillic;
- special rewritten month-name spellings from the PDF.

These decisions may change, but they should be changed here first and then
reflected in code and tests.
