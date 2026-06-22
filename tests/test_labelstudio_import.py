from __future__ import annotations

from contextlib import closing, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.cli import main
from tatar_preannotator.labelstudio_import import (
    LabelStudioImportError,
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
            ("empty conversion", _task("авыл", "N", ""), "must not be empty"),
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
        self.assertEqual(reviewed["авыл"].origin, "N")


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


def _empty_database(path: Path) -> Path:
    with closing(sqlite3.connect(path)):
        pass
    return path


def _write_export(path: Path, tasks: list[dict]) -> Path:
    path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
