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
    contextual_conversion_branches,
    export_contextual_tasks_from_db,
)
from tatar_preannotator.labelstudio_import import (
    LabelStudioImportError,
    audit_labelstudio_export,
    import_labelstudio_annotations,
)
from tatar_preannotator.word_export import conversion_branches


class ContextualReviewExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = FakeMorphologyAnalyzer()
        patchers = (
            patch(
                "tatar_preannotator.word_export.default_morphology_analyzer",
                return_value=self.analyzer,
            ),
            patch(
                "tatar_preannotator.labelstudio_import.default_morphology_analyzer",
                return_value=self.analyzer,
            ),
            patch(
                "tatar_preannotator.cli.default_morphology_analyzer",
                return_value=self.analyzer,
            ),
        )
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_exports_every_occurrence_and_highlights_only_target(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _database(Path(tmpdir) / "db.sqlite")

            result = export_contextual_tasks_from_db(db_path)

        words = [task["data"]["cyrl_word"] for task in result.tasks]
        self.assertEqual(words.count("Акты"), 1)
        self.assertEqual(words.count("акты"), 1)
        self.assertEqual(words.count("Кама"), 1)
        self.assertEqual(words.count("Сер"), 0)
        self.assertEqual(len(result.occurrences), 3)
        self.assertEqual(result.report["effective_homonym_word_count"], 3)
        self.assertEqual(result.report["review_required_homonym_word_count"], 2)
        self.assertEqual(
            result.report["auto_resolved_deterministic_occurrence_count"],
            1,
        )
        for task in result.tasks:
            self.assertEqual(task["meta"]["project_key"], "contextual_homonym")
            self.assertEqual(task["meta"]["schema_version"], 1)
            self.assertEqual(task["data"]["context_html"].count("<mark>"), 1)

    def test_concrete_resolution_clears_raw_homonym_flag(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _database(Path(tmpdir) / "db.sqlite")
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    insert into word_resolutions(normalized_word, decision, updated_at)
                    values ('сер', 'N', 'now')
                    """
                )

            result = export_contextual_tasks_from_db(db_path)

        self.assertNotIn("сер", {task["data"]["cyrl_word"].lower() for task in result.tasks})

    def test_isolated_gk_get_native_fallbacks_only_in_contextual_project(self) -> None:
        self.assertEqual(conversion_branches("г").native_dsl, "")
        self.assertEqual(conversion_branches("к").native_dsl, "")
        self.assertEqual(contextual_conversion_branches("г").native_dsl, "ğ")
        self.assertEqual(contextual_conversion_branches("г").loanword_dsl, "g")
        self.assertEqual(contextual_conversion_branches("к").native_dsl, "q")
        self.assertEqual(contextual_conversion_branches("к").loanword_dsl, "k")

    def test_contextual_native_fallback_round_trips_without_correction(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "insert into samples(id, source_id, text) values ('sent_g', 'src', 'Г.')"
                )
                conn.execute(
                    """
                    insert into preannotation_state(sample_id, status, tatar, tokens_json)
                    values ('sent_g', 'annotated', 1, ?)
                    """,
                    (json.dumps([{"text": "Г", "label": "U", "homonym": True}]),),
                )

            exported = export_contextual_tasks_from_db(db_path)
            task = next(task for task in exported.tasks if task["data"]["cyrl_word"] == "Г")
            self.assertEqual(task["data"]["native_zamanalif"], "ğ")
            self.assertEqual(task["data"]["loanword_zamanalif"], "g")
            task["annotations"] = [
                {
                    "was_cancelled": False,
                    "result": [
                        {
                            "from_name": "reviewed_origin",
                            "type": "choices",
                            "value": {"choices": ["N"]},
                        }
                    ],
                }
            ]
            backup = root / "backup.json"
            backup.write_text(
                json.dumps(
                    {
                        "tasks": [task],
                        "total": 1,
                        "total_annotations": 1,
                        "total_predictions": 0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = import_labelstudio_annotations(db_path, backup)
            with sqlite3.connect(db_path) as conn:
                stored = conn.execute(
                    """
                    select origin, zamanalif_dsl
                    from contextual_reviews
                    where sample_id = 'sent_g' and token_index = 0
                    """
                ).fetchone()

        self.assertEqual(summary.imported_items, 1)
        self.assertEqual(stored, ("N", "ğ"))

    def test_combined_cli_keeps_homonyms_out_of_dictionary_projects(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            output_dir = root / "projects"
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "annotation-export",
                        "--db",
                        str(db_path),
                        "--output-dir",
                        str(output_dir),
                    ]
                )

            contextual_files = sorted(
                output_dir.glob("project_contextual_homonym_batch_*.json")
            )
            contextual_words = {
                task["data"]["cyrl_word"].lower()
                for path in contextual_files
                for task in json.loads(path.read_text(encoding="utf-8"))
            }
            dictionary_words = {
                task["data"]["cyrl_word"].lower()
                for path in output_dir.glob("project_*_batch_*.json")
                if "contextual_homonym" not in path.name
                for task in json.loads(path.read_text(encoding="utf-8"))
            }

        self.assertEqual(exit_code, 0)
        self.assertEqual(contextual_words, {"акты", "кама"})
        self.assertTrue({"акты", "кама", "сер"}.isdisjoint(dictionary_words))
        self.assertIn("проект", dictionary_words)
        self.assertIn("contextual=3", stdout.getvalue())

    def test_repeated_export_returns_same_unreviewed_occurrence(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            first_dir = root / "first"
            second_dir = root / "second"
            first = main(
                [
                    "annotation-export",
                    "--db",
                    str(db_path),
                    "--output-dir",
                    str(first_dir),
                    "--max-items",
                    "1",
                ]
            )
            second = main(
                [
                    "annotation-export",
                    "--db",
                    str(db_path),
                    "--output-dir",
                    str(second_dir),
                    "--max-items",
                    "1",
                ]
            )
            first_context = json.loads(
                next(first_dir.glob("project_contextual_homonym_batch_*.json")).read_text(
                    encoding="utf-8"
                )
            )
            second_context = json.loads(
                next(second_dir.glob("project_contextual_homonym_batch_*.json")).read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual(first_context, second_context)

    def test_exported_contextual_task_round_trips_through_strict_import(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            exported = export_contextual_tasks_from_db(db_path, max_items=1)
            task = {
                **exported.tasks[0],
                "annotations": [
                    {
                        "was_cancelled": False,
                        "result": [
                            {
                                "from_name": "reviewed_origin",
                                "type": "choices",
                                "value": {"choices": ["N"]},
                            },
                            {
                                "from_name": "corrected_zamanalif",
                                "type": "textarea",
                                "value": {"text": ["aqtı"]},
                            },
                        ],
                    }
                ],
            }
            backup = root / "backup.json"
            backup.write_text(
                json.dumps(
                    {
                        "tasks": [task],
                        "total": 1,
                        "total_annotations": 1,
                        "total_predictions": 0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = import_labelstudio_annotations(db_path, backup)
            remaining = export_contextual_tasks_from_db(db_path)

        self.assertEqual(summary.project_key, "contextual_homonym")
        self.assertEqual(summary.imported_items, 1)
        self.assertNotIn(exported.occurrences[0], remaining.occurrences)

    def test_contextual_origin_uses_exported_variant_without_correction(self) -> None:
        for correction_result in (
            [],
            [
                {
                    "from_name": "corrected_zamanalif",
                    "type": "textarea",
                    "value": {"text": [""]},
                }
            ],
        ):
            with self.subTest(correction_result=correction_result):
                with TemporaryDirectory() as tmpdir:
                    root = Path(tmpdir)
                    db_path = _database(root / "db.sqlite")
                    exported = export_contextual_tasks_from_db(db_path, max_items=1)
                    task = {
                        **exported.tasks[0],
                        "annotations": [
                            {
                                "was_cancelled": False,
                                "result": [
                                    {
                                        "from_name": "reviewed_origin",
                                        "type": "choices",
                                        "value": {"choices": ["RL"]},
                                    },
                                    *correction_result,
                                ],
                            }
                        ],
                    }
                    backup = root / "backup.json"
                    backup.write_text(
                        json.dumps(
                            {
                                "tasks": [task],
                                "total": 1,
                                "total_annotations": 1,
                                "total_predictions": 0,
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )

                    summary = import_labelstudio_annotations(db_path, backup)
                    with sqlite3.connect(db_path) as conn:
                        stored = conn.execute(
                            """
                            select origin, zamanalif_dsl
                            from contextual_reviews
                            where sample_id = ? and token_index = ?
                            """,
                            (
                                exported.occurrences[0].sample_id,
                                exported.occurrences[0].token_index,
                            ),
                        ).fetchone()

                self.assertEqual(summary.imported_items, 1)
                self.assertEqual(stored, ("RL", "aktı"))

    def test_initial_references_collapse_spacing_and_observed_case_forms(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _database(Path(tmpdir) / "db.sqlite")
            _add_initial_sentences(db_path)

            result = export_contextual_tasks_from_db(db_path)

        initial_tasks = [task for task in result.tasks if task["data"]["cyrl_word"] == "К"]
        self.assertEqual(len(initial_tasks), 2)
        self.assertEqual(
            {task["data"]["sentence"] for task in initial_tasks},
            {"К.Насыйри язган.", "К. Ушинский язган."},
        )
        self.assertEqual(result.report["grouped_initial_occurrence_count"], 3)

    def test_repeated_initial_letters_keep_distinct_target_slots(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _database(Path(tmpdir) / "db.sqlite")
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    insert into word_resolutions(normalized_word, decision, updated_at)
                    values ('в', 'contextual_homonym', 'now')
                    """
                )
                for index in range(2):
                    sample_id = f"double_{index}"
                    conn.execute(
                        "insert into samples(id, source_id, text) values (?, 'src', 'В.В. Путин.')",
                        (sample_id,),
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
                                [
                                    {"text": "В", "label": "U"},
                                    {"text": "В", "label": "U"},
                                    {"text": "Путин", "label": "RL"},
                                ],
                                ensure_ascii=False,
                            ),
                        ),
                    )

            result = export_contextual_tasks_from_db(db_path)

        tasks = [task for task in result.tasks if task["data"]["cyrl_word"] == "В"]
        self.assertEqual(len(tasks), 2)
        self.assertEqual({task["meta"]["token_index"] for task in tasks}, {0, 1})

    def test_old_duplicate_initial_tasks_propagate_atomically(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_initial_sentences(db_path)
            exported = export_contextual_tasks_from_db(db_path)
            representative = next(
                task
                for task in exported.tasks
                if task["data"]["sentence"] == "К.Насыйри язган."
            )
            tasks = [
                _annotated_initial_task(representative, "initial_1", "N", "q"),
                _annotated_initial_task(
                    representative,
                    "initial_2",
                    "N",
                    "q",
                    sample_id="initial_2",
                ),
            ]
            backup = _contextual_backup(root / "initials.json", tasks)

            audit = audit_labelstudio_export(db_path, backup)
            summary = import_labelstudio_annotations(db_path, backup)
            with sqlite3.connect(db_path) as conn:
                stored = conn.execute(
                    """
                    select sample_id, zamanalif_dsl, origin
                    from contextual_reviews
                    where sample_id like 'initial_%'
                    order by sample_id
                    """
                ).fetchall()

        self.assertEqual(audit.contextual_initial_source_groups, 1)
        self.assertEqual(audit.contextual_initial_propagated_items, 2)
        self.assertEqual(summary.imported_items, 4)
        self.assertEqual(summary.contextual_initial_source_groups, 1)
        self.assertEqual(summary.contextual_initial_propagated_items, 2)
        self.assertEqual(
            stored,
            [
                ("initial_1", "q", "N"),
                ("initial_2", "q", "N"),
                ("initial_3", "q", "N"),
                ("initial_4", "q", "N"),
            ],
        )

    def test_conflicting_duplicate_initial_tasks_roll_back(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_initial_sentences(db_path)
            representative = next(
                task
                for task in export_contextual_tasks_from_db(db_path).tasks
                if task["data"]["sentence"] == "К.Насыйри язган."
            )
            tasks = [
                _annotated_initial_task(representative, "initial_1", "N", "q"),
                _annotated_initial_task(
                    representative,
                    "initial_2",
                    "RL",
                    "k",
                    sample_id="initial_2",
                ),
            ]
            backup = _contextual_backup(root / "conflict.json", tasks)

            with self.assertRaisesRegex(
                LabelStudioImportError,
                "conflicting annotations for initial reference",
            ):
                import_labelstudio_annotations(db_path, backup)
            with sqlite3.connect(db_path) as conn:
                count = conn.execute(
                    "select count(*) from contextual_reviews"
                ).fetchone()[0]

        self.assertEqual(count, 0)

    def test_existing_initial_review_backfills_during_contextual_import(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            _add_initial_sentences(db_path)
            exported = export_contextual_tasks_from_db(db_path)
            unrelated = next(
                task for task in exported.tasks if task["data"]["cyrl_word"] == "Акты"
            )
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    insert into contextual_reviews(
                        sample_id, token_index, normalized_word,
                        zamanalif_dsl, origin, updated_at
                    ) values ('initial_1', 0, 'к', 'q', 'N', 'now')
                    """
                )
            backup = _contextual_backup(
                root / "backfill.json",
                [_annotated_initial_task(unrelated, "unrelated", "RL", "aktı")],
            )

            summary = import_labelstudio_annotations(db_path, backup)
            with sqlite3.connect(db_path) as conn:
                initial_count = conn.execute(
                    """
                    select count(*) from contextual_reviews
                    where sample_id like 'initial_%'
                    """
                ).fetchone()[0]

        self.assertEqual(initial_count, 4)
        self.assertEqual(summary.contextual_initial_source_groups, 0)
        self.assertEqual(summary.contextual_initial_backfill_items, 3)


def _database(path: Path) -> Path:
    with sqlite3.connect(path) as conn:
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
        rows = [
            (
                "sent_1",
                "Акты һәм проект.",
                [
                    {"text": "Акты", "label": "RL", "homonym": True},
                    {"text": "һәм", "label": "N"},
                    {"text": "проект", "label": "RL"},
                ],
            ),
            (
                "sent_2",
                "Кама акты.",
                [
                    {"text": "Кама", "label": "N"},
                    {"text": "акты", "label": "RL"},
                ],
            ),
            (
                "sent_3",
                "Сер ачылды.",
                [
                    {"text": "Сер", "label": "RL", "homonym": True},
                    {"text": "ачылды", "label": "N"},
                ],
            ),
        ]
        for sample_id, text, tokens in rows:
            conn.execute(
                "insert into samples(id, source_id, text) values (?, 'src', ?)",
                (sample_id, text),
            )
            conn.execute(
                """
                insert into preannotation_state(sample_id, status, tatar, tokens_json)
                values (?, 'annotated', 1, ?)
                """,
                (sample_id, json.dumps(tokens, ensure_ascii=False)),
            )
        conn.executemany(
            """
            insert into word_resolutions(normalized_word, decision, updated_at)
            values (?, 'contextual_homonym', 'now')
            """,
            [("акты",), ("кама",)],
        )
    return path


def _add_initial_sentences(path: Path) -> None:
    rows = [
        ("initial_1", "К.Насыйри язган.", "Насыйри", "язган"),
        ("initial_2", "К. Насыйри язган.", "Насыйри", "язган"),
        ("initial_3", "К.Насыйриның китабы.", "Насыйриның", "китабы"),
        ("initial_4", "К. Насыйридан өйрәнгән.", "Насыйридан", "өйрәнгән"),
        ("initial_5", "К. Ушинский язган.", "Ушинский", "язган"),
    ]
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            insert into word_resolutions(normalized_word, decision, updated_at)
            values ('к', 'contextual_homonym', 'now')
            """
        )
        for sample_id, sentence, surname, final_word in rows:
            conn.execute(
                "insert into samples(id, source_id, text) values (?, 'src', ?)",
                (sample_id, sentence),
            )
            conn.execute(
                """
                insert into preannotation_state(sample_id, status, tatar, tokens_json)
                values (?, 'annotated', 1, ?)
                """,
                (
                    sample_id,
                    json.dumps(
                        [
                            {"text": "К", "label": "U"},
                            {"text": surname, "label": "N"},
                            {"text": final_word, "label": "N"},
                        ],
                        ensure_ascii=False,
                    ),
                ),
            )


def _annotated_initial_task(
    task: dict[str, object],
    task_id: str,
    origin: str,
    zamanalif: str,
    *,
    sample_id: str | None = None,
) -> dict[str, object]:
    result = json.loads(json.dumps(task, ensure_ascii=False))
    result["id"] = task_id
    if sample_id is not None:
        result["meta"]["sample_id"] = sample_id
    result["annotations"] = [
        {
            "was_cancelled": False,
            "result": [
                {
                    "from_name": "reviewed_origin",
                    "type": "choices",
                    "value": {"choices": [origin]},
                },
                {
                    "from_name": "corrected_zamanalif",
                    "type": "textarea",
                    "value": {"text": [zamanalif]},
                },
            ],
        }
    ]
    return result


def _contextual_backup(path: Path, tasks: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "tasks": tasks,
                "total": len(tasks),
                "total_annotations": len(tasks),
                "total_predictions": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
