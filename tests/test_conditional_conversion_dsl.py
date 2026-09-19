from __future__ import annotations

import unittest

from tatar_preannotator.conversion import DslError, parse_dsl, resolve_dsl
from tatar_preannotator.word_export import (
    classify_project,
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

    def test_iya_is_deterministic_explicit_glide(self) -> None:
        self.assertEqual(convert_for_annotation_dsl("орфография", "RL"), "orfografiyä")
        self.assertEqual(convert_for_annotation_dsl("әдәбият", "N"), "ädäbiyat")

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
            ("позиция", "RL", "pozitsiyä"),
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

    def test_every_c_defaults_to_ts_without_inline_dsl(self) -> None:
        for word, expected in [
            ("ретроспекция", "retrospektsiyä"),
            ("немецләрне", "nemetslärne"),
            ("принцип", "printsip"),
            ("пицца", "pitsa"),
        ]:
            with self.subTest(word=word):
                suggestion = convert_for_annotation_dsl(word, "RL")
                self.assertEqual(suggestion, expected)
                self.assertNotIn("{{", suggestion)

    def test_final_ts_suffix_policy_does_not_cover_internal_root_ts(self) -> None:
        self.assertEqual(convert_for_annotation_dsl("лицей", "RL"), "litsey")

    def test_loanword_ou_is_not_general_policy_dsl(self) -> None:
        self.assertEqual(convert_for_annotation_dsl("соус", "RL"), "sous")
        self.assertEqual(convert_for_annotation_dsl("боулинг", "RL"), "bowling")

    def test_loanword_stems_use_tatar_g_k_suffix_conversion(self) -> None:
        cases = [
            ("законга", "zakonğa"),
            ("принципларга", "printsiplarğa"),
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
            ("интрига", "intriga"),
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

    def test_deterministic_lexical_cases_are_not_encoded_as_policy_dsl(self) -> None:
        for word, label in [
            ("мәгънә", "N"),
            ("җәмәгать", "N"),
        ]:
            with self.subTest(word=word, label=label):
                dsl = convert_for_annotation_dsl(word, label)
                self.assertNotIn("{{", dsl)

    def test_origin_dependent_lexical_cases_remain_review_items(self) -> None:
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

    def test_month_names_use_ordinary_conversion_and_other_rules(self) -> None:
        cases = [
            ("гыйнвар", "N", "ğıynwar", "ğıynwar"),
            ("июнь", "RL", "iyunʼ", "iyunʼ"),
            ("июль", "RL", "iyulʼ", "iyulʼ"),
            ("сентябрендә", "RL", "sentyabrendä", "sentyabrendä"),
            ("октябрь", "RL", "oktyabrʼ", "oktyabrʼ"),
            ("ноябрь", "N", "noyabr", "noyabr"),
            ("декабрь", "RL", "dekabrʼ", "dekabrʼ"),
        ]

        for word, label, expected_dsl, expected in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, label)
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl), expected)

        self.assertEqual(classify_project("гыйнварга", "N")["key"], "catchall")
        self.assertEqual(classify_project("июнь", "RL")["key"], "catchall")
        self.assertEqual(
            classify_project("октябрена", "RL")["key"],
            "catchall",
        )

    def test_figyl_stem_is_policy_dsl(self) -> None:
        cases = [
            ("фигыль", "{{FIGYL_STEM|antat=fiğıl|pdf=fiğel}}", "fiğıl", "fiğel"),
            ("фигыльләрдә", "{{FIGYL_STEM|antat=fiğıl|pdf=fiğel}}lärdä", "fiğıllärdä", "fiğellärdä"),
        ]

        for word, expected_dsl, antat, pdf in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "N")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl), antat)
                self.assertEqual(resolve_dsl(dsl, {"FIGYL_STEM": "pdf"}), pdf)

    def test_shigyr_stem_is_policy_dsl(self) -> None:
        cases = [
            ("шигырь", "{{SHIGYR_STEM|antat=şiğır|pdf=şiğer}}", "şiğır", "şiğer"),
            (
                "шигырь-шигри",
                "{{SHIGYR_STEM|antat=şiğır|pdf=şiğer}}-şiğri",
                "şiğır-şiğri",
                "şiğer-şiğri",
            ),
        ]

        for word, expected_dsl, antat, pdf in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "N")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl), antat)
                self.assertEqual(resolve_dsl(dsl, {"SHIGYR_STEM": "pdf"}), pdf)

    def test_kagaz_and_mashgul_stems_are_policy_dsl(self) -> None:
        cases = [
            (
                "иҗтимагый",
                "N",
                "{{IJTIMAGIY_STEM|antat=ictimağıy|pdf=ictimaği}}",
                "ictimağıy",
                "ictimaği",
            ),
            (
                "мордва-ерзя",
                "RL",
                "mordva-erz{{YA|ya=ya|ya_front=yä|a=a|ae=ä}}",
                "mordva-erzya",
                "mordva-erzä",
            ),
            ("кәгазъдә", "N", "{{KAGAZ_STEM|antat=käğaz|pdf=qäğäz}}dä", "käğazdä", "qäğäzdä"),
            ("мәшгуль", "N", "{{MASHGUL_STEM|antat=mäşğul|pdf=mäşğül}}", "mäşğul", "mäşğül"),
        ]

        for word, label, expected_dsl, antat, pdf in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, label)
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl), antat)
                self.assertEqual(
                    resolve_dsl(
                        dsl,
                        {
                            "IJTIMAGIY_STEM": "pdf",
                            "YA": "ae",
                            "KAGAZ_STEM": "pdf",
                            "MASHGUL_STEM": "pdf",
                        },
                    ),
                    pdf,
                )

    def test_excluded_disharmony_policy_is_not_registered_as_dsl(self) -> None:
        for word in ["мәшгуль"]:
            with self.subTest(word=word):
                for label in ("N", "RL"):
                    dsl = convert_for_annotation_dsl(word, label)
                    self.assertNotIn("DISHARMONY", dsl)

    def test_russian_sign_before_glide_is_deterministic(self) -> None:
        cases = [
            ("компьютер", "kompʼyuter"),
            ("нью-йорк", "nʼyu-york"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "RL")
                self.assertEqual(dsl, expected)
                self.assertNotIn("RUS_SIGN", dsl)

        self.assertEqual(convert_for_annotation_dsl("барьер", "RL"), "barʼyer")

    def test_russian_soft_sign_is_deterministic(self) -> None:
        cases = [
            ("роль", "rolʼ"),
            ("культура", "kulʼtura"),
            ("секретарь", "sekretarʼ"),
            ("автомобиль", "avtomobilʼ"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "RL")
                self.assertEqual(dsl, expected)
                self.assertNotIn("RUS_SIGN", dsl)

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

    def test_native_u_before_vowel_uses_pdf_plain_spelling(self) -> None:
        cases = [
            ("буа", "bua"),
            ("буар", "buar"),
            ("буын", "buın"),
            ("булуы", "buluı"),
            ("атуы", "atuı"),
            ("куыш", "quış"),
            ("юа", "yua"),
            ("юу", "yuu"),
            ("китүе", "kitüe"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "N")
                self.assertEqual(dsl, expected)
                self.assertNotIn("{{", dsl)

    def test_removed_rule_is_rejected(self) -> None:
        with self.assertRaises(DslError):
            parse_dsl("qor{{HAMZA|omit=|preserve=ʼ}}än")

    def test_cilquar_stem_uses_pdf_plain_spelling(self) -> None:
        cases = [
            ("җилкуар", "cilquar"),
            ("җилкуарлык", "cilquarlıq"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "N")
                self.assertEqual(dsl, expected)
                self.assertNotIn("{{", dsl)

    def test_native_u_before_e_keeps_existing_e_glide_rule(self) -> None:
        self.assertEqual(convert_for_annotation("куелган", "N"), "quyılğan")

    def test_native_ya_u_glide_is_deterministic(self) -> None:
        cases = [
            ("буяу", "buyaw"),
            ("яуган", "yawğan"),
            ("уяу", "uyaw"),
            ("җәяү", "cäyäw"),
            ("төяү", "töyäw"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertNotIn("{{", convert_for_annotation_dsl(word, "N"))


if __name__ == "__main__":
    unittest.main()
