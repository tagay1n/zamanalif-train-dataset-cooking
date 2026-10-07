# Read-only mixed-harmony recovery audit

Audit timestamp: `2026-10-07T17:08:42.122729+00:00`. Database: `data/zamanalif.sqlite`.

This is the pre-recovery audit snapshot. The user subsequently authorized the
371-compound first tier, which was recovered with component provenance. The
current exclusion count is **8,333**; the remaining 105 expanded-rule candidates
were not recovered. See [the recovery record](mixed_harmony_compound_recovery.md).

**No source text, annotations, reviews, variants, resolutions, or conversion rules were changed.** The SQLite connection used `mode=ro` and `query_only`; the database SHA-256 was identical before and after the audit.

The audit starts after the confirmed ten internal-hyphen corrections: **8,704 unique excluded forms, 67,533 token occurrences**, including 827 ASCII-hyphenated forms (1,590 occurrences).

## Recovery candidates

The following rows are disjoint. They are evidence for a future recovery, not newly approved dictionary entries.

| Evidence route | Forms | Occurrences |
| --- | ---: | ---: |
| Existing reviewed/deterministic components | 346 | 697 |
| Additional compounds using existing safe family rules | 25 | 51 |
| Forms using expanded deterministic-suffix evidence | 95 | 835 |
| Additional compounds using expanded component-family evidence | 10 | 13 |
| **Total** | **476** | **1,596** |

**371 compounds / 748 occurrences** have evidence under existing component and family rules. **105 additional forms / 848 occurrences** require the experimental expanded suffix rule. Together the proposed coverage is **476 forms / 1,596 occurrences**, leaving **8,228 forms / 65,937 occurrences** excluded if every candidate is subsequently recovered.

There are no additional whole-form recoveries under the existing strict family rule; the earlier 111-form backfill already exhausted that route. There were no conflicts between proposed family and compound outputs.

These are word/token counts, not a prediction of newly exportable sentences. Other unresolved words or token alignment problems can still block a sentence.

## Expanded suffix evidence

The existing rule rejects divergent suffixes containing certain Cyrillic letters, even when their conversions are deterministic in the actual word. The expanded audit retains an unambiguous pinned-analyzer lemma/POS, literal full-lemma prefixes, compatible policies, and nonconflicting direct human origins. It then requires:

- Stable converted shared prefixes in both native and loanword branches, for both donor and candidate.
- Identical native/loanword conversion of the divergent tails, checked in the full word rather than as isolated suffix fragments.
- Transferable human spelling edits and every stored variant, including single-variant lexical overrides.
- Agreement among all qualifying direct donors; inherited approvals are never treated as independent human donors.

This produces **95 whole-form candidates / 835 occurrences**, and resolves missing components in **10 additional compounds / 13 occurrences**. **Ten of the 95 forms preserve a human correction that differs from the current automatic spelling.**

Examples:

| Candidate | Direct human source | Source spelling | Proposed spelling |
| --- | --- | --- | --- |
| дөньяга | дөнья | `dönya` | `dönyağa` |
| җәмгыятьнең | җәмгыятькә | `cämğiyätkä` | `cämğiyätneñ` |
| шагыйрьнең | шагыйрь | `şağir` | `şağirneñ` |
| вазгыятькә | вазгыять | `wazğıyät` | `wazğıyätkä` |
| дәрьяга | дәрья | `däryä` | `däryäğa` |
| имлясындагы | имля | `imlä` | `imläsındağı` |
| ядкарьләре | ядкарь | `yädqär` | `yädqärläre` |

The experimental rule has not been installed in the importer or converter. Any recovery implementation must preserve component policy variants and source provenance, and validate every resulting spelling before storing derived approvals.

## Boundaries held out

**54 hyphenated forms / 121 occurrences** have boundary clues and are excluded from the compound recovery totals. Checks include recognized joined morphology (including ambiguous analyses), joined spellings present in reviews or the annotated corpus, single-letter components, and possible inflection fragments.

The stronger checks hold out 14 compounds that the preliminary 490-candidate pass included. A joined corpus spelling is only an inspection clue: it can represent an alternative or erroneous spelling of a valid compound. The flags do **not** establish that all 54 hyphens should be removed. The earlier user confirmation applies only to the ten already corrected forms.

