# Җәмгыят stem: dotless ı policy

On 2026-11-18 the user selected `cämğıyät` / `cämğıyäte`, retaining the existing
`yä` conversion and changing only the stem's `i` to `ı`. This intentionally
supersedes ANTAT's spelling for this family. Original reference fixtures remain
unchanged; the two matching ANTAT cases have explicit policy exclusions.

Nine stored word reviews were migrated, including five family-derived entries.
Only the affected `zamanalif_dsl` values and their `updated_at` timestamps changed.
All other reviewed text, origins, variants, source annotations, contextual
reviews, resolutions, and derivation records were preserved. All other tables
were checked against their pre-migration hashes. All 47,630 dictionary entries
validate, the affected family derivations remain valid, and all nine migrated
spellings pass the training converter. SQLite integrity and foreign-key checks
pass.

Full database backup: `data/zamanalif_before_jamgiyat_dotless_i_20261118T122720664809Z.sqlite`.
Before/after rows and validation: `data/jamgiyat_dotless_i_migration_20261118T122720664809Z.json`.
These files are local and ignored by Git.

The test suite ran 387 tests: 381 passed and six were skipped, including two HTTP
tests because this sandbox prohibits socket creation.

No Label Studio task/import files were generated or regenerated. Existing batch
files retain their original suggestions. Future generation requires an explicit
user request.
