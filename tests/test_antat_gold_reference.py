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
DELIBERATELY_EXCLUDED_ANTAT_POLICIES = frozenset(
    {
        ("посылка", 6005),  # Dataset policy maps every written ы to ı.
    }
)
NATIVE_UW_FOLLOWING_VOWELS = frozenset("аәоуөыэеиү")


def _uses_excluded_antat_native_uw_policy(cyrillic: str, expected: str) -> bool:
    """Return whether Antat writes the explicit native glide rejected by policy."""
    if "w" not in expected.casefold():
        return False
    folded = cyrillic.casefold()
    for index, char in enumerate(folded[:-1]):
        next_char = folded[index + 1]
        if char == "у" and next_char in NATIVE_UW_FOLLOWING_VOWELS - {"е"}:
            return True
        if char in {"ү", "ю"} and next_char in NATIVE_UW_FOLLOWING_VOWELS:
            return True
    return False


def _uses_retired_russian_dsl_policy(cyrillic: str, expected: str) -> bool:
    """Return whether Antat uses an alternative now made deterministic."""
    folded = cyrillic.casefold()
    normalized_expected = _normalize_gold_zamanalif(expected)
    if normalized_expected.count("ʼ") < sum(folded.count(sign) for sign in "ьъ"):
        return True
    if "ъе" in folded and "ʼ" in normalized_expected:
        return True
    return any(char in folded for char in "яюё") and "ʼ" in normalized_expected


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
            # The dataset's ц -> ts policy intentionally overrides Antat's
            # positional s spellings; those cases are covered by policy tests.
            if "ц" in cyrillic.casefold():
                continue
            if (
                (cyrillic, align_id) in DELIBERATELY_EXCLUDED_ANTAT_POLICIES
                or _uses_excluded_antat_native_uw_policy(cyrillic, expected)
                or _uses_retired_russian_dsl_policy(cyrillic, expected)
            ):
                continue
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

    def test_antat_iya_variant_is_deterministic(self) -> None:
        dsl = convert_for_annotation_dsl("академия", "RL")

        self.assertEqual(dsl, "akademiyä")
        self.assertNotIn("{{", dsl)

    def test_antat_rejected_hard_sign_e_is_excluded_narrowly(self) -> None:
        self.assertEqual(convert_for_annotation_dsl("объективлык", "RL"), "obyektivlıq")

    def test_generated_antat_word_cases_for_manual_review(self) -> None:
        self.assert_antat_gold_conversions(ANTAT_GOLD_WORD_CASES)


if __name__ == "__main__":
    unittest.main()
