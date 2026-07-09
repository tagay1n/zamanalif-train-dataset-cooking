from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest

from tatar_preannotator.conversion import PDF_COMPACT_POLICY, PREFERRED_POLICY, resolve_dsl
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
    return value.replace("’", "'").casefold()


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
                possible.add(_normalize_gold_zamanalif(resolve_dsl(dsl, PDF_COMPACT_POLICY)))
                possible.add(_normalize_gold_zamanalif(resolve_dsl(dsl, PREFERRED_POLICY)))
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

        self.assertEqual(resolve_dsl(dsl, PREFERRED_POLICY), "akademiyä")

    def test_generated_antat_word_cases_for_manual_review(self) -> None:
        if os.environ.get("RUN_ANTAT_GOLD_COVERAGE") != "1":
            self.skipTest(
                "set RUN_ANTAT_GOLD_COVERAGE=1 to audit unresolved ANTAT conventions"
            )
        self.assert_antat_gold_conversions(ANTAT_GOLD_WORD_CASES)


if __name__ == "__main__":
    unittest.main()
