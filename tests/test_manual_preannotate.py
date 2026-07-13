from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.manual_preannotate import (
    EditableToken,
    ManualPreannotateError,
    apply_token_command,
    run_manual_preannotation,
    tokenize_sentence,
)


class ManualPreannotateTests(unittest.TestCase):
    def test_tokenize_sentence_uses_exact_cyrillic_word_runs(self) -> None:
        text = "«Яңа тормыш»та 1909 елда К.Насыйриның фикере әйтелә."

        self.assertEqual(
            tokenize_sentence(text),
            ["Яңа", "тормыш", "та", "елда", "К", "Насыйриның", "фикере", "әйтелә"],
        )

    def test_apply_token_command_sets_labels_ranges_and_homonym(self) -> None:
        tokens = [
            EditableToken("Мин", "N"),
            EditableToken("проект", "U"),
            EditableToken("турында", "N"),
        ]

        apply_token_command(tokens, "2=RL")
        apply_token_command(tokens, "2h")
        apply_token_command(tokens, "1-3=U")

        self.assertEqual([token.label for token in tokens], ["U", "U", "U"])
        self.assertFalse(tokens[1].homonym)

    def test_homonym_requires_rl_label(self) -> None:
        with self.assertRaisesRegex(ManualPreannotateError, "homonym"):
            apply_token_command([EditableToken("Мин", "N")], "1h")

    def test_run_manual_preannotation_saves_non_tatar_sentence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.sqlite"
            _create_db(db_path, [("sent_1", "Это русское предложение.")])
            inputs = iter(["n"])

            summary = run_manual_preannotation(
                str(db_path),
                input_func=lambda _: next(inputs),
                output=lambda _: None,
            )

            row = _state_row(db_path, "sent_1")
        self.assertEqual(summary.reviewed, 1)
        self.assertEqual(row["status"], "annotated")
        self.assertEqual(row["tatar"], 0)
        self.assertEqual(json.loads(row["tokens_json"]), [])
        self.assertEqual(row["annotated_by_model"], "manual-cli")

    def test_run_manual_preannotation_edits_and_saves_tatar_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.sqlite"
            _create_db(db_path, [("sent_1", "Мин проект турында әйттем.")])
            inputs = iter(["t", "2=RL", ""])

            summary = run_manual_preannotation(
                str(db_path),
                input_func=lambda _: next(inputs),
                output=lambda _: None,
            )

            row = _state_row(db_path, "sent_1")
            tokens = json.loads(row["tokens_json"])

        self.assertEqual(summary.saved_tatar, 1)
        self.assertEqual(row["status"], "annotated")
        self.assertEqual(row["tatar"], 1)
        self.assertEqual([token["text"] for token in tokens], ["Мин", "проект", "турында", "әйттем"])
        self.assertEqual(tokens[1]["label"], "RL")
        self.assertEqual(row["annotated_by_model"], "manual-cli")

    def test_run_manual_preannotation_can_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.sqlite"
            _create_db(db_path, [("sent_1", "Мин проект турында әйттем.")])
            inputs = iter(["s"])

            summary = run_manual_preannotation(
                str(db_path),
                input_func=lambda _: next(inputs),
                output=lambda _: None,
            )

            row = _state_row(db_path, "sent_1")
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(row["status"], "unprocessable")


def _create_db(db_path: Path, samples: list[tuple[str, str]]) -> None:
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
              updated_at text not null,
              annotated_by_model text
            )
            """
        )
        for sample_id, text in samples:
            conn.execute(
                "insert into samples(id, source_id, text) values (?, null, ?)",
                (sample_id, text),
            )
            conn.execute(
                """
                insert into preannotation_state(
                  sample_id, status, tatar, tokens_json, attempts, last_error, updated_at
                ) values (?, 'unprocessable', null, null, 1, 'test error', 'now')
                """,
                (sample_id,),
            )


def _state_row(db_path: Path, sample_id: str) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "select * from preannotation_state where sample_id = ?",
            (sample_id,),
        ).fetchone()
    finally:
        conn.close()


if __name__ == "__main__":
    unittest.main()
