from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tatar_preannotator.cli import main
from tatar_preannotator.contextual_review import (
    export_contextual_tasks_from_db,
)
from tatar_preannotator.labelstudio_import import import_labelstudio_annotations


class ContextualReviewExportTests(unittest.TestCase):
    def test_exports_every_occurrence_and_highlights_only_target(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = _database(Path(tmpdir) / "db.sqlite")

            result = export_contextual_tasks_from_db(db_path)

        words = [task["data"]["cyrl_word"] for task in result.tasks]
        self.assertEqual(words.count("Акты"), 1)
        self.assertEqual(words.count("акты"), 1)
        self.assertEqual(words.count("Кама"), 1)
        self.assertEqual(words.count("Сер"), 1)
        self.assertEqual(len(result.occurrences), 4)
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
        self.assertEqual(contextual_words, {"акты", "кама", "сер"})
        self.assertTrue({"акты", "кама", "сер"}.isdisjoint(dictionary_words))
        self.assertIn("проект", dictionary_words)
        self.assertIn("contextual=4", stdout.getvalue())

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


if __name__ == "__main__":
    unittest.main()
