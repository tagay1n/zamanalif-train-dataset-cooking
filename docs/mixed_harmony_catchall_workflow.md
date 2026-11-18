# Incremental mixed-harmony catchall review

Implemented and exported on 2026-10-07, after the 371 compound recovery.

Native-labelled mixed-vowel forms now enter catchall if their native and
loanword outputs differ or either output is unavailable. Gemini's `N` label is
still a prediction. A complete human-approved spelling takes precedence during
training conversion; unreviewed origin-dependent forms remain blocked.

## Current queue

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
