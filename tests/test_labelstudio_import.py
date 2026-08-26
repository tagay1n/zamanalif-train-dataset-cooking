from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.morphology_fakes import FakeMorphologyAnalyzer
from tatar_preannotator.cli import main
from tatar_preannotator.contextual_review import (
    export_contextual_tasks_from_db,
    load_contextual_reviews,
)
from tatar_preannotator.labelstudio_import import (
    LabelStudioImportError,
    audit_labelstudio_export,
    import_labelstudio_annotations,
    parse_labelstudio_export,
)
from tatar_preannotator.word_export import (
    export_labelstudio_project_tasks_from_db,
    load_reviewed_words,
    save_reviewed_word,
)


class LabelStudioImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = FakeMorphologyAnalyzer()
        self._analyzer_patch = patch(
            "tatar_preannotator.labelstudio_import.default_morphology_analyzer",
            return_value=self.analyzer,
        )
        self._cli_analyzer_patch = patch(
            "tatar_preannotator.cli.default_morphology_analyzer",
            return_value=self.analyzer,
        )
        self._analyzer_patch.start()
        self._cli_analyzer_patch.start()
        self.addCleanup(self._analyzer_patch.stop)
        self.addCleanup(self._cli_analyzer_patch.stop)

    def test_imports_strict_dictionary_backup_and_skips_unannotated(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            backup = _backup(
                root / "backup.json",
                [_task("вакыт", "waqıt", "N"), _task("проект", "proyekt", "RL", [])],
            )

            summary = import_labelstudio_annotations(db_path, backup)
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(summary.project_key, "catchall")
        self.assertEqual(summary.imported_items, 1)
        self.assertEqual(summary.skipped_unannotated_tasks, 1)
        self.assertEqual(set(reviewed), {"вакыт"})
        self.assertEqual(reviewed["вакыт"].origin, "N")

    def test_focused_variant_editor_preserves_policies_and_corrections(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, ["проект", "проекты"], origin="RL")
            exported = export_labelstudio_project_tasks_from_db(
                db_path,
                morphology_analyzer=self.analyzer,
            )
            tasks = exported.projects["e_glide"].tasks
            for task in tasks:
                visible = task["data"]["zamanalif_variants"]
                reviewed = (
                    visible.replace("pro", "pra")
                    if task["data"]["cyrl_word"] == "проекты"
                    else visible
                )
                task["annotations"] = [
                    {
                        "was_cancelled": False,
                        "result": [
                            {
                                "from_name": "reviewed_zamanalif_variants",
                                "type": "textarea",
                                "value": {"text": [reviewed]},
                            }
                        ],
                    }
                ]
            backup = _backup(root / "e-glide.json", tasks)

            audit = audit_labelstudio_export(db_path, backup)
            summary = import_labelstudio_annotations(db_path, backup)
            repeated = import_labelstudio_annotations(db_path, backup)
            reviewed_words = load_reviewed_words(db_path)

        self.assertEqual(audit.unchanged_tasks, 1)
        self.assertEqual(audit.conversion_changes, 1)
        self.assertEqual(summary.imported_items, 2)
        self.assertEqual(repeated.unchanged_items, 2)
        self.assertEqual(
            reviewed_words["проект"].zamanalif_dsl,
            "pro{{E_GLIDE|plain=e|glide=ye}}kt",
        )
        self.assertEqual(
            tuple(
                variant.zamanalif
                for variant in reviewed_words["проекты"].variants
            ),
            ("prayektı", "praektı"),
        )

    def test_focused_family_import_propagates_safe_variant_edits(self) -> None:
        words = ["проект", "проекты", "проектын", "проекте"]
        analyzer = FakeMorphologyAnalyzer(
            {word: ("проект", "n") for word in words}
        )
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, words, origin="RL")
            exported = export_labelstudio_project_tasks_from_db(
                db_path,
                sort_by="word",
                morphology_analyzer=analyzer,
            )
            task = next(
                task
                for task in exported.projects["e_glide"].tasks
                if task["data"]["cyrl_word"] == "проектын"
            )
            task["annotations"] = [
                {
                    "was_cancelled": False,
                    "result": [
                        {
                            "from_name": "reviewed_zamanalif_variants",
                            "type": "textarea",
                            "value": {
                                "text": [
                                    task["data"]["zamanalif_variants"].replace(
                                        "pro", "pra"
                                    )
                                ]
                            },
                        }
                    ],
                }
            ]

            summary = import_labelstudio_annotations(
                db_path,
                _backup(root / "e-glide-family.json", [task]),
                morphology_analyzer=analyzer,
            )
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(summary.imported_items, 1)
        self.assertEqual(summary.inherited_items, 2)
        self.assertEqual(set(reviewed), {"проект", "проекты", "проектын"})
        self.assertNotIn("проекте", reviewed)
        self.assertEqual(
            tuple(variant.zamanalif for variant in reviewed["проект"].variants),
            ("prayekt", "praekt"),
        )
        self.assertEqual(
            tuple(variant.zamanalif for variant in reviewed["проекты"].variants),
            ("prayektı", "praektı"),
        )

    def test_rejected_variant_becomes_family_wide_lexical_override(self) -> None:
        words = ["проект", "проектын"]
        analyzer = FakeMorphologyAnalyzer(
            {word: ("проект", "n") for word in words}
        )
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, words, origin="RL")
            exported = export_labelstudio_project_tasks_from_db(
                db_path,
                morphology_analyzer=analyzer,
            )
            task = exported.projects["e_glide"].tasks[0]
            variants = task["data"]["zamanalif_variants"].splitlines()
            task["annotations"] = [
                {
                    "was_cancelled": False,
                    "result": [
                        {
                            "from_name": "reviewed_zamanalif_variants",
                            "type": "textarea",
                            "value": {"text": [f"{variants[0]}\n-"]},
                        }
                    ],
                }
            ]

            summary = import_labelstudio_annotations(
                db_path,
                _backup(root / "collapsed-family.json", [task]),
                morphology_analyzer=analyzer,
            )
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(summary.inherited_items, 1)
        self.assertEqual(reviewed["проектын"].zamanalif_dsl, "proyektın")
        self.assertEqual(reviewed["проект"].zamanalif_dsl, "proyekt")
        self.assertEqual(
            tuple(variant.zamanalif for variant in reviewed["проект"].variants),
            ("proyekt",),
        )

    def test_rejecting_every_variant_is_invalid(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, ["проект"], origin="RL")
            task = export_labelstudio_project_tasks_from_db(
                db_path,
                morphology_analyzer=self.analyzer,
            ).projects["e_glide"].tasks[0]
            task["annotations"] = [
                {
                    "was_cancelled": False,
                    "result": [
                        {
                            "from_name": "reviewed_zamanalif_variants",
                            "type": "textarea",
                            "value": {"text": ["-\n-"]},
                        }
                    ],
                }
            ]

            with self.assertRaisesRegex(
                LabelStudioImportError,
                "must retain at least one variant",
            ):
                import_labelstudio_annotations(
                    db_path,
                    _backup(root / "all-rejected.json", [task]),
                    morphology_analyzer=self.analyzer,
                )

    def test_reimport_is_idempotent_and_conflict_rolls_back(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            backup = _backup(root / "backup.json", [_task("вакыт", "waqıt", "N")])
            first = import_labelstudio_annotations(db_path, backup)
            second = import_labelstudio_annotations(db_path, backup)
            conflict_task = _task("вакыт", "vaqıt", "RL")
            conflict_task["data"]["gemini_origin"] = "N"
            conflict = _backup(root / "conflict.json", [conflict_task])
            with self.assertRaisesRegex(LabelStudioImportError, "reviewed word conflict"):
                import_labelstudio_annotations(db_path, conflict)

        self.assertEqual(first.imported_items, 1)
        self.assertEqual(second.unchanged_items, 1)

    def test_canonical_catchall_family_imports_every_member_with_provenance(self) -> None:
        analyzer = FakeMorphologyAnalyzer(
            {
                "торган": ("тор", "v"),
                "торганда": ("тор", "v"),
                "торганнар": ("тор", "v"),
                "торганнары": ("тор", "v"),
            }
        )
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(
                db_path,
                ["торган", "торганда", "торганнар", "торганнары"],
            )
            task = _task(
                "торганнары",
                "torğannarı",
                "N",
            )

            summary = import_labelstudio_annotations(
                db_path,
                _backup(root / "family.json", [task]),
                morphology_analyzer=analyzer,
            )
            reviewed = load_reviewed_words(db_path)
            with sqlite3.connect(db_path) as conn:
                derivations = conn.execute(
                    """
                    select normalized_word, source_word, lemma, part_of_speech
                    from reviewed_word_derivations
                    order by normalized_word
                    """
                ).fetchall()

        self.assertEqual(summary.imported_items, 1)
        self.assertEqual(summary.inherited_items, 3)
        self.assertEqual(summary.inherited_current_batch_items, 3)
        self.assertEqual(summary.inherited_backfill_items, 0)
        self.assertEqual(summary.inherited_literal_subword_items, 2)
        self.assertEqual(summary.inherited_deterministic_divergent_items, 1)
        self.assertEqual(summary.inherited_source_families, 1)
        self.assertEqual(
            set(reviewed),
            {"торган", "торганда", "торганнар", "торганнары"},
        )
        self.assertEqual(
            derivations,
            [
                ("торган", "торганнары", "тор", "v"),
                ("торганда", "торганнары", "тор", "v"),
                ("торганнар", "торганнары", "тор", "v"),
            ],
        )
        self.assertEqual(len(analyzer.calls), 1)

    def test_edited_representative_does_not_propagate(self) -> None:
        analyzer = FakeMorphologyAnalyzer(
            {
                "торган": ("тор", "v"),
                "торганда": ("тор", "v"),
                "торганнары": ("тор", "v"),
            }
        )
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, ["торган", "торганда", "торганнары"])
            task = _task(
                "торганнары",
                "torğannarı",
                "N",
            )
            task["annotations"][0]["result"][0]["value"]["text"] = ["torğannarıx"]

            summary = import_labelstudio_annotations(
                db_path,
                _backup(root / "edited.json", [task]),
                morphology_analyzer=analyzer,
            )
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(summary.inherited_items, 0)
        self.assertEqual(set(reviewed), {"торганнары"})

    def test_shared_stem_correction_propagates_to_safe_family_members(self) -> None:
        words = ["казакларны", "казакларын", "казакларның", "казакларының"]
        analyzer = FakeMorphologyAnalyzer(
            {word: ("казак", "n") for word in words}
        )
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, words)
            genitive_task = _task("казакларның", "qazaqlarnıñ", "N")
            genitive_task["annotations"][0]["result"][0]["value"]["text"] = [
                "kazaklarnıñ"
            ]
            possessive_task = _task("казакларының", "qazaqlarınıñ", "N")
            possessive_task["annotations"][0]["result"][0]["value"]["text"] = [
                "kazaklarınıñ"
            ]

            summary = import_labelstudio_annotations(
                db_path,
                _backup(
                    root / "kazak-family.json",
                    [genitive_task, possessive_task],
                ),
                morphology_analyzer=analyzer,
            )
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(summary.inherited_items, 2)
        self.assertEqual(reviewed["казакларны"].zamanalif_dsl, "kazaklarnı")
        self.assertEqual(reviewed["казакларын"].zamanalif_dsl, "kazakların")

    def test_new_direct_correction_refreshes_an_older_inherited_review(self) -> None:
        words = ["өязендә", "өязендәдер", "өязенең"]
        analyzer = FakeMorphologyAnalyzer({word: ("өяз", "n") for word in words})
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, words, origin="RL")

            import_labelstudio_annotations(
                db_path,
                _backup(
                    root / "first.json",
                    [_task("өязендәдер", "öyazendäder", "RL")],
                ),
                morphology_analyzer=analyzer,
            )
            corrected = _task("өязенең", "öyazeneñ", "RL")
            corrected["annotations"][0]["result"][0]["value"]["text"] = [
                "öyäzeneñ"
            ]

            summary = import_labelstudio_annotations(
                db_path,
                _backup(root / "second.json", [corrected]),
                morphology_analyzer=analyzer,
            )
            reviewed = load_reviewed_words(db_path)
            with sqlite3.connect(db_path) as conn:
                source = conn.execute(
                    """
                    select source_word from reviewed_word_derivations
                    where normalized_word = 'өязендә'
                    """
                ).fetchone()

        self.assertEqual(summary.inherited_items, 1)
        self.assertEqual(reviewed["өязендә"].zamanalif_dsl, "öyäzendä")
        self.assertEqual(source, ("өязенең",))

    def test_historical_shared_stem_correction_backfills_prefix_member(self) -> None:
        analyzer = FakeMorphologyAnalyzer(
            {
                "казакларны": ("казак", "n"),
                "казакларның": ("казак", "n"),
            }
        )
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, ["казакларны", "казакларның"])
            save_reviewed_word(db_path, "казакларның", "kazaklarnıñ", "N")

            summary = import_labelstudio_annotations(
                db_path,
                _backup(root / "next.json", [_task("вакыт", "waqıt", "N")]),
                morphology_analyzer=analyzer,
            )
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(summary.inherited_backfill_items, 1)
        self.assertEqual(reviewed["казакларны"].zamanalif_dsl, "kazaklarnı")

    def test_direct_family_review_outranks_inferred_conversion(self) -> None:
        analyzer = FakeMorphologyAnalyzer(
            {
                "идеяләрендә": ("идея", "n"),
                "идеяләренә": ("идея", "n"),
            }
        )
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, ["идеяләрендә", "идеяләренә"], origin="RL")
            edited = _task("идеяләренә", "ideyalärenä", "RL")
            edited["annotations"][0]["result"][0]["value"]["text"] = [
                "ideyälärenä"
            ]

            summary = import_labelstudio_annotations(
                db_path,
                _backup(
                    root / "direct-family.json",
                    [
                        _task("идеяләрендә", "ideyalärendä", "RL"),
                        edited,
                    ],
                ),
                morphology_analyzer=analyzer,
            )
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(summary.imported_items, 2)
        self.assertEqual(reviewed["идеяләренә"].zamanalif_dsl, "ideyälärenä")

    def test_next_dictionary_import_backfills_safe_divergent_members(self) -> None:
        analyzer = FakeMorphologyAnalyzer(
            {
                "торган": ("тор", "v"),
                "торганда": ("тор", "v"),
                "торганнар": ("тор", "v"),
                "торганнары": ("тор", "v"),
            }
        )
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(
                db_path,
                ["торган", "торганда", "торганнар", "торганнары"],
            )
            save_reviewed_word(db_path, "торганнары", "torğannarı", "N")

            summary = import_labelstudio_annotations(
                db_path,
                _backup(root / "next.json", [_task("вакыт", "waqıt", "N")]),
                morphology_analyzer=analyzer,
            )
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(summary.imported_items, 1)
        self.assertEqual(summary.inherited_items, 3)
        self.assertEqual(
            set(reviewed),
            {"вакыт", "торган", "торганда", "торганнар", "торганнары"},
        )

    def test_imported_branch_propagates_only_to_safe_divergent_member(self) -> None:
        words = [
            "диалог",
            "диалоглар",
            "диалоглары",
            "диалогларын",
            "диалогларында",
            "диалогларындагы",
            "диалогта",
            "диалогларга",
        ]
        analyzer = FakeMorphologyAnalyzer(
            {word: ("диалог", "n") for word in words}
        )
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, words, origin="RL")
            task = _task(
                "диалогларындагы",
                "dialoglarındağı",
                "RL",
            )

            summary = import_labelstudio_annotations(
                db_path,
                _backup(root / "prefix-family.json", [task]),
                morphology_analyzer=analyzer,
            )
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(summary.imported_items, 1)
        self.assertEqual(summary.inherited_items, 6)
        self.assertTrue(
            {
                "диалог",
                "диалоглар",
                "диалоглары",
                "диалогларын",
                "диалогларында",
                "диалогларындагы",
                "диалогта",
            }
            <= set(reviewed)
        )
        self.assertNotIn("диалогларга", reviewed)

    def test_hamza_import_propagates_policy_to_entire_lexical_family(self) -> None:
        words = ["тәэсир", "тәэсире", "тәэсирле", "тәэсирләр"]
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, words)
            task = _task(
                "тәэсир",
                "tä{{HAMZA|omit=|preserve=ʼ}}sir",
                "N",
                project_key="hamza",
            )

            summary = import_labelstudio_annotations(
                db_path,
                _backup(root / "hamza-family.json", [task]),
            )
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(summary.imported_items, 1)
        self.assertEqual(summary.inherited_items, 3)
        self.assertEqual(set(reviewed) & set(words), set(words))
        self.assertEqual(
            reviewed["тәэсирле"].zamanalif_dsl,
            "tä{{HAMZA|omit=|preserve=ʼ}}sirle",
        )

    def test_homonym_annotation_routes_word_to_contextual_export_idempotently(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            task = _task(
                "вакыт",
                "waqıt",
                "N",
                annotations=[_homonym_annotation()],
            )
            backup = _backup(root / "homonym.json", [task])

            audit = audit_labelstudio_export(db_path, backup)
            first = import_labelstudio_annotations(db_path, backup)
            second = import_labelstudio_annotations(db_path, backup)
            contextual = export_contextual_tasks_from_db(db_path)
            reviewed = load_reviewed_words(db_path)
            with sqlite3.connect(db_path) as conn:
                resolutions = dict(
                    conn.execute(
                        "select normalized_word, decision from word_resolutions"
                    ).fetchall()
                )

        self.assertEqual(audit.homonym_changes, 1)
        self.assertTrue(audit.changes[0].is_homonym)
        self.assertEqual(first.imported_items, 1)
        self.assertEqual(first.homonym_items, 1)
        self.assertEqual(second.imported_items, 0)
        self.assertEqual(second.unchanged_items, 1)
        self.assertEqual(resolutions["вакыт"], "contextual_homonym")
        self.assertNotIn("вакыт", reviewed)
        self.assertEqual(
            [task["data"]["cyrl_word"] for task in contextual.tasks],
            ["вакыт"],
        )

    def test_homonym_overrides_resolution_ignores_hidden_values_and_not_family(self) -> None:
        analyzer = FakeMorphologyAnalyzer(
            {
                "торган": ("тор", "v"),
                "торганнар": ("тор", "v"),
                "торганнары": ("тор", "v"),
            }
        )
        retained = _homonym_annotation()
        retained["result"].extend(
            [
                {
                    "from_name": "corrected_zamanalif",
                    "type": "choices",
                    "value": {"choices": []},
                },
            ]
        )
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_words(db_path, ["торган", "торганнар", "торганнары"])
            initial_task = _task(
                "торганнары",
                "torğannarı",
                "N",
            )
            import_labelstudio_annotations(
                db_path,
                _backup(root / "initial-family.json", [initial_task]),
                morphology_analyzer=analyzer,
            )
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    insert into word_resolutions(normalized_word, decision, updated_at)
                    values ('торганнары', 'N', 'now')
                    """
                )
            task = _task(
                "торганнары",
                "torğannarı",
                "N",
                annotations=[retained],
            )

            summary = import_labelstudio_annotations(
                db_path,
                _backup(root / "family-homonym.json", [task]),
                morphology_analyzer=analyzer,
            )
            next_export = export_labelstudio_project_tasks_from_db(
                db_path,
                sort_by="word",
                morphology_analyzer=analyzer,
            )
            with sqlite3.connect(db_path) as conn:
                resolutions = dict(
                    conn.execute(
                        "select normalized_word, decision from word_resolutions"
                    ).fetchall()
                )
                reviewed = {
                    row[0]
                    for row in conn.execute(
                        "select normalized_word from reviewed_words"
                    ).fetchall()
                }

        self.assertEqual(summary.imported_items, 1)
        self.assertEqual(resolutions, {"торганнары": "contextual_homonym"})
        self.assertTrue(
            {"торганнары", "торганнар", "торган"}.isdisjoint(reviewed)
        )
        self.assertEqual(
            next_export.projects["catchall"].exported_words,
            ["вакыт", "торганнар"],
        )
        self.assertEqual(
            next_export.projects["catchall"].report["covered_word_count"],
            3,
        )

    def test_rejects_conflicting_homonym_and_regular_annotations(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dsl_task = _task(
                "орфография",
                "orfografiä",
                "RL",
                annotations=[_homonym_annotation()],
                project_key="iya",
            )
            parsed = parse_labelstudio_export(
                _backup(root / "dsl-homonym.json", [dsl_task])
            )
            self.assertTrue(parsed.annotations[0].is_homonym)

            task = _task("вакыт", "waqıt", "N")
            task["annotations"].append(_homonym_annotation())
            with self.assertRaisesRegex(
                LabelStudioImportError,
                "conflicting annotations",
            ):
                parse_labelstudio_export(
                    _backup(root / "conflicting-homonym.json", [task])
                )

            contextual = _task(
                "акты",
                "aqtı",
                "N",
                annotations=[_homonym_annotation()],
                project_key="contextual_homonym",
                sample_id="sent_1",
                token_index=0,
            )
            with self.assertRaisesRegex(
                LabelStudioImportError,
                "cannot mark a contextual task",
            ):
                parse_labelstudio_export(
                    _backup(root / "contextual-homonym.json", [contextual])
                )

            malformed = _task(
                "вакыт",
                "waqıt",
                "N",
                annotations=[_homonym_annotation()],
            )
            malformed["annotations"][0]["result"][0]["value"]["choices"] = [
                "Not homonym"
            ]
            with self.assertRaisesRegex(
                LabelStudioImportError,
                "invalid homonym choice",
            ):
                parse_labelstudio_export(
                    _backup(root / "malformed-homonym.json", [malformed])
                )

    def test_rejects_array_missing_project_key_and_legacy_dictionary_origin(
        self,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task = _task("вакыт", "waqıt", "N")
            array_path = root / "array.json"
            array_path.write_text(json.dumps([task]), encoding="utf-8")
            with self.assertRaisesRegex(LabelStudioImportError, "task API response schema"):
                parse_labelstudio_export(array_path)

            del task["meta"]["project_key"]
            with self.assertRaisesRegex(LabelStudioImportError, "project_key"):
                parse_labelstudio_export(_backup(root / "no-key.json", [task]))

            task = _task("вакыт", "waqıt", "N")
            task["annotations"][0]["result"].insert(
                0,
                {
                    "from_name": "reviewed_origin",
                    "type": "choices",
                    "value": {"choices": ["N"]},
                },
            )
            with self.assertRaisesRegex(LabelStudioImportError, "unexpected control"):
                parse_labelstudio_export(_backup(root / "legacy-origin.json", [task]))

            contextual = _task(
                "акты",
                "aqtı",
                "N",
                project_key="contextual_homonym",
                sample_id="sent_1",
                token_index=0,
            )
            contextual["annotations"][0]["result"] = contextual["annotations"][0][
                "result"
            ][1:]
            with self.assertRaisesRegex(LabelStudioImportError, "reviewed_origin"):
                parse_labelstudio_export(
                    _backup(root / "contextual-no-origin.json", [contextual])
                )

    def test_rejects_legacy_catchall_schema(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            legacy = _task("вакыт", "waqıt", "N")
            legacy["meta"] = {"schema_version": 1, "project_key": "catchall"}
            with self.assertRaisesRegex(
                LabelStudioImportError,
                "unsupported schema_version",
            ):
                parse_labelstudio_export(_backup(root / "legacy.json", [legacy]))

    def test_rejects_mixed_projects_and_dictionary_homonym(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite", contextual_word="акты")
            mixed = [
                _task("вакыт", "waqıt", "N"),
                _task(
                    "акты",
                    "aqtı",
                    "N",
                    project_key="contextual_homonym",
                    sample_id="sent_1",
                    token_index=0,
                ),
            ]
            with self.assertRaisesRegex(LabelStudioImportError, "mixes project types"):
                parse_labelstudio_export(_backup(root / "mixed.json", mixed))
            dictionary = _backup(
                root / "dictionary.json",
                [_task("акты", "aqtı", "N")],
            )
            with self.assertRaisesRegex(
                LabelStudioImportError,
                "dictionary project contains contextual homonyms",
            ):
                import_labelstudio_annotations(db_path, dictionary)

    def test_imports_contextual_occurrence_without_reviewing_word(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite", contextual_word="акты")
            backup = _backup(
                root / "context.json",
                [
                    _task(
                        "акты",
                        "aqtı",
                        "N",
                        project_key="contextual_homonym",
                        sample_id="sent_1",
                        token_index=0,
                    )
                ],
            )
            first = import_labelstudio_annotations(db_path, backup)
            second = import_labelstudio_annotations(db_path, backup)
            contextual = load_contextual_reviews(db_path)
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(first.imported_items, 1)
        self.assertEqual(second.unchanged_items, 1)
        self.assertEqual(len(contextual), 1)
        self.assertEqual(next(iter(contextual.values())).zamanalif_dsl, "aqtı")
        self.assertNotIn("акты", reviewed)

    def test_contextual_import_rejects_stale_token_identity(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite", contextual_word="акты")
            backup = _backup(
                root / "context.json",
                [
                    _task(
                        "акты",
                        "aqtı",
                        "N",
                        project_key="contextual_homonym",
                        sample_id="sent_1",
                        token_index=1,
                    )
                ],
            )
            with self.assertRaisesRegex(LabelStudioImportError, "token index is stale"):
                import_labelstudio_annotations(db_path, backup)

    def test_audit_validates_database_and_reports_changes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            unchanged = _task("вакыт", "waqıt", "N")
            changed = _task("проект", "proyekt", "RL")
            changed["data"]["auto_zamanalif"] = "proekt"
            changed["annotations"][0]["result"][0]["value"]["text"] = [
                "proekt",
                "proyekt",
            ]
            backup = _backup(root / "backup.json", [unchanged, changed])

            summary = audit_labelstudio_export(db_path, backup)

        self.assertEqual(summary.completed_tasks, 2)
        self.assertEqual(summary.unchanged_tasks, 1)
        self.assertEqual(summary.conversion_changes, 1)
        self.assertEqual(summary.changes[0].word, "проект")

    def test_cli_import_and_audit_print_project_type(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            backup = _backup(root / "backup.json", [_task("вакыт", "waqıt", "N")])
            stdout = StringIO()
            with redirect_stdout(stdout):
                audit_code = main(
                    ["annotation-audit", "--db", str(db_path), "--input", str(backup)]
                )
                import_code = main(
                    ["annotation-import", "--db", str(db_path), "--input", str(backup)]
                )

        self.assertEqual(audit_code, 0)
        self.assertEqual(import_code, 0)
        self.assertIn("project=catchall", stdout.getvalue())
        self.assertIn(
            "family propagation: inherited=0 current_batch=0 "
            "historical_backfill=0 literal_subwords=0 "
            "deterministic_divergent=0 source_families=0",
            stdout.getvalue(),
        )


def _database(path: Path, *, contextual_word: str | None = None) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        create table samples (
            id text primary key,
            source_id text,
            text text not null
        );
        create table preannotation_state (
            sample_id text primary key,
            status text not null,
            tatar integer,
            tokens_json text
        );
        create table word_resolutions (
            normalized_word text primary key,
            decision text not null,
            updated_at text not null
        );
        """
    )
    token = contextual_word or "вакыт"
    conn.execute(
        "insert into samples(id, source_id, text) values ('sent_1', 'src', ?)",
        (token,),
    )
    conn.execute(
        """
        insert into preannotation_state(sample_id, status, tatar, tokens_json)
        values ('sent_1', 'annotated', 1, ?)
        """,
        (json.dumps([{"text": token, "label": "N"}], ensure_ascii=False),),
    )
    if contextual_word:
        conn.execute(
            """
            insert into word_resolutions(normalized_word, decision, updated_at)
            values (?, 'contextual_homonym', 'now')
            """,
            (contextual_word,),
        )
    conn.commit()
    conn.close()
    return path