| Form | Occurrences | Clues |
| --- | ---: | --- |
| акъегет-задә | 1 | joined_spelling_present_in_annotated_corpus |
| буен-ча | 2 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| булуге-л-гарәб | 2 | empty_or_single_letter_component |
| бәйрәкә-тамак | 1 | joined_spelling_present_in_annotated_corpus |
| вак-төяк | 18 | joined_spelling_present_in_annotated_corpus |
| вакыйга-ләре | 1 | joined_spelling_present_in_annotated_corpus, possible_inflection_fragment |
| га-лиме | 1 | joined_spelling_has_ambiguous_morphology, joined_spelling_present_in_annotated_corpus, possible_inflection_fragment |
| га-лимуллинга | 1 | possible_inflection_fragment |
| габдел-хәким | 1 | joined_spelling_present_in_annotated_corpus |
| гаеп-леләрне | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| галим-нәргә | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| галә-л-итлак | 1 | empty_or_single_letter_component |
| гари-зага | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| гарәби-е | 1 | empty_or_single_letter_component |
| гас-кәри | 1 | joined_spelling_has_ambiguous_morphology, joined_spelling_present_in_annotated_corpus |
| гореф-гадәт-ләрен | 1 | possible_inflection_fragment |
| гореф-гадәтләренә | 12 | joined_spelling_present_in_annotated_corpus |
| гый-барәт | 1 | joined_spelling_has_ambiguous_morphology, joined_spelling_present_in_annotated_corpus |
| гый-мадиева | 1 | joined_spelling_has_review, joined_spelling_present_in_annotated_corpus |
| гыйз-зәтуллина | 1 | joined_spelling_present_in_annotated_corpus |
| гыйль-ми | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| егет-ләр-кызлар | 1 | possible_inflection_fragment |
| игъ-лан | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| иҗтимагый-икътисади | 5 | joined_spelling_present_in_annotated_corpus |
| йола-гадәтләре | 3 | joined_spelling_present_in_annotated_corpus |
| к-көнбатыш | 1 | empty_or_single_letter_component |
| ка-биләләре | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus, possible_inflection_fragment |
| кай-берләре | 1 | joined_spelling_present_in_annotated_corpus |
| кайбе-рәүләр | 1 | joined_spelling_present_in_annotated_corpus |
| камусе-л-әгъләми | 2 | empty_or_single_letter_component |
| кирәк-яраклары | 26 | joined_spelling_present_in_annotated_corpus |
| кор-бангали | 1 | joined_spelling_present_in_annotated_corpus |
| кыйм-мәт | 1 | joined_spelling_has_ambiguous_morphology, joined_spelling_present_in_annotated_corpus |
| лөгат-е | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus, empty_or_single_letter_component |
| нугай-бәк | 1 | joined_spelling_present_in_annotated_corpus |
| оеш-тыруга | 1 | joined_spelling_has_ambiguous_morphology, joined_spelling_present_in_annotated_corpus |
| оеш-тыручы | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| оеш-тыручыларыбызга | 1 | joined_spelling_has_morphology |
| оеш-тырылмавы | 1 | joined_spelling_has_morphology |
| оешты-рылмаганлыкны | 1 | joined_spelling_has_morphology |
| рабигыль-әүвәл | 1 | joined_spelling_present_in_annotated_corpus |
| сак-кисәтү | 1 | joined_spelling_present_in_annotated_corpus |
| таби-гыйлеген | 1 | joined_spelling_has_morphology |
| тасвир-и | 1 | joined_spelling_present_in_annotated_corpus, empty_or_single_letter_component |
| тасвир-лаганда | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| тирә-якта | 4 | joined_spelling_present_in_annotated_corpus |
| фи-дакарь | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| ши-гырь | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| шигырен-дәгедән | 2 | joined_spelling_has_morphology |
| шәм-балыкчы | 1 | joined_spelling_present_in_annotated_corpus |
| якты-күл | 1 | joined_spelling_present_in_annotated_corpus |
| җәмә-гатьчелек | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| һәрва-кыт | 1 | joined_spelling_has_morphology, joined_spelling_present_in_annotated_corpus |
| әхвали-л-гарәб | 2 | empty_or_single_letter_component |

## Actual converter gaps

All 8,704 forms produce nonempty native-branch DSL proposals, but nonempty Latin output does not prove that every source letter was handled. Tracing the actual converter character calls identified **29 forms / 48 occurrences** where a conditional Cyrillic letter produces an empty string:

- 26 forms lose initial `е` when whole-word harmony remains mixed.
- `игътибар-юл` loses `ю`, `шифаханә-ял` loses `я`, and `к-көнбатыш` loses `к` under whole-compound context.

