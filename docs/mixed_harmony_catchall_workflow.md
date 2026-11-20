# Incremental mixed-harmony catchall review

Implemented and exported on 2026-10-07, after the 371 compound recovery.

Native-labelled mixed-vowel forms now enter catchall if their native and
loanword outputs differ or either output is unavailable. Gemini's `N` label is
still a prediction. A complete human-approved spelling takes precedence during
training conversion; unreviewed origin-dependent forms remain blocked.

## Reviewed rounds on 2026-11-18

Projects 55 (Catchall #35) and 56 (Catchall #36) were downloaded, audited,
corrected through annotator discussion, rehearsed on database copies, and
imported after creating project-specific SQLite backups.

| Project | Direct reviews | Family approvals propagated | Reviewed words after import |
| --- | ---: | ---: | ---: |
| 55 | 500 | 1,802 | 49,928 |
| 56 | 500 | 277 | 50,705 |

Project 55 included 14 spelling corrections and completion of the skipped
`гаетләрдә -> ğayetlärdä` task. Project 56 included 14 spelling corrections.
All imported spellings were checked against their finalized review files, the
complete reviewed dictionary validated, and SQLite integrity and foreign-key
checks passed. Existing direct reviews were preserved. Project 55 refreshed
four older inferred `шагыйрь` forms; project 56 replaced no existing reviews.

The decisions below are human lexical approvals, not new global converter
rules. They take precedence for the reviewed words and analyzer-confirmed safe
family members:

- Keep written `а -> a` in reviewed mixed-vowel forms, including the
  `мәгариф`, `гаскәр`, `сәнгать`, and `җәмәгать` examples.
- Retain reviewed `yä` choices in the `кыяфәт`, `җинаять`, `шикаять`,
  `риваять`, `хыянәт`, `кыямәт`, and `җәмгыят` families. The latter retains
  dotless `ı`: `cämğıyät`. These approvals coexist with the current automatic
  mixed-vowel policy; they do not authorize a blanket `я -> yä` replacement.
- Use reviewed `гаеп -> ğayep` and `гает -> ğayet` spellings without changing
  the general `ае -> ayı` rule used by words such as `аерым -> ayırım`.
- Preserve compound components in `җидееллык -> cideyıllıq`,
  `берьюлы -> beryulı`, `төбәкара -> töbäkara`, and
  `торбаүткәргечләр -> torbaütkärgeçlär`. The source variant `жидееллык`
  retains its written initial consonant: `jideyıllıq`.
- Retain `мөстәкыйль -> möstäqıyl` without an apostrophe before suffixes,
  surname `v` before Tatar suffixes, and the reviewed name spelling
  `Гафиятуллин/Гафиятуллина -> ğafiyatullin/ğafiyatullina`.
- Approve `казые -> qazıyı`, `уе -> uyı`, and `абруе -> abruyı`.
  Stored ANTAT evidence includes `абруеңны -> abruyıñnı` and
  `буе -> buyı`; the exact three target forms were not found there.
- Approve `Варкләд-Бодья -> varkläd-bodʼya` as an explicit proper-name
  decision. The [official bilingual catalogue](https://fpd.tatarstan.ru/file/fpd/File/%D0%90%D0%B3%D1%80%D1%8B%D0%B7%D1%81%D0%BA%D0%B8%D0%B9%20%D1%80%D0%B0%D0%B9%D0%BE%D0%BD.%20%D3%98%D0%B3%D0%B5%D1%80%D2%97%D0%B5%20%D1%80%D0%B0%D0%B9%D0%BE%D0%BD%D1%8B%281%29.pdf)
  attests the Tatar `ә`, while the
  [field study](https://ojs.utlib.ee/index.php/JEF/article/view/22721)
  uses `Varkled-Böd’ya`. The approved dataset spelling combines that evidence
  with the loanword sign convention; no Tatar pronunciation recording was
  verified. The exported origin label was preserved.

The importer compatibility fix is committed as `5dede7e`. It preserves
historical human reviews when origin branches now agree, while retaining
corpus, origin, routing, and homonym checks. Fresh exports still omit those
origin-independent words. The fix passed 392 tests, with four skips.

Raw downloads, working annotation files, decision histories, import results,
and SQLite backups remain local under ignored `data/`. The round summaries
are `data/labelstudio_project_55_review_20261118.md` and
`data/labelstudio_project_56_review_20261118.md`.

The annotator requested the next round as one file of 1,000 tasks, overriding
the usual 500-task default for this export:

```sh
.venv/bin/python -m tatar_preannotator annotation-export \
  --db data/zamanalif.sqlite \
  --output-dir labelstudio_projects/after_project56_20261118_1000 \
  --max-items 1000 --batch-size 1000
```

## Project 57 review on 2026-11-19

All 1,000 entries in Catchall #37 were audited, including the 907 submissions
that accepted their suggestions unchanged. The audit identified 12 proposed
corrections and two further name choices; every flagged case was discussed.
The finalized batch has eight spelling corrections and six explicitly retained
spellings. No global converter rules were changed.

The user approved these lexical decisions:

- Keep standalone `Гафият -> ğafiyät`. Correct the older direct review
  `Гафиятовка -> ğafiyatovqa`, while preserving the earlier
  `Гафиятуллин/Гафиятуллина` approvals. These are separate reviewed names,
  not a blanket family-wide vowel replacement.
- Retain submitted `iye` in `bahawiyev`, `möxämmätğaliyeva`,
  `möxämmätğaliyev`, `ğaziyev`, and `ğayniyev`. Historical reconciliation
  is deferred: the pre-import dictionary contained 32 direct bare-surname
  reviews using `ie` and 23 using `iye`. The automatic exact-surname-ending
  policy was not changed, and earlier approvals were not migrated.
- Use `җинаятьләрне -> cinayätlärne` and
  `жәмгыятеннән -> jämğıyätennän`, preserving source `ж -> j`.
- Use `тәрәккыятебез -> täräqqıyätebez`, `сабантуе -> sabantuyı`,
  `таткультресурсүзәге -> tatkulʼtresursüzäge`, and `Мукден -> mukden`.
- Use `Галәветдинов -> ğaläwetdinov` and `Кавиев -> qawiyev`.
  Confirm the existing `Кави -> qawi` review and correct the older direct
  `Кавиева` review to `qawiyeva`.

The two older direct-review corrections each had a separate SQLite backup and
changed only the requested spelling and its timestamp, preserving origins and
all other rows. The finalized project then passed a full import rehearsal and
was imported after a project-specific backup:

| Direct reviews | Family approvals propagated | Reviewed words after import |
| ---: | ---: | ---: |
| 1,000 | 137 | 51,842 |

All 1,000 imported spellings match the finalized working file, the complete
reviewed dictionary validates, and SQLite integrity and foreign-key checks
pass. The project import preserved all existing reviews and source tables.
The archived hosted project and original download were left unchanged.

The local audit, all-entry coverage record, decisions, and import result are
`data/labelstudio_project_57_review_20261119.md`,
`data/labelstudio_project_57_full_coverage_20261119.json`,
`data/labelstudio_project_57_review_decisions.json`, and
`data/labelstudio_project_57_import_result.json`. The project backup is
`data/zamanalif_before_project57_import_20261119T162252Z.sqlite`.

Generate the next requested round as one file of 1,000 tasks:

```sh
.venv/bin/python -m tatar_preannotator annotation-export \
  --db data/zamanalif.sqlite \
  --output-dir labelstudio_projects/after_project57_20261119_1000 \
  --max-items 1000 --batch-size 1000
```

## Project 58 review on 2026-11-20

All 1,000 entries in Catchall #38 were audited, including 840 unchanged
submissions and three skipped entries. All flagged groups were discussed.
The finalized file has 13 spelling corrections, two explicitly confirmed
spellings, and two completed skipped entries. The third skipped entry was
resolved by repairing its source typo. No global converter rules were changed.

The user approved these lexical decisions:

- Complete `консидеранның -> konsiderannıñ` and
  `чишмә-кнс -> çişmä-kns`, retaining the acronym without expansion.
- Repair `гаее -> гаебе` in sample `sent_014112` and token 29 of its
  preannotation, reusing the existing `гаебе -> ğayebe` review. Keep the
  corrupt-word task cancelled instead of adding a `гаее` dictionary entry.
- Use `корьән -> qorʼän`, preserving the hamza and the source spelling.
- Use `музей-тыюлык -> muzey-tıyulıq`,
  `сәнгатькярләребез -> sänğatkyärlärebez`, and
  `бүлекара -> bülekara`, preserving component consonants.
- Use `унъеллыгында -> unyıllığında`, matching the recent
  `унъеллыкта -> unyıllıqta` approval. Older inconsistent reviews remain
  deferred, along with the previously deferred surname `ie`/`iye` choices.
- Use `хәятына -> xäyätına` and `хәятының -> xäyätınıñ`, retaining
  their submitted suffixes and the reviewed `хәят -> xäyät` stem.
- Use `әдәбиятчы-галим -> ädäbiyatçı-ğalim`, preserving the documented
  `әдәбият -> ädäbiyat` exception.
- Normalize ASCII apostrophes to U+02BC in
  `актуальләштерелә -> aktualʼläşterelä` and `барьева -> barʼyeva`.
- Use `балтаевага -> baltayevağa` and `г.исхакый -> ğ.isxaqıy`.
- Keep `никяхларының -> niqäxlarınıñ`, consistent with the existing
  `никях -> niqäx` and `никях-талак -> niqäx-talaq` reviews, separately
  from the reviewed `никах -> niqax` spelling.
- Keep `сырдәрья -> sırdäryä` and use `амудәрья -> amudäryä`, matching
  the existing `дәрья -> däryä` review in both river names.

The source repair had its own backup and changed exactly one sample row and
one preannotation row. After that repair, the finalized project passed an
import rehearsal and was imported after another database backup:

| Direct reviews | Family approvals propagated | Reviewed words after import |
| ---: | ---: | ---: |
| 999 | 11 | 52,852 |

All 999 imported spellings and origins match the finalized working file.
The complete reviewed dictionary validates, SQLite integrity and foreign-key
checks pass, and the main database matches the rehearsal. The import preserved
every existing review and all source tables. The raw download is unchanged.

Local artifacts under ignored `data/` include
`labelstudio_project_58_tasks_reviewed.json`,
`labelstudio_project_58_review_decisions.json`,
`labelstudio_project_58_final_coverage.json`, and
`labelstudio_project_58_final_import_validation.json`.
The source repair is recorded in `gaee_source_repair_20261120T160744Z.json`;
the final import backup is
`zamanalif_before_project58_import_20261120T163030Z.sqlite`.

Generate the next requested round as one file of 1,000 tasks:

```sh
.venv/bin/python -m tatar_preannotator annotation-export \
  --db data/zamanalif.sqlite \
  --output-dir labelstudio_projects/after_project58_20261120_1000 \
  --max-items 1000 --batch-size 1000
```

The exported `project_catchall_batch_001_of_001.json` contains exactly 1,000
unique tasks with context, using catchall schema version 3. It has no overlap
with reviewed words, project 58, or the preceding three completed batch
exports. Serialized task validation passed and the database hash is unchanged.
The single blank suggestion is `к-көнбатыш`, whose isolated initial `к`
requires a human spelling decision. Export validation is recorded in
`data/after_project58_20261120_1000_validation.json`.

## Initial queue snapshot

| Item | Count |
| --- | ---: |
| Previously excluded forms | 8,333 |
| Now origin-independent after converter fixes | 51 |
| Former exclusions still requiring review | 8,282 |
| Additional hyphenated forms now requiring review | 55 |
| Total forms covered by catchall | 8,337 |
| Grouped dictionary tasks | 6,112 |
| Files of at most 500 tasks | 13 |

The additional 55 forms previously had equal origin branches because conversion
context crossed a hyphen. Separate component conversion exposes different
branches; they now require review too. The 51 automatic forms require no new
human decision because their complete origin branches agree.

The export is in `labelstudio_projects/mixed_harmony_catchall_20261007`.
Start with `project_catchall_batch_001_of_013.json`, containing exactly 500
entries. The directory includes catchall instructions and the export report.
Source excerpts accompany the displayed words. There are no pending contextual
homonym tasks in this snapshot.

## Review loop

Import completed Label Studio reviews, then generate a fresh queue before
starting the next batch:

```sh
.venv/bin/python -m tatar_preannotator annotation-import \
  --db data/zamanalif.sqlite \
  --input /path/to/completed_labelstudio_export.json

.venv/bin/python -m tatar_preannotator annotation-export \
  --db data/zamanalif.sqlite \
  --output-dir labelstudio_projects/next_review_batch \
  --max-items 500 --batch-size 500
```

Use the newly generated catchall file each round. The initial 13 files are a
snapshot; later files can contain words resolved by an earlier import. Exporting
alone does not mark anything reviewed. Partial completed exports are supported.

Imports propagate human shared-prefix spelling edits within analyzer-confirmed
families and backfill eligible forms from older direct reviews. The full lemma
must be shared literally, with no guessed stem alternation. Conditional letters
in divergent suffixes can transfer only when both origin branches preserve the
shared converted prefix and have identical tails for both source and candidate,
with compatible convention policies. Suffix-only human edits remain local.

Catchall displays a preferred plain spelling even for a canonical suggestion
with policy choices. Approving or editing it stores a single-spelling lexical
override with the preferred policy mapping retained for family transfer.
A human review of a word already resolved by family or compound evidence
replaces the derivation. Conflicting direct human reviews still require
resolution. Family and compound provenance remain recorded for inferred forms.

## Converter correction and verification

Initial `е` uses local vowel context when the remaining vowels are mixed.
Hyphen components convert independently, retaining component lexical conventions
such as the attested `ерзя` spelling. An unresolved conditional letter makes the
whole suggestion unavailable instead of silently disappearing.

Of the 29 audited omission forms, 28 now have complete proposals. `к-көнбатыш`
has a blank proposal because its isolated `к` cannot be resolved safely; the
annotator must supply a complete spelling. These are proposals, not new reviews.

All 371 recovered compound approvals still validate with their exact stored
spellings and provenance. Export preserved the database hash; no source or
review rows were changed. The regression suite ran 387 tests successfully,
with four existing skips. The actual exported JSON files passed task validation
and the 500-entry size limit.

Detailed queue membership, the 51 automatic forms, changes for all 55 additional
forms, omission proposals, and the export report are recorded in
`data/mixed_harmony_catchall_queue_20261007.json` (local, ignored by Git).
