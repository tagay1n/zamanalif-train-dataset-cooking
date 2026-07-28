from __future__ import annotations

from contextlib import closing, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.cli import main
from tatar_preannotator.conflict_resolver import load_word_resolutions
from tatar_preannotator.labelstudio_import import (
    LabelStudioImportError,
    audit_labelstudio_export,
    import_labelstudio_annotations,
    parse_labelstudio_export,
)
from tatar_preannotator.word_export import load_reviewed_words, save_reviewed_word


class LabelStudioImportTests(unittest.TestCase):
    def test_imports_completed_tasks_and_skips_unannotated_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _empty_database(root / "zamanalif.sqlite")
            input_path = _write_export(
                root / "labelstudio.json",
                [
                    _task("авыл", "N", "awıl"),
                    _task(
                        "орфография",
                        "RL",
                        "orfografi{{IYA|compact=ä|explicit=yä}}",
                        extra_results=[
                            {
                                "from_name": "ignored_control",
                                "type": "choices",
                                "value": {"choices": ["ignored"]},
                            }
                        ],
                    ),
                    {"data": {"cyrl_word": "вакыт"}, "annotations": []},
                    {
                        "data": {"cyrl_word": "сер"},
                        "annotations": [{"was_cancelled": True, "result": []}],
                    },
                ],
            )

            summary = import_labelstudio_annotations(db_path, input_path)
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(summary.total_tasks, 4)
        self.assertEqual(summary.completed_tasks, 2)
        self.assertEqual(summary.imported_words, 2)
        self.assertEqual(summary.skipped_unannotated_tasks, 2)
        self.assertEqual(reviewed["авыл"].zamanalif_dsl, "awıl")
        self.assertEqual(reviewed["авыл"].origin, "N")
        self.assertEqual(
            reviewed["орфография"].zamanalif_dsl,
            "orfografi{{IYA|compact=ä|explicit=yä}}",
        )

    def test_reimporting_identical_annotation_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _empty_database(root / "zamanalif.sqlite")
            input_path = _write_export(root / "labelstudio.json", [_task("авыл", "N", "awıl")])

            first = import_labelstudio_annotations(db_path, input_path)
            second = import_labelstudio_annotations(db_path, input_path)

        self.assertEqual(first.imported_words, 1)
        self.assertEqual(first.unchanged_words, 0)
        self.assertEqual(second.imported_words, 0)
        self.assertEqual(second.unchanged_words, 1)

    def test_import_defers_preannotated_homonym_words_for_context_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database_with_homonym(root / "zamanalif.sqlite", "акты")
            changed = _task("акты", "RL", "aktı")
            changed["annotations"][0]["result"][1]["value"]["text"] = ["aktı", "aqtı"]
            input_path = _write_export(root / "labelstudio.json", [changed])

            summary = import_labelstudio_annotations(db_path, input_path)
            reviewed = load_reviewed_words(db_path)
            resolutions = load_word_resolutions(db_path)

        self.assertEqual(summary.completed_tasks, 1)
        self.assertEqual(summary.imported_words, 0)
        self.assertEqual(summary.contextual_homonym_words, 1)
        self.assertEqual(summary.contextual_homonym_examples, ("акты",))
        self.assertNotIn("акты", reviewed)
        self.assertEqual(resolutions["акты"].decision, "contextual_homonym")

    def test_conflicting_existing_review_rolls_back_entire_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _empty_database(root / "zamanalif.sqlite")
            save_reviewed_word(db_path, "авыл", "awıl", "N")
            input_path = _write_export(
                root / "labelstudio.json",
                [
                    _task("вакыт", "N", "waqıt"),
                    _task("авыл", "RL", "avıl"),
                ],
            )

            with self.assertRaisesRegex(LabelStudioImportError, "reviewed word conflict"):
                import_labelstudio_annotations(db_path, input_path)
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(set(reviewed), {"авыл"})
        self.assertEqual(reviewed["авыл"].zamanalif_dsl, "awıl")

    def test_duplicate_word_tasks_are_rejected_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _empty_database(root / "zamanalif.sqlite")
            input_path = _write_export(
                root / "labelstudio.json",
                [_task("Авыл", "N", "awıl"), _task("авыл", "N", "awıl")],
            )

            with self.assertRaisesRegex(LabelStudioImportError, "duplicate normalized word"):
                import_labelstudio_annotations(db_path, input_path)
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(reviewed, {})

    def test_malformed_completed_annotation_is_rejected(self) -> None:
        cases = [
            (
                "missing origin",
                {
                    "data": {"cyrl_word": "авыл"},
                    "annotations": [
                        {
                            "result": [
                                {
                                    "from_name": "corrected_zamanalif",
                                    "type": "textarea",
                                    "value": {"text": ["awıl"]},
                                }
                            ]
                        }
                    ],
                },
                "reviewed_origin",
            ),
            ("invalid origin", _task("авыл", "native", "awıl"), "invalid origin"),
            ("invalid DSL", _task("авыл", "N", "авыл"), "invalid Zamanalif DSL"),
            (
                "empty conversion",
                _task_with_empty_conversion("авыл", "N", "awıl"),
                "must not be empty",
            ),
        ]
        for name, task, message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = _write_export(Path(tmpdir) / "export.json", [task])
                with self.assertRaisesRegex(LabelStudioImportError, message):
                    parse_labelstudio_export(path)

    def test_matching_multiple_annotations_are_accepted_but_conflicts_fail(self) -> None:
        matching = _task("авыл", "N", "awıl")
        matching["annotations"].append(matching["annotations"][0].copy())
        conflicting = _task("авыл", "N", "awıl")
        conflicting["annotations"].append(_annotation("RL", "avıl"))

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            parsed = parse_labelstudio_export(
                _write_export(root / "matching.json", [matching])
            )
            with self.assertRaisesRegex(LabelStudioImportError, "conflicting"):
                parse_labelstudio_export(
                    _write_export(root / "conflicting.json", [conflicting])
                )

        self.assertEqual(len(parsed.annotations), 1)

    def test_cli_imports_annotations_and_prints_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _empty_database(root / "zamanalif.sqlite")
            input_path = _write_export(root / "export.json", [_task("авыл", "N", "awıl")])
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "annotation-import",
                        "--db",
                        str(db_path),
                        "--input",
                        str(input_path),
                    ]
                )
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(exit_code, 0)
        self.assertIn("annotation import complete", stdout.getvalue())
        self.assertIn("imported=1", stdout.getvalue())
        self.assertIn("contextual_homonyms=0", stdout.getvalue())
        self.assertEqual(reviewed["авыл"].origin, "N")

    def test_accepts_task_api_wrapper_and_origin_from_exported_data(self) -> None:
        task = _task("авыл", "N", "awıl")
        task["data"]["gemini_origin"] = "N"
        task["annotations"][0]["result"] = [
            task["annotations"][0]["result"][1]
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "api.json"
            path.write_text(
                json.dumps({"tasks": [task], "total": 1}, ensure_ascii=False),
                encoding="utf-8",
            )
            parsed = parse_labelstudio_export(path)

        self.assertEqual(parsed.annotations[0].origin, "N")
        self.assertEqual(parsed.annotations[0].zamanalif_dsl, "awıl")

    def test_uses_last_textarea_value_when_first_is_exported_suggestion(self) -> None:
        task = _task("акты", "RL", "aktı")
        task["annotations"][0]["result"][1]["value"]["text"] = ["aktı", "aqtı"]
        with tempfile.TemporaryDirectory() as tmpdir:
            parsed = parse_labelstudio_export(
                _write_export(Path(tmpdir) / "export.json", [task])
            )

        self.assertEqual(parsed.annotations[0].zamanalif_dsl, "aqtı")

    def test_rejects_ambiguous_multiple_textarea_values(self) -> None:
        task = _task("акты", "RL", "aktı")
        task["annotations"][0]["result"][1]["value"]["text"] = ["other", "aqtı"]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_export(Path(tmpdir) / "export.json", [task])
            with self.assertRaisesRegex(
                LabelStudioImportError,
                "without the exported suggestion first",
            ):
                parse_labelstudio_export(path)

    def test_audit_reports_only_values_changed_by_annotator(self) -> None:
        unchanged = _task("авыл", "N", "awıl")
        changed = _task("акты", "RL", "aktı")
        changed["id"] = 510
        changed["annotations"][0]["result"][1]["value"]["text"] = ["aktı", "aqtı"]
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = audit_labelstudio_export(
                _write_export(Path(tmpdir) / "export.json", [unchanged, changed])
            )

        self.assertEqual(summary.completed_tasks, 2)
        self.assertEqual(summary.unchanged_tasks, 1)
        self.assertEqual(summary.origin_changes, 0)
        self.assertEqual(summary.conversion_changes, 1)
        self.assertEqual(len(summary.changes), 1)
        self.assertEqual(summary.changes[0].task_id, "510")
        self.assertEqual(summary.changes[0].word, "акты")
        self.assertEqual(summary.changes[0].reviewed_zamanalif, "aqtı")

    def test_cli_audit_prints_only_genuine_changes(self) -> None:
        unchanged = _task("авыл", "N", "awıl")
        changed = _task("акты", "RL", "aktı")
        changed["annotations"][0]["result"][1]["value"]["text"] = ["aktı", "aqtı"]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_export(
                Path(tmpdir) / "export.json",
                [unchanged, changed],
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["annotation-audit", "--input", str(path)])

        self.assertEqual(exit_code, 0)
        self.assertIn("changed=1", stdout.getvalue())
        self.assertIn("conversion: aktı -> aqtı", stdout.getvalue())


def _task(
    word: str,
    origin: str,
    zamanalif_dsl: str,
    *,
    extra_results: list[dict] | None = None,
) -> dict:
    annotation = _annotation(origin, zamanalif_dsl)
    annotation["result"].extend(extra_results or [])
    return {
        "data": {
            "id": f"word_{word}",
            "cyrl_word": word,
            "auto_zamanalif": zamanalif_dsl,
        },
        "annotations": [annotation],
    }


def _annotation(origin: str, zamanalif_dsl: str) -> dict:
    return {
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
                "value": {"text": [zamanalif_dsl]},
            },
        ],
    }


def _task_with_empty_conversion(
    word: str,
    origin: str,
    suggested_zamanalif: str,
) -> dict:
    task = _task(word, origin, suggested_zamanalif)
    task["annotations"][0]["result"][1]["value"]["text"] = [""]
    return task


def _empty_database(path: Path) -> Path:
    with closing(sqlite3.connect(path)):
        pass
    return path


def _database_with_homonym(path: Path, word: str) -> Path:
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            """
            create table samples (
                id text primary key,
                text text not null
            );
            create table preannotation_state (
                sample_id text primary key,
                status text not null,
                tatar integer,
                tokens_json text,
                updated_at text not null
            );
            """
        )
        conn.execute("insert into samples(id, text) values ('sent_1', ?)", (word,))
        conn.execute(
            """
            insert into preannotation_state(
                sample_id, status, tatar, tokens_json, updated_at
            ) values ('sent_1', 'annotated', 1, ?, 'now')
            """,
            (
                json.dumps(
                    [{"text": word, "label": "RL", "homonym": True}],
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()
    return path


def _write_export(path: Path, tasks: list[dict]) -> Path:
    path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
