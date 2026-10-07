# Hyphen-component audit of remaining mixed-harmony forms

Audit date: 2026-10-07. Source: `data/zamanalif.sqlite`.

**Read-only audit snapshot:** no word reviews, preannotations, resolutions, or
conversion rules were changed by this audit. At audit time, all 8,712 forms
remained excluded pending a recovery decision.

The user subsequently confirmed all ten flagged boundaries were internal word
breaks. All eleven occurrences were joined in the source text and token
annotations. The current mixed-harmony exclusion count is 8,704, including 827
hyphenated forms. See [the source correction record](hyphen_boundary_source_corrections.md).

Of the 8,712 remaining forms, **837 contain ASCII hyphens** and 7,875 do not.
The hyphenated forms occur 1,601 times and contain 1,685 component positions
representing 1,226 distinct component spellings.

## Component results

| Component status | Distinct spellings |
| --- | ---: |
| Origin-independent conversion | 493 |
| Direct human word review | 223 |
| Existing inherited human review | 109 |
| Unreviewed origin-dependent conversion | 395 |
| Contextual homonym requiring occurrence review | 6 |

Human word reviews and stored variants take precedence over automatic
component conversion. Inherited component reviews were checked against their
source and current pinned morphology. Contextual reviews of another occurrence
were not reused as global component approvals.

## Whole-form results

| Result | Forms | Occurrences |
| --- | ---: | ---: |
| Every component already resolved | 371 | 745 |
| Some components resolved | 398 | 736 |
| No components resolved | 68 | 120 |

Among the 371 fully resolved forms, 8 have only deterministic components and
363 use at least one existing direct or inherited human review.

For unresolved components, a further check against direct human-reviewed
morphological families found 27 safely inheritable component spellings. Using
these would make **27 additional whole forms** component-resolvable, covering
78 occurrences. This uses the same literal-lemma, deterministic-divergence,
policy-compatibility, human-edit-transfer, and conflict checks as the previous
111-form recovery. No new component reviews were stored.

The component evidence therefore covers 398 whole forms in total. Recovering
371 would leave 8,341 excluded; recovering all 398 would leave 8,314 excluded.
These are prospective counts, subject to checking the hyphen boundaries below.

## Examples

| Original form | Component evidence | Composed preferred spelling |
| --- | --- | --- |
| азык-төлек | азык reviewed; төлек deterministic | `azıq-tölek` |
| вак-төяк | Both reviewed | `waq-töyäk` |
| якын-тирә | якын reviewed; тирә deterministic | `yaqın-tirä` |
| гыйнвар-февраль | Both inherited human reviews | `ğıynwar-fevralʼ` |
| кирәк-яраклары | кирәк deterministic; яраклары can inherit яраксыз review | `kiräk-yaraqları` |

`фәнни-гамәли` remains unresolved because `гамәли` has origin-dependent
conversion and no transferable human review. `аерым-аерым` has no resolved
component. Partial conversion does not make either whole form exportable.

Composed preferred spellings differ from the current native whole-word
suggestion for 28 of the 371 forms. This can reflect human origins or spellings
and component-boundary effects. All generated preferred strings pass the
training export plain-text validator; they have not been approved as new
whole-form reviews.

## Boundaries requiring closer inspection

Component readiness alone does not establish that a hyphen separates independent
words. Some forms appear to contain inserted word-break hyphens or suffix
fragments. The following 10 candidates were flagged by a reviewed joined form,
a recognized joined morphology, a single-letter component, or corpus inspection.
These clues are not proof that the hyphen should be removed, and this list may
be incomplete.

| Form | Joined spelling | Clue |
| --- | --- | --- |
| вәлие-ва | вәлиева | Joined form already reviewed |
| га-дәткә | гадәткә | Joined analyzer lemma: гадәт |
| га-мәлгә | гамәлгә | Joined analyzer lemma: гамәл |
| диван-и | дивани | Single-letter component |
| игтибарлык-ларын | игтибарлыкларын | Suffix-like fragment in corpus context |
| күзаллар-га | күзалларга | Joined analyzer lemma: күзалла |
| мәйдан-чыкларында | мәйданчыкларында | Joined analyzer lemma: мәйданчык |
| җәмә-гать | җәмәгать | Joined analyzer lemma: җәмәгать |
| җәраид-е | җәраиде | Single-letter component |
| үз-агымына | үзагымына | Joined analyzer lemma: үзагым |

In `sent_025371`, `мәйдан-чыкларында` occurs alongside unsplit
`мәйданчыкларында` in the same sentence. The joined analyzer lemma is
`мәйданчык`, while analyzing the isolated component `чыкларында` assigns
it to a different family. This is a concrete reason to check that boundary
before propagating a component review.

Nine further remaining forms contain soft hyphens and five use an en dash.
They were listed separately in the detailed report and were not split as ASCII
hyphen compounds in the counts above.

Full local report: `data/mixed_harmony_hyphen_component_audit_20261007T111256518413Z.json` (ignored by Git). It lists all 837 forms,
their components and statuses, composed suggestions, available family sources,
review variants, and boundary clues.

Pinned analyzer revision: `18fe9e45d5672d6f6113291197449e7522df1b3e`.
