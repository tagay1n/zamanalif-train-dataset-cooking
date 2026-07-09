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


if __name__ == "__main__":
    unittest.main()
