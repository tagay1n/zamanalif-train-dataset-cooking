from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.cli import main
from tatar_preannotator.word_export import (
    contains_conditional_letter,
    convert_for_annotation,
    export_labelstudio_tasks,
    export_labelstudio_tasks_from_db,
    load_exported_words,
    mark_exported_words,
    normalize_word,
    vowel_harmony_class,
)


class PreannotatorWordExportTests(unittest.TestCase):
    def test_normalize_word_strips_punctuation_and_lowercases(self) -> None:
        self.assertEqual(normalize_word("«Вакытында!»"), "вакытында")
        self.assertEqual(normalize_word("..."), "")
        self.assertEqual(normalize_word("сүз-сүз"), "сүз-сүз")

    def test_conditional_letter_detection(self) -> None:
        self.assertTrue(contains_conditional_letter("вакыт"))
        self.assertTrue(contains_conditional_letter("позиция"))
        self.assertFalse(contains_conditional_letter("шәһәр"))

    def test_vowel_harmony_classification(self) -> None:
        self.assertEqual(vowel_harmony_class("күрә"), "front_only")
        self.assertEqual(vowel_harmony_class("бара"), "back_only")
        self.assertEqual(vowel_harmony_class("гадел"), "mixed_front_back")

    def test_export_filters_deduplicates_and_generates_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.jsonl"
            input_path.write_text(
                "\n".join(
                    json.dumps(row, ensure_ascii=False)
                    for row in [
                        {
                            "id": "sent_1",
                            "tatar": True,
                            "tokens": [
                                {"text": "Мин", "label": "N"},
                                {"text": "вакытында", "label": "N"},
                                {"text": "Вакытында", "label": "N"},
                                {"text": "яңа", "label": "N"},
                                {"text": "проект", "label": "RL"},
                                {"text": "турында", "label": "N"},
                                {"text": "әйттем", "label": "N"},
                            ],
                        },
                        {
                            "id": "sent_2",
                            "tatar": True,
                            "tokens": [
                                {"text": "Гадел", "label": "N"},
                                {"text": "сүз", "label": "U"},
                                {"text": "сер", "label": "N"},
                            ],
                        },
                        {
                            "id": "sent_3",
                            "tatar": True,
                            "tokens": [
                                {"text": "позиция", "label": "RL"},
                                {"text": "дөрес", "label": "N"},
                            ],
                        },
                        {
                            "id": "sent_4",
                            "tatar": False,
                            "tokens": [{"text": "вакыт", "label": "N"}],
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = export_labelstudio_tasks(input_path, sort_by="word")

        words = [task["data"]["cyrl_word"] for task in result.tasks]
        self.assertEqual(
            words,
            [
                "вакытында",
                "дөрес",
                "позиция",
                "проект",
                "сер",
                "сүз",
                "турында",
                "яңа",
                "әйттем",
            ],
        )
        self.assertEqual(result.tasks[0]["data"]["auto_zamanalif"], "waqıtında")
        self.assertEqual(result.tasks[2]["data"]["auto_zamanalif"], "pozitsiya")
        self.assertEqual(result.tasks[3]["data"]["auto_zamanalif"], "proyekt")
        self.assertEqual(result.tasks[7]["data"]["auto_zamanalif"], "yaña")
        self.assertEqual(result.report["mixed_harmony_n_word_skipped_count"], 1)
        self.assertEqual(result.report["u_exported_word_count"], 1)

        html = result.tasks[0]["data"]["hints_html"]
        self.assertIn("<b>в</b> -> <b>w</b> because of native word", html)
        self.assertIn("<b>к</b> -> <b>q</b> because of native word", html)
        self.assertIn("Frequency for <b><i>вакытында</i></b>: <b>2</b>", html)

    def test_mixed_harmony_rl_is_kept_and_rl_without_conditional_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.jsonl"
            input_path.write_text(
                json.dumps(
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "проект", "label": "RL"},
                            {"text": "банк", "label": "RL"},
                            {"text": "спорт", "label": "RL"},
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            result = export_labelstudio_tasks(input_path, sort_by="word")

        self.assertEqual([task["data"]["cyrl_word"] for task in result.tasks], ["банк", "проект"])
        self.assertEqual(result.tasks[1]["data"]["auto_zamanalif"], "proyekt")

    def test_include_unknown_and_include_rl_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.jsonl"
            input_path.write_text(
                json.dumps(
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "сүз", "label": "U"},
                            {"text": "проект", "label": "RL"},
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            result = export_labelstudio_tasks(input_path, include_unknown=False, include_rl=False)

        self.assertEqual(result.tasks, [])

    def test_converter_integration_and_clean_zamanalif_letters(self) -> None:
        self.assertEqual(convert_for_annotation("шәһәр", "N"), "şähär")
        self.assertEqual(convert_for_annotation("проект", "RL"), "proyekt")
        self.assertEqual(convert_for_annotation("яңа", "N"), "yaña")

    def test_sorting_frequency_limit_and_min_frequency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.jsonl"
            rows = [
                {"id": "1", "tatar": True, "tokens": [{"text": "юл", "label": "N"}]},
                {"id": "2", "tatar": True, "tokens": [{"text": "юл", "label": "N"}]},
                {"id": "3", "tatar": True, "tokens": [{"text": "вакыт", "label": "N"}]},
            ]
            input_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )

            limited = export_labelstudio_tasks(input_path, max_items=1)
            frequent = export_labelstudio_tasks(input_path, min_frequency=2)

        self.assertEqual([task["data"]["cyrl_word"] for task in limited.tasks], ["юл"])
        self.assertEqual([task["data"]["cyrl_word"] for task in frequent.tasks], ["юл"])

    def test_cli_writes_labelstudio_json_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.jsonl"
            output_path = Path(tmpdir) / "out.json"
            input_path.write_text(
                json.dumps(
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [{"text": "вакыт", "label": "N"}],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "annotation-export",
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                    ]
                )

            data = json.loads(output_path.read_text(encoding="utf-8"))
            report = json.loads(Path(str(output_path) + ".report.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(data[0]["data"]["cyrl_word"], "вакыт")
        self.assertEqual(set(data[0]["data"]), {"id", "cyrl_word", "auto_zamanalif", "hints_html"})
        self.assertEqual(report["exported_word_count"], 1)
        self.assertIn("annotation export complete", output.getvalue())

    def test_exports_from_sqlite_annotation_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "selected.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    create table samples (
                        id text primary key,
                        source_id text,
                        text text not null
                    )
                    """
                )
                conn.execute(
                    """
                    create table preannotation_state (
                        sample_id text primary key references samples(id),
                        status text not null,
                        tatar integer,
                        tokens_json text,
                        attempts integer not null default 0,
                        last_error text,
                        updated_at text not null
                    )
                    """
                )
                conn.execute(
                    "insert into samples(id, source_id, text) values (?, ?, ?)",
                    ("sent_1", "src", "Мин вакыт турында әйттем."),
                )
                conn.execute(
                    """
                    insert into preannotation_state(
                        sample_id, status, tatar, tokens_json, updated_at
                    ) values (?, ?, ?, ?, ?)
                    """,
                    (
                        "sent_1",
                        "annotated",
                        1,
                        json.dumps(
                            [
                                {"text": "вакыт", "label": "N"},
                                {"text": "турында", "label": "N"},
                            ],
                            ensure_ascii=False,
                        ),
                        "2026-01-01T00:00:00+00:00",
                    ),
                )

            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")

        self.assertEqual(
            [task["data"]["cyrl_word"] for task in result.tasks],
            ["вакыт", "турында"],
        )

    def test_sqlite_tracking_skips_previously_exported_words(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.jsonl"
            db_path = Path(tmpdir) / "state.sqlite"
            input_path.write_text(
                json.dumps(
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "вакыт", "label": "N"},
                            {"text": "яңа", "label": "N"},
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            mark_exported_words(db_path, ["вакыт"])

            result = export_labelstudio_tasks(
                input_path,
                sort_by="word",
                already_exported=load_exported_words(db_path),
            )

            with sqlite3.connect(db_path) as conn:
                count = conn.execute("select count(*) from exported_words").fetchone()[0]

        self.assertEqual([task["data"]["cyrl_word"] for task in result.tasks], ["яңа"])
        self.assertEqual(result.report["already_exported_skipped_count"], 1)
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
