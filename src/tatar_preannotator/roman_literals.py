"""A derived Latin view of mixed-script Roman numerals, never a source edit."""
from __future__ import annotations

import re


_CANDIDATE_RE = re.compile(r"(?<!\w)[IVXLCDMІХ]+(?!\w)")
_CANONICAL_RE = re.compile(
    r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
)
_LATIN_LETTERS = frozenset("IVXLCDM")
_LOOKALIKES = str.maketrans({"І": "I", "Х": "X"})
_ISOLATED_I_RE = re.compile(r"(?<!\w)І(?![\w.])")
_FRACTIONAL_I_RE = re.compile(r"(?<!\w)ІІ1/2(?!\w)")


def roman_literal_view(text: str) -> str:
    """Project complete canonical Roman sequences to Latin, retaining offsets.

    Canonical multi-character sequences with Ukrainian І, or Cyrillic Х mixed
    with Latin Roman letters, qualify. Isolated І markers and the fractional
    literal ІІ1/2 also use Latin I. Initial-like І. forms, embedded foreign
    letters, pure Cyrillic Х-only numerals and malformed spellings stay intact.
    Every substitution is one code point for one code point.
    """
    def project(match: re.Match[str]) -> str:
        source = match.group()
        if len(source) < 2 or not (
            "І" in source
            or ("Х" in source and any(char in _LATIN_LETTERS for char in source))
        ):
            return source
        latin = source.translate(_LOOKALIKES)
        return latin if _CANONICAL_RE.fullmatch(latin) else source

    view = _CANDIDATE_RE.sub(project, text)
    view = _FRACTIONAL_I_RE.sub("II1/2", view)
    return _ISOLATED_I_RE.sub("I", view)
