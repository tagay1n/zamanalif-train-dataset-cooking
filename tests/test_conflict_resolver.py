from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.conflict_resolver import (
    ConflictReviewService,
    build_conflict_candidates,
    load_word_resolutions,
)


class ConflictResolverTests(unittest.TestCase):
    def test_detects_label_and_homonym_conflicts(self) -> None:
        candidates = build_conflict_candidates(
            [
                {
                    "id": "sent_1",
                    "text": "Һәм килде.",
                    "tokens_json": json.dumps(
                        [{"text": "Һәм", "label": "N"}], ensure_ascii=False
                    ),
                },
                {
                    "id": "sent_2",
                    "text": "Һәм китте.",
                    "tokens_json": json.dumps(
                        [{"text": "Һәм", "label": "RL", "homonym": True}],
                        ensure_ascii=False,
                    ),
                },
            ]
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.normalized_word, "һәм")
        self.assertEqual(candidate.label_counts["N"], 1)
        self.assertEqual(candidate.label_counts["RL"], 1)
        self.assertEqual(candidate.homonym_counts[True], 1)
        self.assertEqual(candidate.homonym_counts[False], 1)
        self.assertIn("RL:homonym", candidate.examples)

    def test_service_saves_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    ("sent_1", "Һәм килде.", [{"text": "Һәм", "label": "N"}]),
                    (
                        "sent_2",
                        "Һәм китте.",
                        [{"text": "Һәм", "label": "RL", "homonym": True}],
                    ),
                ],
            )
            service = ConflictReviewService(db_path)
            try:
                item = service.item(0)
                result = service.save({"word": "һәм", "decision": "N"})
            finally:
                service.close()
            resolutions = load_word_resolutions(db_path)

        self.assertEqual(item["candidate"]["word"], "һәм")
        self.assertTrue(result["ok"])
        self.assertEqual(resolutions["һәм"].decision, "N")


def _write_db(path: Path, rows: list[tuple[str, str, list[dict]]]) -> Path:
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
        for sample_id, text, tokens in rows:
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
    return path


if __name__ == "__main__":
    unittest.main()
