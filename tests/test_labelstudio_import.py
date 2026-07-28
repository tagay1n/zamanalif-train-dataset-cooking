from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tatar_preannotator.cli import main
from tatar_preannotator.contextual_review import load_contextual_reviews
from tatar_preannotator.labelstudio_import import (
    LabelStudioImportError,
    audit_labelstudio_export,
    import_labelstudio_annotations,
    parse_labelstudio_export,
)
from tatar_preannotator.word_export import load_reviewed_words


class LabelStudioImportTests(unittest.TestCase):
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

    def test_reimport_is_idempotent_and_conflict_rolls_back(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _database(root / "db.sqlite")
            backup = _backup(root / "backup.json", [_task("вакыт", "waqıt", "N")])
            first = import_labelstudio_annotations(db_path, backup)
            second = import_labelstudio_annotations(db_path, backup)
            conflict = _backup(
                root / "conflict.json",
                [_task("вакыт", "vaqıt", "RL")],
            )
            with self.assertRaisesRegex(LabelStudioImportError, "reviewed word conflict"):
                import_labelstudio_annotations(db_path, conflict)

        self.assertEqual(first.imported_items, 1)
        self.assertEqual(second.unchanged_items, 1)

    def test_rejects_array_missing_project_key_and_missing_origin(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task = _task("вакыт", "waqıt", "N")
            array_path = root / "array.json"
            array_path.write_text(json.dumps([task]), encoding="utf-8")
            with self.assertRaisesRegex(LabelStudioImportError, "task API response schema"):
                parse_labelstudio_export(array_path)

            del task["data"]["project_key"]
            with self.assertRaisesRegex(LabelStudioImportError, "project_key"):
                parse_labelstudio_export(_backup(root / "no-key.json", [task]))

            task = _task("вакыт", "waqıt", "N")
            task["annotations"][0]["result"] = task["annotations"][0]["result"][1:]
            with self.assertRaisesRegex(LabelStudioImportError, "reviewed_origin"):
                parse_labelstudio_export(_backup(root / "no-origin.json", [task]))

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
            changed["annotations"][0]["result"][1]["value"]["text"] = [
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
        (json.dumps([{"text": token, "label": "RL"}], ensure_ascii=False),),
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
        "id": f"task_{word}",
        "project_key": project_key,
        "project_title": (
            "Contextual homonyms"
            if project_key == "contextual_homonym"
            else "Catchall word review"
        ),
        "batch_id": f"{project_key}_batch_001",
        "batch_index": 1,
        "batch_total": 1,
        "cyrl_word": word,
        "auto_zamanalif": zamanalif,
        "gemini_origin": origin,
    }
    if project_key == "contextual_homonym":
        data["sample_id"] = sample_id
        data["token_index"] = token_index
    if annotations is None:
        annotations = [
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
    return {"id": data["id"], "data": data, "annotations": annotations}


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


if __name__ == "__main__":
    unittest.main()
