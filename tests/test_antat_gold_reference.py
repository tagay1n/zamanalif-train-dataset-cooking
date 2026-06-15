from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

from tatar_preannotator.word_export import convert_for_annotation


def _load_antat_gold_cases() -> list[tuple[str, str, str, int]]:
    fixture_path = Path(__file__).with_name("antat_gold_reference_cases.py")
    spec = importlib.util.spec_from_file_location("antat_gold_reference_cases", fixture_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Antat gold fixture: {fixture_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ANTAT_GOLD_WORD_CASES


ANTAT_GOLD_WORD_CASES = _load_antat_gold_cases()


class AntatGoldReferenceTests(unittest.TestCase):
    def assert_antat_gold_conversions(self, cases: list[tuple[str, str, str, int]]) -> None:
        failures: list[str] = []
        for cyrillic, expected, headword, align_id in cases:
            native = convert_for_annotation(cyrillic, "N")
            loanword = convert_for_annotation(cyrillic, "RL")
            if expected.casefold() not in {native.casefold(), loanword.casefold()}:
                failures.append(
                    f"{cyrillic!r} -> expected {expected!r}, "
                    f"N got {native!r}, RL got {loanword!r}, "
                    f"headword={headword!r}, align_id={align_id}"
                )
        if failures:
            shown = "\n".join(failures[:100])
            remaining = "" if len(failures) <= 100 else f"\n... {len(failures) - 100} more failures"
            self.fail(f"Antat gold mismatches: {len(failures)}\n{shown}{remaining}")

    def test_generated_antat_word_cases_for_manual_review(self) -> None:
        self.assertGreater(len(ANTAT_GOLD_WORD_CASES), 9000)
        self.assert_antat_gold_conversions(ANTAT_GOLD_WORD_CASES)


if __name__ == "__main__":
    unittest.main()
