from __future__ import annotations

from contextlib import redirect_stdout
from collections import Counter
from io import StringIO
import random
import sqlite3
import tempfile
import unittest

from zamanalif_selector.cli import (
    _candidate_keep_reason,
    _diagnostic_summary,
    _document_windows,
    _prepare_source_rows,
    _write_selected_sqlite,
    main,
)


class CliTests(unittest.TestCase):
    def test_prepare_help_includes_quiet(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(output):
            main(["prepare", "--help"])

        self.assertIn("--quiet", output.getvalue())
        self.assertIn("--max-candidates", output.getvalue())
        self.assertIn("--window-chars", output.getvalue())
        self.assertIn("--exhaustive", output.getvalue())
        self.assertIn("--min-tatar-specific-letters", output.getvalue())
        self.assertNotIn("--min-tatar-score", output.getvalue())

    def test_select_help_includes_quiet(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(output):
            main(["select", "--help"])

        self.assertIn("--quiet", output.getvalue())
        self.assertIn("--shortlist-size", output.getvalue())
        self.assertIn("--source-penalty", output.getvalue())
        self.assertIn("--force", output.getvalue())
        self.assertIn("--min-tatar-specific-letters", output.getvalue())
        self.assertNotIn("--audit-sample", output.getvalue())
        self.assertNotIn("--min-tatar-score", output.getvalue())
        self.assertNotIn("--allow-russian-ratio", output.getvalue())

    def test_report_help_includes_quiet(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(output):
            main(["report", "--help"])

        self.assertIn("--quiet", output.getvalue())

    def test_candidate_keep_reason_prioritizes_valuable_sentences(self) -> None:
        rng = random.Random(13)

        self.assertEqual(
            _candidate_keep_reason(
                "Ел башында вакыт бар.",
                exhaustive=False,
                general_keep_probability=0.0,
                rng=rng,
            ),
            "conditional",
        )
        self.assertEqual(
            _candidate_keep_reason(
                "Заһир тәмам.",
                exhaustive=False,
                general_keep_probability=0.0,
                rng=rng,
            ),
            "mixed_harmony",
        )
        self.assertIsNone(
            _candidate_keep_reason(
                "Әни һаман җырлый.",
                exhaustive=False,
                general_keep_probability=0.0,
                rng=rng,
            )
        )
        self.assertEqual(
            _candidate_keep_reason(
                "Әни һаман җырлый.",
                exhaustive=True,
                general_keep_probability=0.0,
                rng=rng,
            ),
            "exhaustive",
        )

    def test_diagnostic_summary_uses_compact_labels(self) -> None:
        summary = _diagnostic_summary(
            [{}] * 12,
            Counter({
                "prefilter:kept:conditional": 5,
                "prefilter:kept:mixed_harmony": 3,
                "prefilter:kept:general_sample": 2,
                "prefilter:skipped_general": 100,
                "prefilter:skipped_doc_cap": 7,
            }),
        )

        self.assertIn("cand=12", summary)
        self.assertIn("cond=5", summary)
        self.assertIn("mix=3", summary)
        self.assertNotIn("prefilter:", summary)

    def test_document_windows_cover_bounded_large_text(self) -> None:
        text = "а" * 10_000
        windows = _document_windows(
            text,
            exhaustive=False,
            max_doc_chars=2_000,
            window_chars=500,
            windows_per_doc=4,
            rng=random.Random(13),
        )

        self.assertEqual(len(windows), 4)
        self.assertEqual(windows[0][1], 0)
        self.assertEqual(windows[-1][1], 9_500)
        self.assertTrue(all(len(window_text) <= 500 for _, _, window_text in windows))

    def test_document_windows_use_full_short_text(self) -> None:
        text = "Ел башында вакыт бар."
        windows = _document_windows(
            text,
            exhaustive=False,
            max_doc_chars=2_000,
            window_chars=500,
            windows_per_doc=4,
            rng=random.Random(13),
        )

        self.assertEqual(windows, [(0, 0, text)])

    def test_prepare_source_rows_caps_prioritizes_offsets_and_deduplicates(self) -> None:
        text = (
            "Әни һаман җырлый. "
            "Ел башында шәһәрдә вакыт бар. "
            "Заһир тәмам. "
            "Ел башында шәһәрдә вакыт бар. "
            "Юл турында хәбәрләр бар."
        )
        diagnostics = Counter()
        rows = _prepare_source_rows(
            {"id": "doc1", "publish_year": 2020, "genre": "news"},
            text,
            exhaustive=False,
            min_chars=1,
            max_chars=200,
            max_doc_chars=10_000,
            window_chars=1_000,
            windows_per_doc=1,
            max_candidates_per_doc=2,
            max_candidates_for_doc=None,
            general_keep_probability=1.0,
            min_tatar_specific_letters=2,
            rng=random.Random(13),
            seen_sentences=set(),
            diagnostics=diagnostics,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual([row["reason"] for row in rows], ["conditional", "conditional"])
        self.assertEqual([row["index"] for row in rows], [0, 0])
        self.assertNotIn("genre", rows[0])
        self.assertNotIn("publish_year", rows[0])
        self.assertNotIn("prepare_keep_reason", rows[0])
        self.assertNotIn("prepare_window_index", rows[0])
        self.assertIn("tatar_specific_letter_count", rows[0])
        self.assertIn("tatar_specific_letters", rows[0])
        self.assertNotIn("tatar_score", rows[0])
        self.assertNotIn("russian_score", rows[0])
        self.assertNotIn("language_reason", rows[0])
        self.assertEqual(rows[0]["source_start_char"], text.index("Ел"))
        self.assertGreater(diagnostics["prefilter:skipped_doc_duplicate"], 0)
        self.assertGreater(diagnostics["prefilter:skipped_doc_cap"], 0)

    def test_prepare_source_rows_skips_sentences_without_two_tatar_specific_letters(self) -> None:
        diagnostics = Counter()
        rows = _prepare_source_rows(
            {"id": "doc1"},
            "В случае обнаружения технической ошибки заявитель представляет документы. Әни шәһәргә бара.",
            exhaustive=False,
            min_chars=1,
            max_chars=200,
            max_doc_chars=10_000,
            window_chars=1_000,
            windows_per_doc=1,
            max_candidates_per_doc=10,
            max_candidates_for_doc=None,
            general_keep_probability=1.0,
            min_tatar_specific_letters=2,
            rng=random.Random(13),
            seen_sentences=set(),
            diagnostics=diagnostics,
        )

        self.assertEqual(len(rows), 1)
        self.assertIn("Әни", rows[0]["sentence"])
        self.assertGreater(diagnostics["prefilter:skipped_language"], 0)

    def test_prepare_source_rows_skips_quality_artifacts(self) -> None:
        diagnostics = Counter()
        rows = _prepare_source_rows(
            {"id": "doc1"},
            (
                "## ВЕРХОВНЫЙ БАШ КОМАНДУЮЩИЙ ПРИКАЗЫ ### гаскәрләренә шәһәргә. "
                "Әни шәһәргә бара һәм яңа сүзләр өйрәнә."
            ),
            exhaustive=False,
            min_chars=1,
            max_chars=200,
            max_doc_chars=10_000,
            window_chars=1_000,
            windows_per_doc=1,
            max_candidates_per_doc=10,
            max_candidates_for_doc=None,
            general_keep_probability=1.0,
            min_tatar_specific_letters=2,
            rng=random.Random(13),
            seen_sentences=set(),
            diagnostics=diagnostics,
        )

        self.assertEqual(len(rows), 1)
        self.assertIn("Әни", rows[0]["sentence"])
        self.assertGreater(diagnostics["prefilter:skipped_quality"], 0)

    def test_write_selected_sqlite_keeps_minimal_samples_and_state(self) -> None:
        rows = [
            {"id": "doc-a", "sentence": "Казан университетында яңа проект башланды."},
            {"id": "doc-b", "sentence": "Әни шәһәргә бара."},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/selected.sqlite"

            _write_selected_sqlite(path, rows, force=False)

            with sqlite3.connect(path) as conn:
                sample_columns = [
                    row[1] for row in conn.execute("PRAGMA table_info(samples)").fetchall()
                ]
                state_columns = [
                    row[1]
                    for row in conn.execute("PRAGMA table_info(preannotation_state)").fetchall()
                ]
                samples = conn.execute(
                    "SELECT id, source_id, text FROM samples ORDER BY id"
                ).fetchall()
                states = conn.execute(
                    "SELECT sample_id, status FROM preannotation_state ORDER BY sample_id"
                ).fetchall()

            self.assertEqual(sample_columns, ["id", "source_id", "text"])
            self.assertIn("tokens_json", state_columns)
            self.assertEqual(
                samples,
                [
                    ("sent_000001", "doc-a", "Казан университетында яңа проект башланды."),
                    ("sent_000002", "doc-b", "Әни шәһәргә бара."),
                ],
            )
            self.assertEqual(
                states,
                [("sent_000001", "pending"), ("sent_000002", "pending")],
            )

            with self.assertRaises(SystemExit):
                _write_selected_sqlite(path, rows, force=False)


if __name__ == "__main__":
    unittest.main()
