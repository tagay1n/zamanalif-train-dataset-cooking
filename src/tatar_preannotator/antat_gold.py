from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

from tatar_preannotator.antat_sanity import AntatWordPair, extract_antat_word_pairs
from tatar_preannotator.conversion import (
    ZAMANALIF_APOSTROPHE,
    normalize_zamanalif_apostrophes,
)


ALLOWED_ZAMANALIF = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "äÄöÖüÜñÑıİğĞşŞçÇ"
    f"-—{ZAMANALIF_APOSTROPHE}"
)


@dataclass(frozen=True)
class AntatGoldBuildResult:
    """Deterministic Antat gold fixture rows and skipped-row counts."""

    cases: list[AntatWordPair]
    skipped_non_zamanalif: int


def build_antat_gold_cases(db_path: str | Path) -> AntatGoldBuildResult:
    """Build deduplicated Antat gold cases from aligned dictionary entries."""
    pairs = extract_antat_word_pairs(db_path)
    seen: set[tuple[str, str]] = set()
    cases: list[AntatWordPair] = []
    skipped_non_zamanalif = 0
    for pair in pairs:
        expected_zamanalif = normalize_zamanalif_apostrophes(pair.expected_zamanalif)
        if not _is_clean_zamanalif(expected_zamanalif):
            skipped_non_zamanalif += 1
            continue
        key = (pair.cyrillic_word, expected_zamanalif.casefold())
        if key in seen:
            continue
        seen.add(key)
        cases.append(
            AntatWordPair(
                cyrillic_word=pair.cyrillic_word,
                expected_zamanalif=expected_zamanalif,
                label=pair.label,
                headword=pair.headword,
                align_id=pair.align_id,
            )
        )
    cases.sort(
        key=lambda pair: (
            pair.cyrillic_word,
            pair.expected_zamanalif.casefold(),
            pair.headword,
            pair.align_id,
        )
    )
    return AntatGoldBuildResult(cases=cases, skipped_non_zamanalif=skipped_non_zamanalif)


def render_antat_gold_fixture(result: AntatGoldBuildResult) -> str:
    """Render Antat gold cases as a stable Python fixture module."""
    lines = [
        "from __future__ import annotations",
        "",
        "# Generated from data/zamanalif.sqlite by tatar_preannotator.antat_gold.",
        "# Each tuple is: (cyrillic_word, expected_zamanalif, headword, align_id).",
        f"# Skipped non-Zamanalif tokens: {result.skipped_non_zamanalif}.",
        "",
        "ANTAT_GOLD_WORD_CASES = [",
    ]
    for pair in result.cases:
        lines.append(
            "    "
            + repr(
                (
                    pair.cyrillic_word,
                    pair.expected_zamanalif,
                    pair.headword,
                    pair.align_id,
                )
            )
            + ","
        )
    lines.extend(["]", ""])
    return "\n".join(lines)


def write_antat_gold_fixture(db_path: str | Path, output_path: str | Path) -> AntatGoldBuildResult:
    """Write a deterministic Antat gold fixture module and return build metadata."""
    result = build_antat_gold_cases(db_path)
    Path(output_path).write_text(render_antat_gold_fixture(result), encoding="utf-8")
    return result


def _is_clean_zamanalif(value: str) -> bool:
    value = normalize_zamanalif_apostrophes(value)
    return bool(value) and all(char in ALLOWED_ZAMANALIF for char in value)


def main(argv: list[str] | None = None) -> int:
    """Generate the committed Antat gold fixture from a downloaded SQLite database."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(
            "usage: python -m tatar_preannotator.antat_gold "
            "data/zamanalif.sqlite tests/antat_gold_reference_cases.py",
            file=sys.stderr,
        )
        return 2
    result = write_antat_gold_fixture(args[0], args[1])
    print(
        "wrote Antat gold fixture: "
        f"cases={len(result.cases)} skipped_non_zamanalif={result.skipped_non_zamanalif} "
        f"output={args[1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
