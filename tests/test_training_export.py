from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.cli import main
from tatar_preannotator.training_export import (
    TrainingExportError,
    export_training_dataset,
    parse_policy_overrides,
)
from tatar_preannotator.word_export import save_reviewed_word


class TrainingExportTests(unittest.TestCase):
    def test_default_policy_exports_plain_jsonl_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_000001",
                        "text": "Орфография һәм ШӘҺӘР.",
                        "tokens": [
                            {"text": "Орфография", "label": "RL"},
                            {"text": "һәм", "label": "N"},
                            {"text": "ШӘҺӘР", "label": "N"},
                        ],
                    }
                ],
            )
            save_reviewed_word(
                db_path,
                "орфография",
                "orfografi{{IYA|compact=ä|explicit=yä}}",
                "RL",
            )
            output = root / "train.jsonl"

            summary = export_training_dataset(db_path, output)
            rows = _read_jsonl(output)
            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            rows,
            [
                {
                    "id": "sent_000001",
                    "cyrillic": "Орфография һәм ШӘҺӘР.",
                    "zamanalif": "Orfografiyä häm ŞÄHÄR.",
                }
            ],
        )
        self.assertNotIn("{{", rows[0]["zamanalif"])
        self.assertEqual(
            manifest["effective_policy"],
            {
                "IYA": "explicit",
                "ARABIC_INITIAL_GA": "plain",
                "GIY_COMPACT": "plain",
                "IE_GLIDE": "plain",
                "RUS_SIGN_GLIDE": "omit",
                "RUS_SOFT_SIGN": "preserve",
                "RUS_JOTATED_SOFTENING": "glide",
                "RL_FINAL_KA": "suffix",
                "NATIVE_UW": "glide",
            },
        )
        self.assertEqual(manifest["overrides"], {})
        self.assertEqual(manifest["counts"]["exported_sentences"], 1)

    def test_cli_choice_override_selects_compact_iya(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Орфография.",
                        "tokens": [{"text": "Орфография", "label": "RL"}],
                    }
                ],
            )
            save_reviewed_word(
                db_path,
                "орфография",
                "orfografi{{IYA|compact=ä|explicit=yä}}",
                "RL",
            )
            output = root / "compact.jsonl"
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "training-export",
                        "--db",
                        str(db_path),
                        "--output",
                        str(output),
                        "--choice",
                        "IYA=compact",
                    ]
                )
            row = _read_jsonl(output)[0]
            manifest = json.loads(
                Path(str(output) + ".manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(row["zamanalif"], "Orfografiä.")
        self.assertEqual(manifest["overrides"], {"IYA": "compact"})
        self.assertIn("training export complete", stdout.getvalue())

    def test_skips_unreviewed_homonym_and_mixed_harmony_sentences(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Мин вакыт беләм.",
                        "tokens": [
                            {"text": "Мин", "label": "N"},
                            {"text": "вакыт", "label": "N"},
                            {"text": "беләм", "label": "N"},
                        ],
                    },
                    {
                        "id": "sent_2",
                        "text": "Сер калды.",
                        "tokens": [
                            {"text": "Сер", "label": "RL", "homonym": True},
                            {"text": "калды", "label": "N"},
                        ],
                    },
                    {
                        "id": "sent_3",
                        "text": "Гадел килде.",
                        "tokens": [
                            {"text": "Гадел", "label": "N"},
                            {"text": "килде", "label": "N"},
                        ],
                    },
                    {
                        "id": "sent_4",
                        "text": "МИН шат.",
                        "tokens": [
                            {"text": "МИН", "label": "N"},
                            {"text": "шат", "label": "N"},
                        ],
                    },
                ],
            )
            output = root / "train.jsonl"

            summary = export_training_dataset(db_path, output)
            rows = _read_jsonl(output)
            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual([row["id"] for row in rows], ["sent_4"])
        self.assertEqual(rows[0]["zamanalif"], "MİN şat.")
        self.assertEqual(
            manifest["skipped_by_reason"],
            {
                "contextual_homonym": 1,
                "mixed_harmony_word": 1,
                "unreviewed_word": 1,
            },
        )
        self.assertEqual(summary.skipped_count, 3)

    def test_non_tatar_sentences_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Русский текст.",
                        "tatar": False,
                        "tokens": [],
                    }
                ],
            )
            output = root / "train.jsonl"

            summary = export_training_dataset(db_path, output)
            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(output.read_text(encoding="utf-8"), "")
            self.assertEqual(manifest["counts"]["non_tatar_sentences_ignored"], 1)

    def test_origin_independent_conditional_words_need_no_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Бу юл.",
                        "tokens": [
                            {"text": "Бу", "label": "U"},
                            {"text": "юл", "label": "U"},
                        ],
                    }
                ],
            )
            output = root / "train.jsonl"

            export_training_dataset(db_path, output)
            rows = _read_jsonl(output)

        self.assertEqual(rows[0]["zamanalif"], "Bu yul.")

    def test_malformed_reviewed_dsl_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [])
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    insert into reviewed_words(normalized_word, zamanalif_dsl, origin, updated_at)
                    values ('орфография', 'x{{BROKEN}}', 'RL', 'now')
                    """
                )
            output = root / "train.jsonl"
            output.write_text("existing\n", encoding="utf-8")

            with self.assertRaisesRegex(TrainingExportError, "орфография"):
                export_training_dataset(db_path, output)

            self.assertEqual(output.read_text(encoding="utf-8"), "existing\n")
            self.assertFalse(Path(str(output) + ".manifest.json").exists())

    def test_token_alignment_error_fails_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Мин шат.",
                        "tokens": [{"text": "юк", "label": "N"}],
                    }
                ],
            )
            output = root / "train.jsonl"

            with self.assertRaisesRegex(TrainingExportError, "missing or out of order"):
                export_training_dataset(db_path, output)

        self.assertFalse(output.exists())
        self.assertFalse(Path(str(output) + ".manifest.json").exists())

    def test_omitted_cyrillic_token_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Мин шат.",
                        "tokens": [{"text": "Мин", "label": "N"}],
                    }
                ],
            )

            with self.assertRaisesRegex(TrainingExportError, "unresolved Cyrillic"):
                export_training_dataset(db_path, root / "train.jsonl")

    def test_policy_override_validation(self) -> None:
        effective, overrides = parse_policy_overrides(["IYA=compact"])
        self.assertEqual(
            effective,
            {
                "IYA": "compact",
                "ARABIC_INITIAL_GA": "plain",
                "GIY_COMPACT": "plain",
                "IE_GLIDE": "plain",
                "RUS_SIGN_GLIDE": "omit",
                "RUS_SOFT_SIGN": "preserve",
                "RUS_JOTATED_SOFTENING": "glide",
                "RL_FINAL_KA": "suffix",
                "NATIVE_UW": "glide",
            },
        )
        self.assertEqual(overrides, {"IYA": "compact"})

        with self.assertRaisesRegex(TrainingExportError, "duplicate"):
            parse_policy_overrides(["IYA=compact", "IYA=explicit"])
        with self.assertRaisesRegex(TrainingExportError, "unknown DSL rule"):
            parse_policy_overrides(["OTHER=value"])
        with self.assertRaisesRegex(TrainingExportError, "unknown option"):
            parse_policy_overrides(["IYA=other"])
        with self.assertRaisesRegex(TrainingExportError, "RULE=OPTION"):
            parse_policy_overrides(["IYA"])


def _write_db(path: Path, rows: list[dict]) -> Path:
    with sqlite3.connect(path) as conn:
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
            """
            create table reviewed_words (
                normalized_word text primary key,
                zamanalif_dsl text not null,
                origin text not null check(origin in ('N', 'RL', 'U')),
                updated_at text not null
            )
            """
        )
        for row in rows:
            sample_id = row["id"]
            conn.execute(
                "insert into samples(id, source_id, text) values (?, 'src', ?)",
                (sample_id, row["text"]),
            )
            conn.execute(
                """
                insert into preannotation_state(
                    sample_id, status, tatar, tokens_json, updated_at
                ) values (?, 'annotated', ?, ?, 'now')
                """,
                (
                    sample_id,
                    1 if row.get("tatar", True) else 0,
                    json.dumps(row.get("tokens", []), ensure_ascii=False),
                ),
            )
    return path


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


if __name__ == "__main__":
    unittest.main()
