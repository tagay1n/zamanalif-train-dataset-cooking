from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.antat_sanity import (
    antat_converter_mismatches,
    antat_rule_coverage,
    extract_antat_word_pairs,
    format_mismatches,
    format_rule_gaps,
    infer_antat_label,
)
from tatar_preannotator.antat_reference import ensure_schema
from tatar_preannotator.antat_gold import build_antat_gold_cases


CYRILLIC_HTML = """
<html><body><p><b>abandon</b> [əˊbænd(ə)n] <i>v</i>
1) ташлап китәргә, ташларга; 2) баш тартырга</p></body></html>
"""

ZAMANALIF_HTML = """
<html><body><p><b>abandon</b> <font>[əˊbænd(ə)n]</font> <i>v</i>
1) taşlap kitärğä, taşlarğa; 2) baş tartırğa</p></body></html>
"""


class AntatSanityTests(unittest.TestCase):
    def test_extract_antat_word_pairs_from_aligned_articles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "zamanalif.sqlite"
            _write_antat_fixture(db_path)

            pairs = extract_antat_word_pairs(db_path)

        tuples = [
            (pair.cyrillic_word, pair.expected_zamanalif, pair.label, pair.headword)
            for pair in pairs
        ]
        self.assertEqual(
            tuples,
            [
                ("ташлап", "taşlap", "N", "ABANDON"),
                ("китәргә", "kitärğä", "N", "ABANDON"),
                ("ташларга", "taşlarğa", "N", "ABANDON"),
                ("баш", "baş", "N", "ABANDON"),
                ("тартырга", "tartırğa", "N", "ABANDON"),
            ],
        )

    def test_extract_antat_word_pairs_skips_unaligned_token_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "zamanalif.sqlite"
            _write_antat_fixture(
                db_path,
                cyrillic_html=CYRILLIC_HTML,
                zamanalif_html=ZAMANALIF_HTML.replace("taşlarğa", "extra taşlarğa"),
            )

            pairs = extract_antat_word_pairs(db_path)

        self.assertEqual(pairs, [])

    def test_infer_antat_label_uses_loanword_branch_for_review_letters(self) -> None:
        self.assertEqual(infer_antat_label("роль"), "RL")
        self.assertEqual(infer_antat_label("шофёр"), "RL")
        self.assertEqual(infer_antat_label("вакыт"), "N")

    def test_format_mismatches_is_reviewable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "zamanalif.sqlite"
            _write_antat_fixture(db_path)
            pairs = extract_antat_word_pairs(db_path)
            mismatches = antat_converter_mismatches(pairs)

        text = format_mismatches(mismatches, limit=1)

        self.assertIn("expected", text)
        self.assertIn("headword='ABANDON'", text)
        self.assertIn("align_id=1", text)

    def test_rule_coverage_tries_native_and_loanword_branches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "zamanalif.sqlite"
            _write_antat_fixture(
                db_path,
                cyrillic_html="""
                <html><body><p><b>sample</b> <i>n</i>
                вакыт, проект, мәгънәле, фамилия</p></body></html>
                """,
                zamanalif_html="""
                <html><body><p><b>sample</b> <i>n</i>
                waqıt, proyekt, mäğnäle, familiyä</p></body></html>
                """,
            )
            pairs = extract_antat_word_pairs(db_path)

        coverage = antat_rule_coverage(pairs)

        self.assertEqual(
            [pair.cyrillic_word for pair in coverage.matched_native],
            ["вакыт", "мәгънәле"],
        )
        self.assertEqual([pair.cyrillic_word for pair in coverage.matched_loanword], ["проект"])
        self.assertEqual([pair.cyrillic_word for pair in coverage.matched_both], ["фамилия"])
        self.assertEqual(coverage.rule_gaps, [])
        self.assertEqual(
            coverage.summary(),
            {
                "matched_native": 2,
                "matched_loanword": 1,
                "matched_both": 1,
                "rule_gaps": 0,
                "total": 4,
            },
        )

    def test_build_antat_gold_cases_deduplicates_pairs_and_keeps_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "zamanalif.sqlite"
            _write_antat_fixture(
                db_path,
                cyrillic_html="""
                <html><body><p><b>sample</b> <i>n</i>
                канат, канат, канат, сүз</p></body></html>
                """,
                zamanalif_html="""
                <html><body><p><b>sample</b> <i>n</i>
                qanat, qanat, kanat, səz</p></body></html>
                """,
            )

            result = build_antat_gold_cases(db_path)

        self.assertEqual(
            [(case.cyrillic_word, case.expected_zamanalif) for case in result.cases],
            [("канат", "kanat"), ("канат", "qanat")],
        )
        self.assertEqual(result.skipped_non_zamanalif, 1)

    def test_downloaded_antat_pairs_are_covered_by_some_converter_branch(self) -> None:
        if os.environ.get("RUN_ANTAT_FULL_COVERAGE") != "1":
            self.skipTest("set RUN_ANTAT_FULL_COVERAGE=1 to audit downloaded Antat pairs")
        db_path = Path("data/zamanalif.sqlite")
        pairs = extract_antat_word_pairs(db_path)

        self.assertGreater(len(pairs), 1000)
        coverage = antat_rule_coverage(pairs)
        if coverage.rule_gaps:
            self.fail(f"coverage={coverage.summary()}\n{format_rule_gaps(coverage.rule_gaps)}")


def _write_antat_fixture(
    db_path: Path,
    *,
    cyrillic_html: str = CYRILLIC_HTML,
    zamanalif_html: str = ZAMANALIF_HTML,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        conn.execute(
            """
            insert into antat_entry_pages(
                source_id, entry_id, headword, html, plain_text, fetched_at
            ) values (29, 'c1', 'ABANDON', ?, '', 'now')
            """,
            (cyrillic_html,),
        )
        conn.execute(
            """
            insert into antat_entry_pages(
                source_id, entry_id, headword, html, plain_text, fetched_at
            ) values (30, 'z1', 'ABANDON', ?, '', 'now')
            """,
            (zamanalif_html,),
        )
        conn.execute(
            """
            insert into antat_aligned_entries(
                page, position, headword, cyrillic_entry_id, zamanalif_entry_id,
                cyrillic_text, zamanalif_text, status
            ) values (1, 1, 'ABANDON', 'c1', 'z1', '', '', 'aligned')
            """
        )
        conn.commit()


if __name__ == "__main__":
    unittest.main()
