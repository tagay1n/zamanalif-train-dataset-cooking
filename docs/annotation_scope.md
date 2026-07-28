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
- **Contextual homonyms.** Effective homonyms are excluded from every dictionary
  project, including catchall.
- **Native hamza conversions.** Words whose native conversion emits `ʼ`, or
  contains the `HAMZA` policy rule, are routed only to the `hamza` dictionary
  project. They never appear in catchall or the multi-rule project. Russian
  soft/hard-sign apostrophes remain separate `rus_*` review cases.
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

Exporting alone never suppresses a word. It remains eligible until a successful
Label Studio import stores its completed review.

Annotators can mark any dictionary task as a contextual homonym. That decision
overrides an existing N/RL/U word resolution, ignores the task's origin and
conversion fields, and routes occurrences of the displayed word to the
contextual project on the next export. For a grouped catchall task, only the
displayed representative is marked; hidden family members remain dictionary
review candidates.

### Catchall Morphological Families

Catchall is reduced with the pinned Apertium-tat morphological analyzer. Words
share one task only when every form has one unambiguous analysis with the same
lemma and part of speech, and the forms have the same predicted origin.
Ambiguous and unknown words remain independent tasks. DSL-rule words, hamza
words, and contextual homonyms are routed to their dedicated projects before
family grouping and therefore cannot enter a catchall family.

The longest form represents a family; frequency and lexical order break ties.
Accepting its unchanged canonical conversion approves each exported member
with that member's own canonical conversion. Editing the representative
conversion disables propagation. A later dictionary import applies the same
rule to eligible families anchored by older directly reviewed canonical
catchall words. Derived approvals retain their source and analyzer revision in
`reviewed_word_derivations`.

## Contextual Homonym Review

The same split export writes one task per homonym occurrence to
`contextual_homonym`. Each task highlights the exact token in its sentence and
requires an `N` or `RL` decision plus the approved Zamanalif spelling.
Decisions are stored by `(sample_id, token_index)` in `contextual_reviews`;
they never approve the normalized word globally.

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