Examples: `елмаеп` currently proposes `lmayıp`, `егет-кызлар` proposes `get-qızlar`, and `шифаханә-ял` proposes `şifaxanä-l`. The audit does not approve these proposals or invent replacement spellings.

Component recovery bypasses the gap for five of these forms (nine occurrences). **24 forms / 39 occurrences** remain affected after the proposed recovery. This is a concrete reason to fix conditional-letter handling before considering broad automatic native conversion.

Other native/loanword differences are not, by themselves, demonstrated converter errors. The audit groups their default renderings into 208 difference patterns. The most frequent are:

| Native → loanword difference pattern | Forms | Occurrences | Example |
| --- | ---: | ---: | --- |
| `q → k` | 1,783 | 9,212 | кадәр: `qadär` / `kadär` |
| `ı → e` | 596 | 9,082 | аерым: `ayırım` / `ayerım` |
| `ğ → g` | 1,711 | 9,056 | иҗтимагый: `ictimağıy` / `ictimagıy` |
| `yı → e` | 140 | 8,175 | буенча: `buyınça` / `buença` |
| `ğä → ga` | 514 | 6,773 | гамәлгә: `ğämälgä` / `gamälgä` |
| `ğ → gʼ` | 213 | 4,274 | мәгълүмат: `mäğlümat` / `mägʼlümat` |
| ` → ʼ; ğä → ga` | 131 | 2,926 | мөрәҗәгать: `möräcäğät` / `möräcägatʼ` |
| `w → v` | 625 | 2,654 | вазыйфаи: `wazıyfai` / `vazıyfai` |

## What remains

After the proposed 476-form recovery:

- **3,508 forms** have unique lemma/POS analyses and group into **759 families**. Of those families, 706 have no direct human-reviewed donor and 53 still have a transfer obstacle.
- **4,720 forms** lack a unique usable analysis: 3,639 unknown forms, 645 with multiple lemma/POS families, and 436 returned as multiple lexical units. These cannot be counted as 4,720 distinct lexical families.
- Among the original donor-backed but untransferable forms, 159 forms (1,503 occurrences) do not literally start with the analyzed lemma; four more have only nonliteral donors. These are opportunities to investigate stem transformations, not safe transfers under this audit.

Examples of the nonliteral route: `җәмгыяте` analyzed under `җәмгыять` has the reviewed donor `җәмгыятькә`; `шагыйре` has `шагыйрь`; `җыентыгы` has `җыентык`. A future transfer rule would have to handle the exact transformation and preserve human edits and policy variants.

The largest unresolved uniquely analyzed families illustrate why form-by-form annotation is unnecessary if family/rule decisions are adopted:

| Lemma / POS | Forms | Occurrences | Direct reviewed donors |
| --- | ---: | ---: | ---: |
| буенча / post | 1 | 7,022 | 0 |
| мәгълүмат / n | 25 | 2,142 | 0 |
| гамәл / n | 30 | 2,124 | 0 |
| оешма / n | 27 | 1,456 | 0 |
| кадәр / post | 2 | 1,410 | 0 |
| мөрәҗәгать / n | 25 | 1,100 | 0 |
| аерым / adj | 1 | 918 | 0 |
| оештыр / v | 79 | 906 | 0 |
| гариза / n | 27 | 826 | 0 |
| мәгариф / n | 13 | 652 | 0 |
| иҗтимагый / adj | 1 | 644 | 0 |
| гаилә / n | 40 | 623 | 0 |

One representative decision is not guaranteed to cover every member: a suffix or stem transformation can introduce a new ambiguity. These family totals estimate grouping opportunities, not guaranteed approval counts.

Nine soft-hyphen forms and five en-dash forms were inventoried separately. For example, the joined `дөньядан` already has a word review and `хакимият` converts independently. These separator normalization opportunities are not included in the 476-form recovery total and were not edited.

## Recommendation

Recover the 371 compounds with existing evidence first. Then implement and verify the expanded suffix guard to recover the additional 105 forms while retaining the ten human spelling corrections. Fix the actual letter-omission cases before considering an automatic native-conversion policy for the rest. Existing reviews alone do not cover the majority of the remaining vocabulary; avoiding further human decisions would require an explicit broader automation policy.

## Reproduction and evidence

