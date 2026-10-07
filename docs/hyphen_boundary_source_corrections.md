# Confirmed internal-hyphen source corrections

Correction timestamp: `2026-10-07T16:36:42.632516+00:00`.

The user confirmed that all ten boundary cases identified in the hyphen
component audit are single words. Their internal ASCII hyphens were removed
from the source sentence text and matching preannotation token text in
`data/zamanalif.sqlite`.

**11 occurrences in 11 sentences** were corrected. `күзаллар-га` occurred twice;
the other nine spellings occurred once each.

## Corrected spellings

| Original normalized form | Corrected form | Occurrences |
| --- | --- | ---: |
| вәлие-ва | вәлиева | 1 |
| га-дәткә | гадәткә | 1 |
| га-мәлгә | гамәлгә | 1 |
| диван-и | дивани | 1 |
| игтибарлык-ларын | игтибарлыкларын | 1 |
| күзаллар-га | күзалларга | 2 |
| мәйдан-чыкларында | мәйданчыкларында | 1 |
| җәмә-гать | җәмәгать | 1 |
| җәраид-е | җәраиде | 1 |
| үз-агымына | үзагымына | 1 |

## Storage and verification

Only `samples.text`, `preannotation_state.tokens_json`, and the affected
`preannotation_state.updated_at` timestamps were updated. Capitalization,
surrounding punctuation, whitespace, token counts and order, token labels,
other token metadata, sample IDs, source IDs, statuses, and model provenance
were preserved.

Existing word reviews, word variants, family derivations, word resolutions,
and contextual reviews were retained unchanged. No new spelling approvals
were created. Joined forms follow the ordinary human-review precedence and
deterministic/review-required conversion rules.

The update ran in one SQLite transaction after saving the complete affected
source and preannotation rows. Every changed sentence passed ordered token
alignment checks before and after correction. All ten old exact forms are
absent from the source text and token annotations. SQLite integrity and
foreign-key checks pass. A fresh post-commit read verified all eleven changes.

The mixed-harmony exclusion count is now **8,704 unique forms**, including
**827 hyphenated forms**. Removing the ten split spellings also introduced two
previously absent unreviewed joined spellings, `дивани` and `игтибарлыкларын`;
the other still-unreviewed joined forms already existed in the excluded set.
`вәлиева` uses an existing human review, while `күзалларга` and `җәраиде`
convert deterministically.

Restorable original-row backup: `data/hyphen_boundary_source_before_20261007T163642632516Z.json` (local, ignored by Git).

Committed changes and verification manifest: `data/hyphen_boundary_source_corrections_20261007T163642632516Z.json` (local, ignored by Git).

## Changed occurrences

| Sample ID | Token index | Original token text | Corrected token text |
| --- | ---: | --- | --- |
| sent_000018 | 34 | җәраид-е | җәраиде |
| sent_004119 | 35 | га-дәткә | гадәткә |
| sent_005853 | 39 | җәмә-гать | җәмәгать |
| sent_006538 | 20 | үз-агымына | үзагымына |
| sent_007863 | 39 | күзаллар-га | күзалларга |
| sent_009758 | 15 | Вәлие-ва | Вәлиева |
| sent_010050 | 31 | игтибарлык-ларын | игтибарлыкларын |
| sent_011924 | 2 | Диван-и | Дивани |
| sent_015580 | 29 | күзаллар-га | күзалларга |
| sent_017353 | 26 | га-мәлгә | гамәлгә |
| sent_025371 | 10 | мәйдан-чыкларында | мәйданчыкларында |
