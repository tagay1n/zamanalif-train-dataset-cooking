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

Unresolved-origin word forms share one `unknown_origin` review project rather
than separate projects by surface type. Genuine conversion-policy cases and
contextual homonyms remain in their focused projects.

### Excluded From Project 1

- **Not mostly Tatar sentences.** If Gemini marks a sentence as
  `"tatar": false`, ignore it.
- **Already reviewed words.** If a normalized word is already present in
  `reviewed_words`, do not export it again.
- **Contextual homonyms.** Effective homonyms are excluded from every dictionary
  project, including catchall.
- **Native hamza conversions.** Verified lexical families use the global
  `HAMZA` omit/preserve policy and are routed only to the `hamza` dictionary
  project. One representative covers every observed form of the exact lexical
  family. Russian soft/hard-sign apostrophes remain separate `rus_*` cases.
- **Native-looking mixed-harmony words.** If Gemini labels a word `N` and the
  word has mixed front/back vowels, skip it for Project 1 unless it belongs to
  a verified lexical hamza family. Keep matching `RL` and `U` words.
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
contextual project on the next export. For any grouped dictionary task, only
the displayed representative is marked; hidden family members remain
dictionary review candidates.

### Dictionary Morphological Families

Every dictionary project is reduced with the pinned Apertium-tat morphological
analyzer. Words share one task only when every form has one unambiguous analysis
with the same lemma and part of speech, predicted origin, and project. Ambiguous
and unknown words remain independent tasks. Families never cross project
boundaries. Hamza keeps its stricter verified lexical-family grouping, and
contextual homonyms remain occurrence-level tasks.

Export chooses longer representatives first. A representative covers its
literal prefixes and shorter divergent siblings when divergence starts after
the full lemma and the sibling-only suffix contains none of
`вгекуцюяүщъьё`. Cyrillic `ы` is deterministic everywhere and does not make a
branch unsafe. Other divergent
branches remain separate tasks. Accepting
the unchanged canonical conversion approves each covered form with that
form's own canonical conversion. For example, `диалогларындагы` can approve
`диалогларында`, `диалог`, and `диалогларда`, but not `диалогларга`. Editing the
representative conversion can transfer a correction confined to the shared
Latin prefix, including each edited focused-project variant. A suffix-only edit
remains local. Historical reviews use the same safe-family rule. Derived
approvals retain their source and analyzer revision in
`reviewed_word_derivations`.

### Native Hamza Families

The verified families `маэмай`, `таэмин`/`тәэмин`, `тәэсир`, `мөэмин`,
`мәсьәлә`, `җөрьәт`, and `коръән` use one global `HAMZA` choice. For example,
`тәэсир` is stored as `tä{{HAMZA|omit=|preserve=ʼ}}sir`. Export groups only by
these exact lexical prefixes and resolved origin branch; it never infers hamza
from an arbitrary `э`, `ь`, or `ъ`. Importing an unchanged representative
propagates the structured policy to every matching family form while preserving
each form's canonical suffix.

## Contextual Homonym Review

The same split export automatically converts homonym occurrences whose native
and loanword branches are identical. It writes one task per remaining
origin-dependent occurrence to `contextual_homonym`. Each task highlights the
exact token in its sentence and requires an `N` or `RL` decision plus the
approved Zamanalif spelling.
Decisions are stored by `(sample_id, token_index)` in `contextual_reviews`;
they never approve the normalized word globally.

## Final Training Dataset Policy

The final training dataset should contain plain Zamanalif text, not DSL syntax.
DSL variants are an internal review/storage mechanism and must be resolved
before training export.

If an annotator rejects DSL alternatives with `-` and leaves one spelling, that
spelling is a lexical override for every global policy. It propagates only to
analyzer-confirmed safe family members, using the same shared-prefix and
conditional-suffix boundaries as ordinary family review.

Current preferred dataset policy excludes these PDF-reference policies:

- deliberate vowel-harmony restoration for words that are disharmonic in
  Cyrillic;
- special rewritten month-name spellings from the PDF.

These decisions may change, but they should be changed here first and then
reflected in code and tests.