Validation independently reproduced the 8,704 exclusions with the production
exporter, checked candidate and occurrence totals, reanalyzed all 95 expanded
whole-form candidates and their selected donors, verified their suffix proofs
and human corrections, and compared unchanged component spellings against the
earlier audit. The database hash still matched after these checks. Script
compilation and Git whitespace checks passed.

Run from the repository root:

```sh
.venv/bin/python tools/audit_mixed_harmony_recovery.py --output data/mixed_harmony_recovery_audit_20261007.json
```

Pinned analyzer revision: `18fe9e45d5672d6f6113291197449e7522df1b3e` (checked against the local analyzer checkout).

Database SHA-256 before and after: `cdf1148da70eb7255638f153b68e1dca2d8ba20662b61e8604c6fda0aa7cf451`.

Full evidence: `data/mixed_harmony_recovery_audit_20261007.json` (local, ignored by Git). It includes all 8,704 forms and frequencies, source contexts, donor reviews and variants, suffix proofs, component evidence, boundary flags, alternative analyses, omission traces, and grouped differences.

## Expanded whole-form candidates

| Form | Occurrences | Direct human source | Proposed preferred spelling | Human correction retained |
| --- | ---: | --- | --- | --- |
| аятьләре | 7 | аять | `ayatläre` |  |
| аятьләрен | 2 | аять | `ayatlären` |  |
| аятьләрендә | 1 | аять | `ayatlärendä` |  |
| аятьләрне | 3 | аять | `ayatlärne` |  |
| аятьләрнең | 2 | аять | `ayatlärneñ` |  |
| аятьне | 3 | аять | `ayatne` |  |
| аятьнең | 1 | аять | `ayatneñ` |  |
| баету | 24 | баетты | `bayıtu` |  |
| баетуга | 16 | баетты | `bayıtuğa` |  |
| баетуда | 7 | баетты | `bayıtuda` |  |
| баетудагы | 1 | баетты | `bayıtudağı` |  |
| баетуны | 3 | баетты | `bayıtunı` |  |
| баетуның | 1 | баетты | `bayıtunıñ` |  |
| баетучы | 3 | баетты | `bayıtuçı` |  |
| баетуы | 1 | баетты | `bayıtuı` |  |
| баетуын | 1 | баетты | `bayıtuın` |  |
| баетуына | 1 | баетты | `bayıtuına` |  |
| баетылган | 4 | баетты | `bayıtılğan` |  |
| баетылу | 1 | баетты | `bayıtılu` |  |
| баетылуы | 3 | баетты | `bayıtıluı` |  |
| баетылуын | 1 | баетты | `bayıtıluın` |  |
| баетылырга | 3 | баетты | `bayıtılırğa` |  |
| баетырга | 5 | баетты | `bayıtırğa` |  |
| баетырлык | 1 | баетты | `bayıtırlıq` |  |
| бөтендөньяга | 5 | бөтендөнья | `bötendönyağa` |  |
| вазгыятькә | 4 | вазгыять | `wazğıyätkä` | yes |
| вазгыятьне | 4 | вазгыять | `wazğıyätne` | yes |
| вазгыятьнең | 3 | вазгыять | `wazğıyätneñ` | yes |
| васыятьләрен | 6 | васыять | `wasıyätlären` |  |
| васыятьләреннән | 1 | васыять | `wasıyätlärennän` |  |
| васыятьләренә | 4 | васыять | `wasıyätlärenä` |  |
| васыятьләрне | 2 | васыять | `wasıyätlärne` |  |
| васыятьне | 1 | васыять | `wasıyätne` |  |
| вөҗданга | 1 | вөҗдансыз | `wöcdanğa` |  |
| гакыйльнең | 1 | гакыйль | `ğaqıylneñ` |  |
| гамиле | 1 | гам | `ğamile` |  |
| гамьгә | 1 | гамь | `ğämgä` |  |
| гамьле | 1 | гамь | `ğämle` |  |
| гамьсез | 4 | гамь | `ğämsez` |  |
| гомумирәк | 3 | гомуми | `ğomumiräk` |  |
| горефләрен | 1 | горефле | `ğoreflären` |  |
| дәрьяга | 3 | дәрья | `däryäğa` | yes |
| дәрьяларга | 1 | дәрья | `däryälarğa` | yes |
| дөньяга | 174 | дөнья | `dönyağa` |  |
| дөньямдагы | 1 | дөнья | `dönyamdağı` |  |
| дөньясындагы | 5 | дөнья | `dönyasındağı` |  |
| имлясындагы | 1 | имля | `imläsındağı` | yes |
| карусельгә | 1 | карусель | `karuselʼgä` |  |
| көньяктагы | 4 | көньяк | `könyaqtağı` |  |
| пакьләргә | 1 | пакь | `paqlärgä` |  |
| туннельгә | 1 | туннельнең | `tunnelʼgä` |  |
| төньяктагы | 3 | төньяк | `tönyaqtağı` |  |
| факториясендә | 1 | факторияләре | `faktoriyäsendä` |  |
| фракциясендәгегә | 1 | фракциягә | `fraktsiyäsendägegä` |  |
| хакыйкатькә | 7 | хакыйкать | `xaqıyqatkä` |  |
| хакыйкатьләре | 2 | хакыйкать | `xaqıyqatläre` |  |
| хакыйкатьләрен | 1 | хакыйкать | `xaqıyqatlären` |  |
| хакыйкатьләрне | 1 | хакыйкать | `xaqıyqatlärne` |  |
| хакыйкатьне | 34 | хакыйкать | `xaqıyqatne` |  |
| хакыйкатьнең | 7 | хакыйкать | `xaqıyqatneñ` |  |
| шагыйрьгә | 4 | шагыйрь | `şağirgä` |  |
| шагыйрьдер | 2 | шагыйрь | `şağirder` |  |
| шагыйрьдәге | 1 | шагыйрь | `şağirdäge` |  |
| шагыйрьләргә | 1 | шагыйрь | `şağirlärgä` |  |
| шагыйрьләре | 23 | шагыйрь | `şağirläre` |  |
| шагыйрьләребез | 10 | шагыйрь | `şağirlärebez` |  |
| шагыйрьләребезгә | 1 | шагыйрь | `şağirlärebezgä` |  |
| шагыйрьләребезнең | 4 | шагыйрь | `şağirlärebezneñ` |  |
| шагыйрьләренең | 5 | шагыйрь | `şağirläreneñ` |  |
| шагыйрьләреннән | 3 | шагыйрь | `şağirlärennän` |  |
| шагыйрьләренә | 1 | шагыйрь | `şağirlärenä` |  |
| шагыйрьләрне | 4 | шагыйрь | `şağirlärne` |  |
| шагыйрьләрнең | 18 | шагыйрь | `şağirlärneñ` |  |
| шагыйрьне | 9 | шагыйрь | `şağirne` |  |
| шагыйрьнең | 89 | шагыйрь | `şağirneñ` |  |
| ядкарьләре | 3 | ядкарь | `yädqärläre` | yes |
| ядкарьләрен | 4 | ядкарь | `yädqärlären` | yes |
| ядкарьләренең | 2 | ядкарь | `yädqärläreneñ` | yes |
| ядкарьләрне | 2 | ядкарь | `yädqärlärne` | yes |
| җыентыкларга | 3 | җыентык | `cıyıntıqlarğa` |  |
| җыентыктагы | 4 | җыентык | `cıyıntıqtağı` |  |
| җәмгыятьләргә | 5 | җәмгыятькә | `cämğiyätlärgä` |  |
| җәмгыятьләре | 27 | җәмгыятькә | `cämğiyätläre` |  |
| җәмгыятьләрен | 3 | җәмгыятькә | `cämğiyätlären` |  |
| җәмгыятьләрендә | 3 | җәмгыятькә | `cämğiyätlärendä` |  |
| җәмгыятьләрендәге | 1 | җәмгыятькә | `cämğiyätlärendäge` |  |
| җәмгыятьләренең | 28 | җәмгыятькә | `cämğiyätläreneñ` |  |
| җәмгыятьләреннән | 1 | җәмгыятькә | `cämğiyätlärennän` |  |
| җәмгыятьләренә | 12 | җәмгыятькә | `cämğiyätlärenä` |  |
| җәмгыятьләрне | 3 | җәмгыятькә | `cämğiyätlärne` |  |
| җәмгыятьләрнең | 10 | җәмгыятькә | `cämğiyätlärneñ` |  |
| җәмгыятьне | 18 | җәмгыятькә | `cämğiyätne` |  |
| җәмгыятьнеке | 1 | җәмгыятькә | `cämğiyätneke` |  |
| җәмгыятьнең | 111 | җәмгыятькә | `cämğiyätneñ` |  |
| җәмгыятьтәге | 32 | җәмгыятькә | `cämğiyättäge` |  |
