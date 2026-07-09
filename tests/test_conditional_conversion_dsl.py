from __future__ import annotations

import unittest

from tatar_preannotator.conversion import PDF_COMPACT_POLICY, PREFERRED_POLICY, resolve_dsl
from tatar_preannotator.word_export import (
    conversion_branches,
    convert_for_annotation,
    convert_for_annotation_dsl,
)


class ConditionalConversionDslTests(unittest.TestCase):
    def assert_origin_independent(self, word: str, expected: str) -> None:
        branches = conversion_branches(word)

        self.assertEqual(branches.state, "origin_independent")
        self.assertEqual(branches.native_dsl, expected)
        self.assertEqual(branches.loanword_dsl, expected)

    def assert_origin_dependent(self, word: str, native: str, loanword: str) -> None:
        branches = conversion_branches(word)

        self.assertEqual(branches.state, "origin_dependent")
        self.assertEqual(branches.native_dsl, native)
        self.assertEqual(branches.loanword_dsl, loanword)

    def test_iya_is_the_v1_policy_dsl_rule(self) -> None:
        dsl = convert_for_annotation_dsl("орфография", "RL")

        self.assertEqual(dsl, "orfografi{{IYA|compact=ä|explicit=yä}}")
        self.assertEqual(resolve_dsl(dsl, PREFERRED_POLICY), "orfografiyä")
        self.assertEqual(resolve_dsl(dsl, PDF_COMPACT_POLICY), "orfografiä")

    def test_iya_dsl_marks_only_the_differing_span(self) -> None:
        dsl = convert_for_annotation_dsl("әдәбият", "N")

        self.assertEqual(dsl, "ädäbi{{IYA|compact=ä|explicit=yä}}t")
        self.assertEqual(resolve_dsl(dsl, PREFERRED_POLICY), "ädäbiyät")
        self.assertEqual(resolve_dsl(dsl, PDF_COMPACT_POLICY), "ädäbiät")

    def test_origin_branch_difference_is_not_inline_dsl(self) -> None:
        branches = conversion_branches("авыл")

        self.assertEqual(branches.state, "origin_dependent")
        self.assertEqual(branches.native_dsl, "awıl")
        self.assertEqual(branches.loanword_dsl, "avıl")
        self.assertNotIn("{{", branches.native_dsl)
        self.assertNotIn("{{", branches.loanword_dsl)

    def test_general_conditional_rules_still_produce_plain_suggestions(self) -> None:
        cases = [
            ("вакыт", "N", "waqıt"),
            ("проект", "RL", "proyekt"),
            ("позиция", "RL", "pozitsiä"),
            ("яңа", "N", "yaña"),
            ("ел", "N", "yıl"),
            ("юл", "N", "yul"),
            ("юкә", "N", "yükä"),
            ("тию", "N", "tiyü"),
            ("пицца", "RL", "pitsa"),
            ("меццо", "RL", "metso"),
        ]

        for word, label, expected in cases:
            with self.subTest(word=word, label=label):
                self.assertEqual(convert_for_annotation(word, label), expected)

    def test_deterministic_words_are_origin_independent(self) -> None:
        for word, expected in [
            ("шәһәр", "şähär"),
            ("әни", "äni"),
            ("зәңгәр", "zäñgär"),
            ("бала", "bala"),
        ]:
            with self.subTest(word=word):
                self.assert_origin_independent(word, expected)

    def test_origin_dependent_words_go_to_dictionary_review(self) -> None:
        cases = [
            ("авыл", "awıl", "avıl"),
            ("актив", "aqtiw", "aktiv"),
            ("вакыт", "waqıt", "vakıt"),
            ("проект", "proyıkt", "proyekt"),
        ]

        for word, native, loanword in cases:
            with self.subTest(word=word):
                self.assert_origin_dependent(word, native, loanword)

    def test_legacy_lexical_cases_are_not_encoded_as_policy_dsl(self) -> None:
        for word, label in [
            ("мәгънә", "N"),
            ("җәмәгать", "N"),
            ("шигырь", "N"),
            ("мордва-ерзя", "RL"),
            ("мәшгуль", "N"),
        ]:
            with self.subTest(word=word, label=label):
                dsl = convert_for_annotation_dsl(word, label)
                self.assertNotIn("{{", dsl)

    def test_legacy_lexical_cases_remain_review_items(self) -> None:
        for word in [
            "мәгънә",
            "җәмәгать",
            "шигырь",
            "мәшгуль",
            "кәгазъ",
            "башка",
            "ияк",
        ]:
            with self.subTest(word=word):
                self.assertNotEqual(conversion_branches(word).state, "origin_independent")

    def test_excluded_pdf_policies_are_not_registered_as_dsl(self) -> None:
        for word in ["гыйнвар", "февраль", "ноябрь", "декабрь", "мәшгуль"]:
            with self.subTest(word=word):
                for label in ("N", "RL"):
                    self.assertNotIn("{{", convert_for_annotation_dsl(word, label))


if __name__ == "__main__":
    unittest.main()
