from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.local_repair import LOCAL_REPAIR_MODEL_NAME, repair_unprocessable


class LocalRepairTests(unittest.TestCase):
    def test_repairs_unprocessable_tatar_row_and_allows_unknown_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_db(
                Path(tmpdir) / "zamanalif.sqlite",
                annotated=[
                    ("sent_ok", "Әни килде.", [{"text": "Әни", "label": "N"}]),
                ],
                unprocessable=[
                    ("sent_bad", "Әни әни Авыл.", "invalid response"),
                ],
            )

            summary = repair_unprocessable(db_path)
            row = _state_row(db_path, "sent_bad")
            tokens = json.loads(row["tokens_json"])

        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.repaired, 1)
        self.assertEqual(row["status"], "annotated")
        self.assertEqual(row["annotated_by_model"], LOCAL_REPAIR_MODEL_NAME)
        self.assertEqual([token["text"] for token in tokens], ["Әни", "әни", "Авыл"])
        self.assertEqual(tokens[-1]["label"], "U")

    def test_dry_run_does_not_mutate_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_db(
                Path(tmpdir) / "zamanalif.sqlite",
                annotated=[],
                unprocessable=[("sent_bad", "Әни әни Авыл.", "invalid response")],
            )

            summary = repair_unprocessable(db_path, dry_run=True)
            row = _state_row(db_path, "sent_bad")

        self.assertTrue(summary.dry_run)
        self.assertEqual(summary.repaired, 1)
        self.assertEqual(row["status"], "unprocessable")
        self.assertIsNone(row["tokens_json"])

    def test_skips_rows_with_too_few_tatar_specific_letters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_db(
                Path(tmpdir) / "zamanalif.sqlite",
                annotated=[],
                unprocessable=[("sent_bad", "Русский текст.", "invalid response")],
            )

            summary = repair_unprocessable(db_path)
            row = _state_row(db_path, "sent_bad")

        self.assertEqual(summary.repaired, 0)
        self.assertEqual(summary.skipped_low_tatar_specific, 1)
        self.assertEqual(row["status"], "unprocessable")

    def test_limit_repairs_only_requested_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_db(
                Path(tmpdir) / "zamanalif.sqlite",
                annotated=[],
                unprocessable=[
                    ("sent_1", "Әни әни Авыл.", "invalid response"),
                    ("sent_2", "Әти әти Авыл.", "invalid response"),
                ],
            )

            summary = repair_unprocessable(db_path, limit=1)

        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.repaired, 1)
        self.assertEqual(summary.remaining, 1)


def _write_db(
    path: Path,
    *,
    annotated: list[tuple[str, str, list[dict]]],
    unprocessable: list[tuple[str, str, str]],
) -> Path:
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
        for sample_id, text, tokens in annotated:
            conn.execute(
                "insert into samples(id, source_id, text) values (?, 'src', ?)",
                (sample_id, text),
            )
            conn.execute(
                """
                insert into preannotation_state(
                    sample_id, status, tatar, tokens_json, updated_at
                ) values (?, 'annotated', 1, ?, 'now')
                """,
                (sample_id, json.dumps(tokens, ensure_ascii=False)),
            )
        for sample_id, text, error in unprocessable:
            conn.execute(
                "insert into samples(id, source_id, text) values (?, 'src', ?)",
                (sample_id, text),
            )
            conn.execute(
                """
                insert into preannotation_state(
                    sample_id, status, tatar, tokens_json, attempts, last_error, updated_at
                ) values (?, 'unprocessable', null, null, 1, ?, 'now')
                """,
                (sample_id, error),
            )
    return path


def _state_row(path: Path, sample_id: str) -> sqlite3.Row:
    conn = sqlite3.connect(path)
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
