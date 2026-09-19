from __future__ import annotations

import unittest

from tatar_preannotator.conversion import (
    Choice,
    ConversionResult,
    DslError,
    Literal,
    PDF_COMPACT_POLICY,
    PREFERRED_POLICY,
    TS_RULE,
    parse_dsl,
    resolve_dsl,
)
from tatar_preannotator.word_export import (
    conversion_result_for_annotation,
    convert_for_annotation_dsl,
)


class ConversionDslTests(unittest.TestCase):
    def test_legacy_ts_rule_defaults_to_ts_in_registered_policies(self) -> None:
        self.assertEqual(TS_RULE.default_option, "ts")
        self.assertEqual(PREFERRED_POLICY["TS"], "ts")
        self.assertEqual(PDF_COMPACT_POLICY["TS"], "ts")
        self.assertEqual(resolve_dsl("{{TS|s=s|ts=ts}}"), "ts")

    def test_e_glide_resolves_under_named_policies(self) -> None:
        value = "pro{{E_GLIDE|plain=e|glide=ye}}kt"

        self.assertEqual(resolve_dsl(value, PREFERRED_POLICY), "proyekt")
        self.assertEqual(resolve_dsl(value, PDF_COMPACT_POLICY), "proyekt")
        self.assertEqual(resolve_dsl(value), "proyekt")

    def test_round_trip_multiple_choices(self) -> None:
        value = "ti{{E_GLIDE|plain=e|glide=ye}}ş-mi{{E_GLIDE|plain=e|glide=ye}}"

        parsed = parse_dsl(value)

        self.assertEqual(parsed.to_dsl(), value)
        self.assertEqual(parsed.resolve(PDF_COMPACT_POLICY), "tiyeş-miye")
        self.assertEqual(parsed.resolve(PREFERRED_POLICY), "tiyeş-miye")

    def test_deterministic_result_has_no_dsl(self) -> None:
        result = conversion_result_for_annotation("шәһәр", "N")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.has_choices)
        self.assertEqual(result.to_dsl(), "şähär")

    def test_annotation_converter_emits_deterministic_iya(self) -> None:
        self.assertEqual(convert_for_annotation_dsl("әдәбият", "N"), "ädäbiyat")

    def test_rejects_removed_iya_rule(self) -> None:
        with self.assertRaisesRegex(DslError, "unknown rule id"):
            parse_dsl("orfografi{{IYA|compact=ä|explicit=yä}}")

    def test_rejects_unknown_rule(self) -> None:
        with self.assertRaisesRegex(DslError, "unknown rule id"):
            parse_dsl("x{{UNKNOWN|one=a|two=b}}")

    def test_rejects_duplicate_option(self) -> None:
        with self.assertRaisesRegex(DslError, "duplicate option"):
            parse_dsl("x{{E_GLIDE|plain=e|plain=ye}}")

    def test_rejects_custom_text_for_fixed_rule(self) -> None:
        with self.assertRaisesRegex(DslError, "options for E_GLIDE"):
            parse_dsl("x{{E_GLIDE|plain=a|glide=ya}}")

    def test_rejects_nested_and_unclosed_choices(self) -> None:
        with self.assertRaisesRegex(DslError, "nested choices"):
            parse_dsl("x{{E_GLIDE|plain={{x}}|glide=ye}}")
        with self.assertRaisesRegex(DslError, "unclosed choice"):
            parse_dsl("x{{E_GLIDE|plain=e|glide=ye")

    def test_rejects_invalid_zamanalif_characters(self) -> None:
        with self.assertRaisesRegex(DslError, "invalid characters"):
            parse_dsl("сәлам")

    def test_accepts_parentheses_in_literal(self) -> None:
        self.assertEqual(parse_dsl("vkp(b").to_dsl(), "vkp(b")

    def test_accepts_periods_in_abbreviation_literal(self) -> None:
        self.assertEqual(parse_dsl("s.g.v").to_dsl(), "s.g.v")

    def test_accepts_digits_in_identifier_literal(self) -> None:
        self.assertEqual(parse_dsl("vn-5507-mr").to_dsl(), "vn-5507-mr")

    def test_accepts_preserved_slash_in_literal(self) -> None:
        self.assertEqual(parse_dsl("gkal/säğ").to_dsl(), "gkal/säğ")

    def test_accepts_single_ascii_spaces_between_words(self) -> None:
        value = "neftʼ avtomatları"

        self.assertEqual(parse_dsl(value).to_dsl(), value)
        self.assertEqual(resolve_dsl(value), value)

    def test_rejects_malformed_literal_spacing(self) -> None:
        for value in (" neftʼ", "neftʼ ", "neftʼ  avtomatları"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(DslError, "invalid spacing"):
                    parse_dsl(value)

    def test_rejects_unknown_policy_rule_and_option(self) -> None:
        result = ConversionResult((Choice("E_GLIDE", (("plain", "e"), ("glide", "ye"))),))

        with self.assertRaisesRegex(DslError, "unknown policy rules"):
            result.resolve({"OTHER": "one"})
        with self.assertRaisesRegex(DslError, "unknown option"):
            result.resolve({"E_GLIDE": "other"})

    def test_russian_sign_glide_rule_resolves_by_policy(self) -> None:
        value = "komp{{RUS_SIGN|omit=|preserve=ʼ}}yuter"

        self.assertEqual(resolve_dsl(value), "kompʼyuter")
        self.assertEqual(resolve_dsl(value, {"RUS_SIGN": "omit"}), "kompyuter")
        self.assertEqual(
            resolve_dsl(value, {"RUS_SIGN": "preserve"}),
            "kompʼyuter",
        )

    def test_russian_soft_sign_rule_resolves_by_policy(self) -> None:
        value = "rol{{RUS_SIGN|omit=|preserve=ʼ}}"

        self.assertEqual(resolve_dsl(value), "rolʼ")
        self.assertEqual(resolve_dsl(value, {"RUS_SIGN": "omit"}), "rol")
        self.assertEqual(resolve_dsl(value, PDF_COMPACT_POLICY), "rolʼ")
        self.assertEqual(resolve_dsl(value, PREFERRED_POLICY), "rolʼ")

    def test_russian_jotation_rule_resolves_by_policy(self) -> None:
        value = "b{{RUS_JOTATION|glide=y|apostrophe=ʼ|plain=}}uro"

        self.assertEqual(resolve_dsl(value), "byuro")
        self.assertEqual(
            resolve_dsl(value, {"RUS_JOTATION": "glide"}),
            "byuro",
        )
        self.assertEqual(
            resolve_dsl(value, {"RUS_JOTATION": "apostrophe"}),
            "bʼuro",
        )
        self.assertEqual(resolve_dsl(value, PDF_COMPACT_POLICY), "byuro")

    def test_russian_shch_yo_uses_jotation_plain_option(self) -> None:
        value = "şç{{RUS_JOTATION|glide=y|apostrophe=ʼ|plain=}}otka"

        self.assertEqual(resolve_dsl(value), "şçyotka")
        self.assertEqual(resolve_dsl(value, {"RUS_JOTATION": "apostrophe"}), "şçʼotka")
        self.assertEqual(resolve_dsl(value, {"RUS_JOTATION": "plain"}), "şçotka")

    def test_rejects_removed_native_uw_rule(self) -> None:
        value = "bu{{NATIVE_UW|plain=|glide=w}}a"

        with self.assertRaisesRegex(DslError, "unknown rule id"):
            parse_dsl(value)

    def test_e_glide_rule_resolves_by_policy(self) -> None:
        value = "ti{{E_GLIDE|plain=e|glide=ye}}ş"

        self.assertEqual(resolve_dsl(value), "tiyeş")
        self.assertEqual(resolve_dsl(value, {"E_GLIDE": "plain"}), "tieş")
        self.assertEqual(resolve_dsl(value, {"E_GLIDE": "glide"}), "tiyeş")
        self.assertEqual(resolve_dsl(value, PDF_COMPACT_POLICY), "tiyeş")

    def test_rejects_removed_loanword_final_ka_rule(self) -> None:
        value = "bulav{{RL_FINAL_KA|suffix=q|stem=k}}a"

        with self.assertRaisesRegex(DslError, "unknown rule id"):
            parse_dsl(value)

    def test_mostaqil_rule_accepts_custom_option_text(self) -> None:
        base = "möstä{{MOSTAQIL|pdf=qil|antat=qıyl}}"
        suffixed = "möstä{{MOSTAQIL|pdf=qil|antat=qıylʼ}}lege"

        self.assertEqual(resolve_dsl(base), "möstäqıyl")
        self.assertEqual(resolve_dsl(base, {"MOSTAQIL": "pdf"}), "möstäqil")
        self.assertEqual(resolve_dsl(suffixed), "möstäqıylʼlege")
        self.assertEqual(resolve_dsl(suffixed, {"MOSTAQIL": "pdf"}), "möstäqillege")


if __name__ == "__main__":
    unittest.main()
