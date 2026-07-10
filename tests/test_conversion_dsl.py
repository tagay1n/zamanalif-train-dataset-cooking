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
            "ädäbi{{IYA|compact=ä|explicit=yä}}t",
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

    def test_rejects_noncanonical_registered_options(self) -> None:
        with self.assertRaisesRegex(DslError, "options for IYA must be"):
            parse_dsl("x{{IYA|compact=ä|explicit=ya}}")

    def test_rejects_nested_and_unclosed_choices(self) -> None:
        with self.assertRaisesRegex(DslError, "nested choices"):
            parse_dsl("x{{IYA|compact={{x}}|explicit=yä}}")
        with self.assertRaisesRegex(DslError, "unclosed choice"):
            parse_dsl("x{{IYA|compact=ä|explicit=yä")

    def test_rejects_invalid_zamanalif_characters(self) -> None:
        with self.assertRaisesRegex(DslError, "invalid characters"):
            parse_dsl("сәлам")

    def test_rejects_unknown_policy_rule_and_option(self) -> None:
        result = ConversionResult((Choice("IYA", (("compact", "ä"), ("explicit", "yä"))),))

        with self.assertRaisesRegex(DslError, "unknown policy rules"):
            result.resolve({"OTHER": "one"})
        with self.assertRaisesRegex(DslError, "unknown option"):
            result.resolve({"IYA": "other"})

    def test_russian_sign_glide_rule_resolves_by_policy(self) -> None:
        value = "komp{{RUS_SIGN_GLIDE|omit=|preserve='}}yuter"

        self.assertEqual(resolve_dsl(value), "kompyuter")
        self.assertEqual(resolve_dsl(value, {"RUS_SIGN_GLIDE": "omit"}), "kompyuter")
        self.assertEqual(
            resolve_dsl(value, {"RUS_SIGN_GLIDE": "preserve"}),
            "komp'yuter",
        )

    def test_russian_soft_sign_rule_resolves_by_policy(self) -> None:
        value = "rol{{RUS_SOFT_SIGN|omit=|preserve='}}"

        self.assertEqual(resolve_dsl(value), "rol'")
        self.assertEqual(resolve_dsl(value, {"RUS_SOFT_SIGN": "omit"}), "rol")
        self.assertEqual(resolve_dsl(value, PDF_COMPACT_POLICY), "rol")
        self.assertEqual(resolve_dsl(value, PREFERRED_POLICY), "rol'")

    def test_russian_jotated_softening_rule_resolves_by_policy(self) -> None:
        value = "b{{RUS_JOTATED_SOFTENING|glide=y|apostrophe='}}uro"

        self.assertEqual(resolve_dsl(value), "byuro")
        self.assertEqual(
            resolve_dsl(value, {"RUS_JOTATED_SOFTENING": "glide"}),
            "byuro",
        )
        self.assertEqual(
            resolve_dsl(value, {"RUS_JOTATED_SOFTENING": "apostrophe"}),
            "b'uro",
        )
        self.assertEqual(resolve_dsl(value, PDF_COMPACT_POLICY), "byuro")

    def test_native_uw_rule_resolves_by_policy(self) -> None:
        value = "bu{{NATIVE_UW|plain=|glide=w}}a"

        self.assertEqual(resolve_dsl(value), "buwa")
        self.assertEqual(resolve_dsl(value, {"NATIVE_UW": "plain"}), "bua")
        self.assertEqual(resolve_dsl(value, PDF_COMPACT_POLICY), "bua")
        self.assertEqual(resolve_dsl(value, PREFERRED_POLICY), "buwa")

    def test_ie_glide_rule_resolves_by_policy(self) -> None:
        value = "ti{{IE_GLIDE|plain=e|glide=ye}}ş"

        self.assertEqual(resolve_dsl(value), "tieş")
        self.assertEqual(resolve_dsl(value, {"IE_GLIDE": "plain"}), "tieş")
        self.assertEqual(resolve_dsl(value, {"IE_GLIDE": "glide"}), "tiyeş")
        self.assertEqual(resolve_dsl(value, PDF_COMPACT_POLICY), "tieş")

    def test_loanword_final_ka_rule_resolves_by_policy(self) -> None:
        value = "bulav{{RL_FINAL_KA|suffix=q|stem=k}}a"

        self.assertEqual(resolve_dsl(value), "bulavqa")
        self.assertEqual(resolve_dsl(value, {"RL_FINAL_KA": "suffix"}), "bulavqa")
        self.assertEqual(resolve_dsl(value, {"RL_FINAL_KA": "stem"}), "bulavka")

    def test_arabic_initial_ga_rule_accepts_custom_option_text(self) -> None:
        value = "{{ARABIC_INITIAL_GA|plain=ğayı|front=ğäye}}p"

        self.assertEqual(resolve_dsl(value), "ğayıp")
        self.assertEqual(
            resolve_dsl(value, {"ARABIC_INITIAL_GA": "front"}),
            "ğäyep",
        )

    def test_giy_compact_rule_accepts_custom_option_text(self) -> None:
        value = "{{GIY_COMPACT|plain=ğıybad|compact=ğibäd}}ät"

        self.assertEqual(resolve_dsl(value), "ğıybadät")
        self.assertEqual(
            resolve_dsl(value, {"GIY_COMPACT": "compact"}),
            "ğibädät",
        )

    def test_arabic_final_at_rule_accepts_custom_option_text(self) -> None:
        value = "{{ARABIC_FINAL_AT|plain=qanäğat|front=qanäğät}}lek"

        self.assertEqual(resolve_dsl(value), "qanäğatlek")
        self.assertEqual(
            resolve_dsl(value, {"ARABIC_FINAL_AT": "front"}),
            "qanäğätlek",
        )


if __name__ == "__main__":
    unittest.main()
