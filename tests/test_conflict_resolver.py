from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.conflict_resolver import (
    ConflictReviewService,
    HTML_PAGE,
    auto_resolve_conflicts,
    build_conflict_candidates,
    conservative_auto_decision,
    load_word_resolutions,
    save_word_resolution,
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

    def test_new_service_skips_already_resolved_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    ("sent_1", "Һәм килде. Авыл бар.", [
                        {"text": "Һәм", "label": "N"},
                        {"text": "Авыл", "label": "N"},
                    ]),
                    ("sent_2", "Һәм китте. Авыл зур.", [
                        {"text": "Һәм", "label": "RL", "homonym": True},
                        {"text": "Авыл", "label": "RL"},
                    ]),
                ],
            )
            with sqlite3.connect(db_path) as conn:
                save_word_resolution(conn, "һәм", "N")
            service = ConflictReviewService(db_path)
            try:
                item = service.item(0)
            finally:
                service.close()

        self.assertEqual(item["candidate"]["word"], "авыл")

    def test_conservative_auto_decision_resolves_low_risk_cases(self) -> None:
        independent = _candidate("йөз", {"N": 10, "U": 1})
        tiny_u = _candidate("немец", {"RL": 100, "U": 2})
        tiny_minority = _candidate("революцион", {"RL": 100, "N": 1})

        self.assertEqual(conservative_auto_decision(independent), "N")
        self.assertEqual(conservative_auto_decision(tiny_u), "RL")
        self.assertEqual(conservative_auto_decision(tiny_minority), "RL")

    def test_conservative_auto_decision_keeps_homonym_and_meaningful_conflicts(self) -> None:
        homonym = _candidate("сер", {"N": 10, "RL": 10}, homonyms={True: 1, False: 19})
        meaningful = _candidate("мәскәү", {"N": 80, "RL": 20})

        self.assertIsNone(conservative_auto_decision(homonym))
        self.assertIsNone(conservative_auto_decision(meaningful))

    def test_conservative_auto_decision_resolves_10x_dominant_origin(self) -> None:
        dominant_rl = _candidate("гражданлык", {"RL": 20, "N": 1, "U": 5})
        dominant_n = _candidate("ерткыч", {"N": 20, "RL": 1})
        too_small = _candidate("авыл", {"N": 9, "RL": 1})
        homonym = _candidate("сер", {"RL": 20, "N": 1}, homonyms={True: 1, False: 20})

        self.assertEqual(conservative_auto_decision(dominant_rl), "RL")
        self.assertEqual(conservative_auto_decision(dominant_n), "N")
        self.assertIsNone(conservative_auto_decision(too_small))
        self.assertIsNone(conservative_auto_decision(homonym))

    def test_auto_resolve_conflicts_writes_only_unresolved_low_risk_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    ("sent_1", "Йөз немец сер.", [
                        {"text": "Йөз", "label": "N"},
                        {"text": "немец", "label": "RL"},
                        {"text": "сер", "label": "N"},
                    ]),
                    ("sent_2", "Йөз немец сер.", [
                        {"text": "Йөз", "label": "U"},
                        {"text": "немец", "label": "U"},
                        {"text": "сер", "label": "RL", "homonym": True},
                    ]),
                    ("sent_3", "немец.", [{"text": "немец", "label": "RL"}]),
                    ("sent_4", "немец.", [{"text": "немец", "label": "RL"}]),
                ],
            )

            dry = auto_resolve_conflicts(db_path, dry_run=True)
            self.assertEqual(load_word_resolutions(db_path), {})

            summary = auto_resolve_conflicts(db_path)
            resolutions = load_word_resolutions(db_path)

        self.assertTrue(dry.dry_run)
        self.assertEqual(dry.auto_resolved, 2)
        self.assertEqual(summary.auto_resolved, 2)
        self.assertEqual(resolutions["йөз"].decision, "N")
        self.assertEqual(resolutions["немец"].decision, "RL")
        self.assertNotIn("сер", resolutions)

    def test_auto_resolve_conflicts_does_not_overwrite_existing_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    ("sent_1", "Йөз.", [{"text": "Йөз", "label": "N"}]),
                    ("sent_2", "Йөз.", [{"text": "Йөз", "label": "U"}]),
                ],
            )
            with sqlite3.connect(db_path) as conn:
                save_word_resolution(conn, "йөз", "U")

            summary = auto_resolve_conflicts(db_path)
            resolutions = load_word_resolutions(db_path)

        self.assertEqual(summary.inspected, 0)
        self.assertEqual(resolutions["йөз"].decision, "U")

    def test_page_has_requested_keyboard_shortcuts(self) -> None:
        self.assertIn("Keyboard: N=native", HTML_PAGE)
        self.assertIn('key === "n"', HTML_PAGE)
        self.assertIn('key === "r"', HTML_PAGE)
        self.assertIn('key === "u"', HTML_PAGE)
        self.assertIn('key === "h"', HTML_PAGE)
        self.assertIn('event.key === "ArrowLeft"', HTML_PAGE)
        self.assertIn('event.key === "ArrowRight"', HTML_PAGE)
        self.assertIn('event.key === " "', HTML_PAGE)


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


def _candidate(
    word: str,
    labels: dict[str, int],
    *,
    homonyms: dict[bool, int] | None = None,
):
    from collections import Counter

    from tatar_preannotator.conflict_resolver import ConflictCandidate

    return ConflictCandidate(
        normalized_word=word,
        frequency=sum(labels.values()),
        label_counts=Counter(labels),
        homonym_counts=Counter(homonyms or {False: sum(labels.values())}),
        examples={},
    )


if __name__ == "__main__":
    unittest.main()
