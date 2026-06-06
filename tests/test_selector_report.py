from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from zamanalif_selector.report import build_report
from zamanalif_selector.progress import NullProgress
from zamanalif_selector.selector import (
    score_candidates,
    select_candidates,
    select_candidates_adaptive,
    select_candidates_streaming,
)


class SelectorReportTests(unittest.TestCase):
    def test_same_seed_produces_same_output_order(self) -> None:
        rows = [
            {"id": "a", "source_sentence_index": 0, "genre": "news", "sentence": "ел килә вакыт бара."},
            {"id": "b", "source_sentence_index": 0, "genre": "news", "sentence": "юл кала дәүләт күрә."},
            {"id": "c", "source_sentence_index": 0, "genre": "essay", "sentence": "әни җырлый матур итеп."},
        ]

        first = select_candidates(score_candidates(rows, seed=7), target_size=3)
        second = select_candidates(score_candidates(rows, seed=7), target_size=3)

        self.assertEqual(
            [row["sentence"] for row in first],
            [row["sentence"] for row in second],
        )
        self.assertNotIn("genre", first[0])
        self.assertNotIn("publish_year", first[0])

    def test_progress_does_not_change_scoring_order(self) -> None:
        rows = [
            {"id": "a", "source_sentence_index": 0, "genre": "news", "sentence": "ел килә вакыт бара."},
            {"id": "b", "source_sentence_index": 0, "genre": "news", "sentence": "юл кала дәүләт күрә."},
            {"id": "c", "source_sentence_index": 0, "genre": "essay", "sentence": "әни җырлый матур итеп."},
        ]

        without_progress = select_candidates(score_candidates(rows, seed=7), target_size=3)
        with_progress = select_candidates(
            score_candidates(rows, seed=7, progress=NullProgress()),
            target_size=3,
        )

        self.assertEqual(
            [row["sentence"] for row in without_progress],
            [row["sentence"] for row in with_progress],
        )

    def test_report_contains_conditional_letter_coverage(self) -> None:
        rows = [
            {"id": "a", "source_sentence_index": 0, "genre": "news", "sentence": "ел килә вакыт бара."},
            {"id": "b", "source_sentence_index": 0, "genre": "news", "sentence": "юл гадел дәүләт күрә."},
        ]
        selected = select_candidates(score_candidates(rows, seed=3), target_size=2)
        report = build_report(selected, seed=3, config={"target_size": 2})

        self.assertIn("conditional_letter_coverage", report)
        self.assertIn("е", report["conditional_letter_coverage"])
        self.assertGreater(report["conditional_letter_coverage"]["е"]["sentence_count"], 0)
        self.assertGreater(report["sentences_with_at_least_one_conditional_letter"], 0)
        self.assertIn("conditional_letter_count_distribution", report)
        self.assertIn("vowel_harmony_coverage", report)
        self.assertGreater(report["mixed_harmony_sentence_count"], 0)
        self.assertGreater(report["mixed_harmony_conditional_sentence_count"], 0)
        self.assertIn("top_mixed_harmony_words_selected", report)
        self.assertIn("top_mixed_harmony_conditional_words_selected", report)
        self.assertNotIn("genre_distribution", report)
        self.assertNotIn("publish_year_distribution", report)

    def test_adaptive_selection_prefers_new_contexts(self) -> None:
        rows = [
            {"id": "a", "source_sentence_index": 0, "sentence": "Ел вакыт вакыт вакыт бара."},
            {"id": "b", "source_sentence_index": 0, "sentence": "Ел вакыт вакыт вакыт килә."},
            {"id": "c", "source_sentence_index": 0, "sentence": "Юл кырыенда дәүләт күрә."},
        ]

        selected = select_candidates_adaptive(score_candidates(rows, seed=11), target_size=2)

        self.assertEqual(len(selected), 2)
        self.assertTrue(any("Юл" in row["sentence"] for row in selected))
        self.assertTrue(all("adaptive_gain" in row for row in selected))

    def test_streaming_selection_is_deterministic(self) -> None:
        rows = [
            {"id": "a", "source_sentence_index": 0, "sentence": "ел килә вакыт бара."},
            {"id": "b", "source_sentence_index": 0, "sentence": "юл гадел дәүләт күрә һәм шәһәргә бара."},
            {"id": "c", "source_sentence_index": 0, "sentence": "әни җырлый матур итеп."},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "candidates.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

            first = select_candidates_streaming(path, target_size=2, seed=17, shortlist_size=10)
            second = select_candidates_streaming(path, target_size=2, seed=17, shortlist_size=10)

        self.assertEqual(
            [row["sentence"] for row in first.selected],
            [row["sentence"] for row in second.selected],
        )
        self.assertEqual(first.total_candidates, 2)
        self.assertLessEqual(first.shortlist_size, 10)

    def test_streaming_selection_filters_rows_without_two_tatar_specific_letters(self) -> None:
        rows = [
            {
                "id": "ru",
                "source_sentence_index": 0,
                "sentence": "В случае обнаружения технической ошибки заявитель представляет документы.",
                "tatar_score": 999,
                "russian_score": 0,
                "language_reason": "stale_candidate_metadata",
            },
            {
                "id": "tt",
                "source_sentence_index": 0,
                "sentence": "Әни шәһәргә бара һәм яңа сүзләр өйрәнә.",
                "tatar_score": 999,
                "russian_score": 0,
                "language_reason": "stale_candidate_metadata",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "candidates.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

            result = select_candidates_streaming(path, target_size=2, seed=17, shortlist_size=10)

        self.assertEqual(result.total_candidates, 1)
        self.assertEqual(len(result.selected), 1)
        self.assertIn("Әни", result.selected[0]["sentence"])
        self.assertGreaterEqual(result.selected[0]["tatar_specific_letter_count"], 2)
        self.assertNotIn("tatar_score", result.selected[0])
        self.assertNotIn("russian_score", result.selected[0])
        self.assertNotIn("language_reason", result.selected[0])

    def test_streaming_selection_filters_artifact_rows(self) -> None:
        rows = [
            {
                "id": "artifact",
                "source_sentence_index": 0,
                "sentence": "## ВЕРХОВНЫЙ БАШ КОМАНДУЮЩИЙ ПРИКАЗЫ ### гаскәрләренә шәһәргә.",
            },
            {
                "id": "tt",
                "source_sentence_index": 1,
                "sentence": "Әни шәһәргә бара һәм яңа сүзләр өйрәнә.",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "candidates.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

            result = select_candidates_streaming(path, target_size=2, seed=17, shortlist_size=10)

        self.assertEqual(result.total_candidates, 1)
        self.assertEqual(len(result.selected), 1)
        self.assertIn("Әни", result.selected[0]["sentence"])
        self.assertIn("quality_penalty", result.selected[0])
        self.assertIn("quality_reasons", result.selected[0])

    def test_report_contains_adaptive_diversity_fields(self) -> None:
        rows = [
            {"id": "a", "source_sentence_index": 0, "sentence": "ел килә вакыт бара."},
            {"id": "b", "source_sentence_index": 0, "sentence": "юл гадел дәүләт күрә."},
        ]
        selected = select_candidates_adaptive(score_candidates(rows, seed=3), target_size=2)
        report = build_report(
            selected,
            seed=3,
            config={"target_size": 2, "selection_strategy": "adaptive_coverage"},
        )

        self.assertEqual(report["selection_strategy"], "adaptive_coverage")
        self.assertIn("unique_conditional_words_selected", report)
        self.assertIn("unique_conditional_contexts_selected", report)
        self.assertIn("conditional_word_saturation", report)
        self.assertIn("source_repetition_summary", report)
        self.assertIn("language_filter", report)
        self.assertIn("tatar_specific_letter_filter", report)
        self.assertIn("quality_filter", report)
        self.assertNotIn("tatar_score_distribution", report)
        self.assertNotIn("language_reason_distribution", report)
        self.assertNotIn("russian_like_selected_count", report)


if __name__ == "__main__":
    unittest.main()
