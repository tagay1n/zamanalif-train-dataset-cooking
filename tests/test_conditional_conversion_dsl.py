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

        self.assertEqual(dsl, "ädäbi{{IYA|compact=a|explicit=ya}}t")
        self.assertEqual(resolve_dsl(dsl, PREFERRED_POLICY), "ädäbiyat")
        self.assertEqual(resolve_dsl(dsl, PDF_COMPACT_POLICY), "ädäbiat")

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

    def test_loanword_kts_after_k_is_policy_dsl(self) -> None:
        dsl = convert_for_annotation_dsl("ретроспекция", "RL")

        self.assertEqual(
            dsl,
            "retrospek{{KTS_AFTER_K|s=s|ts=ts}}i{{IYA|compact=ä|explicit=yä}}",
        )
        self.assertEqual(resolve_dsl(dsl), "retrospeksiyä")
        self.assertEqual(
            resolve_dsl(dsl, {"KTS_AFTER_K": "ts", "IYA": "explicit"}),
            "retrospektsiyä",
        )

    def test_kts_after_k_policy_does_not_cover_other_consonant_ts(self) -> None:
        self.assertEqual(convert_for_annotation_dsl("принцип", "RL"), "prinsip")

    def test_loanword_final_ts_before_tatar_suffix_is_policy_dsl(self) -> None:
        dsl = convert_for_annotation_dsl("немецләрне", "RL")

        self.assertEqual(dsl, "neme{{FINAL_TS_SUFFIX|stem_s=s|surface_ts=ts}}lärne")
        self.assertEqual(resolve_dsl(dsl), "nemeslärne")
        self.assertEqual(resolve_dsl(dsl, {"FINAL_TS_SUFFIX": "surface_ts"}), "nemetslärne")

    def test_final_ts_suffix_policy_does_not_cover_internal_root_ts(self) -> None:
        self.assertEqual(convert_for_annotation_dsl("лицей", "RL"), "litsey")

    def test_loanword_ou_is_policy_dsl(self) -> None:
        dsl = convert_for_annotation_dsl("боулинг", "RL")

        self.assertEqual(dsl, "bo{{OU_LOANWORD|plain=u|source_w=w}}ling")
        self.assertEqual(resolve_dsl(dsl), "bouling")
        self.assertEqual(resolve_dsl(dsl, {"OU_LOANWORD": "source_w"}), "bowling")

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
            ("проект", "proyıkt", "pro{{PROJECT_E|plain=e|glide=ye}}kt"),
        ]

        for word, native, loanword in cases:
            with self.subTest(word=word):
                self.assert_origin_dependent(word, native, loanword)

    def test_legacy_lexical_cases_are_not_encoded_as_policy_dsl(self) -> None:
        for word, label in [
            ("мәгънә", "N"),
            ("җәмәгать", "N"),
            ("шигырь", "N"),
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

    def test_month_names_are_policy_dsl(self) -> None:
        cases = [
            ("гыйнвар", "N", "{{MONTH_NAME|ordinary=ğıynwar|pdf=ğinwar}}", "ğıynwar", "ğinwar"),
            ("июнь", "RL", "{{MONTH_NAME|ordinary=iyun|pdf=iyün}}{{RUS_SOFT_SIGN|omit=|preserve=ʼ}}", "iyunʼ", "iyün"),
            ("июль", "RL", "{{MONTH_NAME|ordinary=iyul|pdf=iyül}}{{RUS_SOFT_SIGN|omit=|preserve=ʼ}}", "iyulʼ", "iyül"),
            ("сентябрендә", "RL", "{{MONTH_NAME|ordinary=sentyabr|pdf=sentäbr}}endä", "sentyabrendä", "sentäbrendä"),
            ("октябрь", "RL", "{{MONTH_NAME|ordinary=oktyabr|pdf=oktäbr}}{{RUS_SOFT_SIGN|omit=|preserve=ʼ}}", "oktyabrʼ", "oktäbr"),
            ("ноябрь", "N", "{{MONTH_NAME|ordinary=noyabr|pdf=noyäbr}}", "noyabr", "noyäbr"),
            ("декабрь", "RL", "{{MONTH_NAME|ordinary=dekabr|pdf=dekäbr}}{{RUS_SOFT_SIGN|omit=|preserve=ʼ}}", "dekabrʼ", "dekäbr"),
        ]

        for word, label, expected_dsl, ordinary, pdf in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, label)
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl), ordinary)
                self.assertEqual(
                    resolve_dsl(dsl, {"MONTH_NAME": "pdf", "RUS_SOFT_SIGN": "omit"}),
                    pdf,
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

    def test_russian_sign_before_glide_is_policy_dsl(self) -> None:
        cases = [
            ("компьютер", "komp{{RUS_SIGN_GLIDE|omit=|preserve=ʼ}}yuter", "kompyuter", "kompʼyuter"),
            ("нью-йорк", "n{{RUS_SIGN_GLIDE|omit=|preserve=ʼ}}yu-york", "nyu-york", "nʼyu-york"),
        ]

        for word, expected_dsl, omitted, preserved in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "RL")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl), omitted)
                self.assertEqual(resolve_dsl(dsl, {"RUS_SIGN_GLIDE": "preserve"}), preserved)

        dsl = convert_for_annotation_dsl("барьер", "RL")
        self.assertEqual(dsl, "bar{{RUS_SIGN_E|glide=y|apostrophe=ʼ|apostrophe_glide=ʼy}}er")
        self.assertEqual(resolve_dsl(dsl), "baryer")
        self.assertEqual(resolve_dsl(dsl, {"RUS_SIGN_E": "apostrophe_glide"}), "barʼyer")

    def test_russian_soft_sign_is_policy_dsl(self) -> None:
        cases = [
            ("роль", "rol{{RUS_SOFT_SIGN|omit=|preserve=ʼ}}", "rol", "rolʼ"),
            ("культура", "kul{{RUS_SOFT_SIGN|omit=|preserve=ʼ}}tura", "kultura", "kulʼtura"),
            ("секретарь", "sekretar{{RUS_SOFT_SIGN|omit=|preserve=ʼ}}", "sekretar", "sekretarʼ"),
            ("автомобиль", "avtomobil{{RUS_SOFT_SIGN|omit=|preserve=ʼ}}", "avtomobil", "avtomobilʼ"),
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

    def test_native_u_before_non_e_vowel_is_policy_dsl(self) -> None:
        cases = [
            ("буа", "bu{{NATIVE_UW|plain=|glide=w}}a", "bua", "buwa"),
            ("буар", "bu{{NATIVE_UW|plain=|glide=w}}ar", "buar", "buwar"),
            ("буын", "bu{{NATIVE_UW|plain=|glide=w}}ın", "buın", "buwın"),
            ("булуы", "bulu{{NATIVE_UW|plain=|glide=w}}ı", "buluı", "buluwı"),
            ("атуы", "atu{{NATIVE_UW|plain=|glide=w}}ı", "atuı", "atuwı"),
            ("куыш", "qu{{NATIVE_UW|plain=|glide=w}}ış", "quış", "quwış"),
            ("юа", "yu{{NATIVE_UW|plain=|glide=w}}a", "yua", "yuwa"),
            ("юу", "yu{{NATIVE_UW|plain=|glide=w}}u", "yuu", "yuwu"),
            ("китүе", "kitü{{NATIVE_UW|plain=|glide=w}}e", "kitüe", "kitüwe"),
        ]

        for word, expected_dsl, plain, glide in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "N")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl, {"NATIVE_UW": "plain"}), plain)
                self.assertEqual(resolve_dsl(dsl, {"NATIVE_UW": "glide"}), glide)

    def test_cilquar_stem_reuses_native_uw_policy_dsl(self) -> None:
        cases = [
            (
                "җилкуар",
                "cilqu{{NATIVE_UW|plain=|glide=w}}ar",
                "cilquar",
                "cilquwar",
            ),
            (
                "җилкуарлык",
                "cilqu{{NATIVE_UW|plain=|glide=w}}arlıq",
                "cilquarlıq",
                "cilquwarlıq",
            ),
        ]

        for word, expected_dsl, plain, glide in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "N")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl, {"NATIVE_UW": "plain"}), plain)
                self.assertEqual(resolve_dsl(dsl, {"NATIVE_UW": "glide"}), glide)

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