def _task(
    word: str,
    zamanalif: str,
    origin: str,
    annotations: list[dict[str, object]] | None = None,
    *,
    project_key: str = "catchall",
    sample_id: str | None = None,
    token_index: int | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "cyrl_word": word,
        "auto_zamanalif": zamanalif,
        "gemini_origin": origin,
        "hints_html": "",
    }
    meta: dict[str, object] = {
        "schema_version": 1,
        "project_key": project_key,
    }
    if project_key == "catchall":
        meta["schema_version"] = 3
    if project_key == "contextual_homonym":
        data.update(
            {
                "sentence": word,
                "context_html": f"<mark>{word}</mark>",
                "native_zamanalif": zamanalif,
                "loanword_zamanalif": zamanalif,
            }
        )
        meta["sample_id"] = sample_id
        meta["token_index"] = token_index
    if annotations is None:
        results: list[dict[str, object]] = [
            {
                "from_name": "corrected_zamanalif",
                "type": "textarea",
                "value": {"text": [zamanalif]},
            }
        ]
        if project_key == "contextual_homonym":
            results.insert(
                0,
                {
                    "from_name": "reviewed_origin",
                    "type": "choices",
                    "value": {"choices": [origin]},
                },
            )
        annotations = [
            {
                "was_cancelled": False,
                "result": results,
            }
        ]
    return {
        "id": f"task_{word}",
        "data": data,
        "meta": meta,
        "annotations": annotations,
    }


def _homonym_annotation() -> dict[str, object]:
    return {
        "was_cancelled": False,
        "result": [
            {
                "from_name": "is_homonym",
                "type": "choices",
                "value": {"choices": ["Homonym"]},
            }
        ],
    }


def _backup(path: Path, tasks: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "tasks": tasks,
                "total": len(tasks),
                "total_annotations": sum(bool(task["annotations"]) for task in tasks),
                "total_predictions": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _add_words(path: Path, words: list[str], *, origin: str = "N") -> None:
    with sqlite3.connect(path) as conn:
        for index, word in enumerate(words, start=2):
            sample_id = f"sent_{index}"
            conn.execute(
                """
                insert into samples(id, source_id, text)
                values (?, 'src', ?)
                """,
                (sample_id, word),
            )
            conn.execute(
                """
                insert into preannotation_state(
                    sample_id, status, tatar, tokens_json
                ) values (?, 'annotated', 1, ?)
                """,
                (
                    sample_id,
                    json.dumps(
                        [{"text": word, "label": origin}],
                        ensure_ascii=False,
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
