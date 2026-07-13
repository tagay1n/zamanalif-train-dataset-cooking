from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

from tatar_preannotator.conversion import (
    Choice,
    Literal,
    normalize_zamanalif_apostrophes,
    parse_dsl,
    resolve_dsl,
)
from tatar_preannotator.word_export import convert_for_annotation_dsl


def _load_antat_gold_cases() -> list[tuple[str, str, str, int]]:
    fixture_path = Path(__file__).with_name("antat_gold_reference_cases.py")
    spec = importlib.util.spec_from_file_location("antat_gold_reference_cases", fixture_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Antat gold fixture: {fixture_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ANTAT_GOLD_WORD_CASES


ANTAT_GOLD_WORD_CASES = _load_antat_gold_cases()


def _normalize_gold_zamanalif(value: str) -> str:
    return normalize_zamanalif_apostrophes(value).casefold()


def _all_supported_resolutions(value: str) -> set[str]:
    outputs = [""]
    for segment in parse_dsl(value).segments:
        if isinstance(segment, Literal):
            outputs = [output + segment.text for output in outputs]
            continue
        if isinstance(segment, Choice):
            outputs = [
                output + option_text
                for output in outputs
                for _, option_text in segment.options
            ]
            continue
        raise AssertionError(f"unknown DSL segment: {segment!r}")
    return {_normalize_gold_zamanalif(output) for output in outputs}


class AntatGoldReferenceTests(unittest.TestCase):
    def assert_antat_gold_conversions(self, cases: list[tuple[str, str, str, int]]) -> None:
        failures: list[str] = []
        for cyrillic, expected, headword, align_id in cases:
            possible: set[str] = set()
            rendered: dict[str, str] = {}
            for origin in ("N", "RL"):
                dsl = convert_for_annotation_dsl(cyrillic, origin)
                if not dsl:
                    continue
                rendered[origin] = dsl
                possible.update(_all_supported_resolutions(dsl))
            if _normalize_gold_zamanalif(expected) not in possible:
                failures.append(
                    f"{cyrillic!r} -> expected {expected!r}, "
                    f"supported DSL={rendered!r}, "
                    f"headword={headword!r}, align_id={align_id}"
                )
        if failures:
            shown = "\n".join(failures[:100])
            remaining = "" if len(failures) <= 100 else f"\n... {len(failures) - 100} more failures"
            self.fail(f"Antat gold mismatches: {len(failures)}\n{shown}{remaining}")

    def test_generated_antat_word_cases_are_available_for_review(self) -> None:
        self.assertGreater(len(ANTAT_GOLD_WORD_CASES), 9000)

    def test_antat_iya_variant_is_covered_by_preferred_policy(self) -> None:
        dsl = convert_for_annotation_dsl("академия", "RL")

        self.assertEqual(resolve_dsl(dsl), "akademiyä")

    def test_antat_audit_checks_every_dsl_option(self) -> None:
        dsl = "atel{{RUS_SIGN_E|glide=y|apostrophe=ʼ|apostrophe_glide=ʼy}}e"

        self.assertEqual(_all_supported_resolutions(dsl), {"atelye", "atelʼe", "atelʼye"})

    def test_generated_antat_word_cases_for_manual_review(self) -> None:
        self.assert_antat_gold_conversions(ANTAT_GOLD_WORD_CASES)


if __name__ == "__main__":
    unittest.main()
