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

    def test_loanword_stems_use_tatar_g_k_suffix_conversion(self) -> None:
        cases = [
            ("законга", "zakonğa"),
            ("принципларга", "prinsiplarğa"),
            ("аббревиатурадагы", "abbreviaturadağı"),
            ("архивка", "arxivqa"),
            ("алфавитка", "alfavitqa"),
            ("авторлык", "avtorlıq"),
            ("адвокатлык", "advokatlıq"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "RL"), expected)

    def test_loanword_stem_g_k_still_uses_plain_letters(self) -> None:
        for word, expected in [
            ("банк", "bank"),
            ("газет", "gazet"),
            ("график", "grafik"),
            ("кодекс", "kodeks"),
        ]:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "RL"), expected)

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
                    dsl = convert_for_annotation_dsl(word, label)
                    self.assertNotIn("MONTH", dsl)
                    self.assertNotIn("DISHARMONY", dsl)

    def test_russian_sign_before_glide_is_policy_dsl(self) -> None:
        cases = [
            ("компьютер", "komp{{RUS_SIGN_GLIDE|omit=|preserve='}}yuter", "kompyuter", "komp'yuter"),
            ("нью-йорк", "n{{RUS_SIGN_GLIDE|omit=|preserve='}}yu-york", "nyu-york", "n'yu-york"),
            ("барьер", "bar{{RUS_SIGN_GLIDE|omit=|preserve='}}yer", "baryer", "bar'yer"),
        ]

        for word, expected_dsl, omitted, preserved in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "RL")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl), omitted)
                self.assertEqual(resolve_dsl(dsl, {"RUS_SIGN_GLIDE": "preserve"}), preserved)

    def test_russian_soft_sign_is_policy_dsl(self) -> None:
        cases = [
            ("роль", "rol{{RUS_SOFT_SIGN|omit=|preserve='}}", "rol", "rol'"),
            ("культура", "kul{{RUS_SOFT_SIGN|omit=|preserve='}}tura", "kultura", "kul'tura"),
            ("секретарь", "sekretar{{RUS_SOFT_SIGN|omit=|preserve='}}", "sekretar", "sekretar'"),
            ("автомобиль", "avtomobil{{RUS_SOFT_SIGN|omit=|preserve='}}", "avtomobil", "avtomobil'"),
        ]

        for word, expected_dsl, omitted, preserved in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "RL")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl, {"RUS_SOFT_SIGN": "omit"}), omitted)
                self.assertEqual(resolve_dsl(dsl, {"RUS_SOFT_SIGN": "preserve"}), preserved)

    def test_arabic_persian_g_hard_sign_and_k_hard_sign_are_general_rules(self) -> None:
        cases = [
            ("мәгънә", "mäğnä"),
            ("игътибар", "iğtibar"),
            ("тәкъдим", "täqdim"),
            ("микъдар", "miqdar"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertNotIn("{{", convert_for_annotation_dsl(word, "N"))

    def test_native_g_follows_immediate_right_vowel_when_available(self) -> None:
        cases = [
            ("аергыч", "ayırğıç"),
            ("куелган", "quyılğan"),
            ("тыелган", "tıyılğan"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)

    def test_disputed_front_g_suffixes_remain_plain_without_policy_dsl(self) -> None:
        for word, expected in [
            ("биргән", "birgän"),
            ("эшләргә", "eşlärgä"),
        ]:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertNotIn("{{", convert_for_annotation_dsl(word, "N"))


if __name__ == "__main__":
    unittest.main()
