from __future__ import annotations

import unittest

from tatar_preannotator.conversion import (
    Choice,
    ConversionResult,
    DslError,
    Literal,
    PDF_COMPACT_POLICY,
    PREFERRED_POLICY,
    parse_dsl,
    resolve_dsl,
    result_with_iya_choices,
)
from tatar_preannotator.word_export import (
    conversion_result_for_annotation,
    convert_for_annotation_dsl,
)


class ConversionDslTests(unittest.TestCase):
    def test_iya_serialization_marks_only_differing_span(self) -> None:
        result = result_with_iya_choices("орфография", "orfografiä")

        self.assertEqual(
            result.to_dsl(),
            "orfografi{{IYA|compact=ä|explicit=yä}}",
        )
        self.assertEqual(result.rule_ids, ("IYA",))

    def test_iya_resolves_under_named_policies(self) -> None:
        value = "orfografi{{IYA|compact=ä|explicit=yä}}"

        self.assertEqual(resolve_dsl(value, PREFERRED_POLICY), "orfografiyä")
        self.assertEqual(resolve_dsl(value, PDF_COMPACT_POLICY), "orfografiä")
        self.assertEqual(resolve_dsl(value), "orfografiyä")

    def test_round_trip_multiple_choices(self) -> None:
        value = "i{{IYA|compact=ä|explicit=yä}}-i{{IYA|compact=ä|explicit=yä}}"

        parsed = parse_dsl(value)

        self.assertEqual(parsed.to_dsl(), value)
        self.assertEqual(parsed.resolve(PDF_COMPACT_POLICY), "iä-iä")
        self.assertEqual(parsed.resolve(PREFERRED_POLICY), "iyä-iyä")

    def test_deterministic_result_has_no_dsl(self) -> None:
        result = conversion_result_for_annotation("шәһәр", "N")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.has_choices)
        self.assertEqual(result.to_dsl(), "şähär")

    def test_annotation_converter_emits_iya_dsl(self) -> None:
        self.assertEqual(
            convert_for_annotation_dsl("әдәбият", "N"),
            "ädäbi{{IYA|compact=a|explicit=ya}}t",
        )

    def test_uncertain_alignment_does_not_invent_choice(self) -> None:
        result = result_with_iya_choices("мияубикә", "miyawbikä")

        self.assertEqual(result, ConversionResult((Literal("miyawbikä"),)))
        self.assertFalse(result.has_choices)

    def test_rejects_unknown_rule(self) -> None:
        with self.assertRaisesRegex(DslError, "unknown rule id"):
            parse_dsl("x{{UNKNOWN|one=a|two=b}}")

    def test_rejects_duplicate_option(self) -> None:
        with self.assertRaisesRegex(DslError, "duplicate option"):
            parse_dsl("x{{IYA|compact=ä|compact=yä}}")

    def test_accepts_custom_iya_vowel_quality_with_canonical_option_ids(self) -> None:
        value = "x{{IYA|compact=a|explicit=ya}}"

        self.assertEqual(parse_dsl(value).to_dsl(), value)

    def test_rejects_nested_and_unclosed_choices(self) -> None:
        with self.assertRaisesRegex(DslError, "nested choices"):
            parse_dsl("x{{IYA|compact={{x}}|explicit=yä}}")
        with self.assertRaisesRegex(DslError, "unclosed choice"):
            parse_dsl("x{{IYA|compact=ä|explicit=yä")

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

    def test_rejects_unknown_policy_rule_and_option(self) -> None:
        result = ConversionResult((Choice("IYA", (("compact", "ä"), ("explicit", "yä"))),))

        with self.assertRaisesRegex(DslError, "unknown policy rules"):
            result.resolve({"OTHER": "one"})
        with self.assertRaisesRegex(DslError, "unknown option"):
            result.resolve({"IYA": "other"})

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

    def test_native_uw_rule_resolves_by_policy(self) -> None:
        value = "bu{{NATIVE_UW|plain=|glide=w}}a"

        self.assertEqual(resolve_dsl(value), "buwa")
        self.assertEqual(resolve_dsl(value, {"NATIVE_UW": "plain"}), "bua")
        self.assertEqual(resolve_dsl(value, PDF_COMPACT_POLICY), "bua")
        self.assertEqual(resolve_dsl(value, PREFERRED_POLICY), "buwa")

    def test_e_glide_rule_resolves_by_policy(self) -> None:
        value = "ti{{E_GLIDE|plain=e|glide=ye}}ş"

        self.assertEqual(resolve_dsl(value), "tiyeş")
        self.assertEqual(resolve_dsl(value, {"E_GLIDE": "plain"}), "tieş")
        self.assertEqual(resolve_dsl(value, {"E_GLIDE": "glide"}), "tiyeş")
        self.assertEqual(resolve_dsl(value, PDF_COMPACT_POLICY), "tiyeş")

    def test_loanword_final_ka_rule_resolves_by_policy(self) -> None:
        value = "bulav{{RL_FINAL_KA|suffix=q|stem=k}}a"

        self.assertEqual(resolve_dsl(value), "bulavqa")
        self.assertEqual(resolve_dsl(value, {"RL_FINAL_KA": "suffix"}), "bulavqa")
        self.assertEqual(resolve_dsl(value, {"RL_FINAL_KA": "stem"}), "bulavka")

    def test_mostaqil_rule_accepts_custom_option_text(self) -> None:
        base = "möstä{{MOSTAQIL|pdf=qil|antat=qıyl}}"
        suffixed = "möstä{{MOSTAQIL|pdf=qil|antat=qıylʼ}}lege"

        self.assertEqual(resolve_dsl(base), "möstäqıyl")
        self.assertEqual(resolve_dsl(base, {"MOSTAQIL": "pdf"}), "möstäqil")
        self.assertEqual(resolve_dsl(suffixed), "möstäqıylʼlege")
        self.assertEqual(resolve_dsl(suffixed, {"MOSTAQIL": "pdf"}), "möstäqillege")


if __name__ == "__main__":
    unittest.main()
