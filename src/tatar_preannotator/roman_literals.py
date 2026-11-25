"""A derived Latin view of mixed-script Roman numerals, never a source edit."""
from __future__ import annotations

import re


_CANDIDATE_RE = re.compile(r"(?<!\w)[IVXLCDMІХ]+(?!\w)")
_CENTURY_RANGE_RE = re.compile(
    r"(?<!\w)([IVXLCDMІХ]+)([ \t]*[-–—][ \t]*)([IVXLCDMІХ]+)(?!\w)"
    r"(?=[ \t]+(?:гасыр|йөз))"
)
_CANONICAL_RE = re.compile(
    r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
)
_LATIN_LETTERS = frozenset("IVXLCDM")
_LOOKALIKES = str.maketrans({"І": "I", "Х": "X"})
_ISOLATED_I_RE = re.compile(r"(?<!\w)І(?![\w.])")
_FRACTIONAL_I_RE = re.compile(r"(?<!\w)ІІ1/2(?!\w)")
_REVIEWED_BRIGADE_II_RE = re.compile(r"(?<!\w)ІЇ(?=, Галимов һәм Мөхәметҗанов иптәшләр)")
_REVIEWED_V_RE = re.compile(
    r"(?<!\w)Ѵ(?= (?:муниципальара фәнни-гамәли конференциясе|бүлеге нигезендә вәкаләтле орган))"
)
_REVIEWED_ANNIVERSARY_XXV_RE = re.compile(r"(?<!\w)XXѴ(?= еллыгы көнендә Москва хезмәт ияләре)")


def roman_literal_view(text: str) -> str:
    """Project complete canonical Roman sequences to Latin, retaining offsets.

    Canonical multi-character sequences with Ukrainian І, or Cyrillic Х mixed
    with Latin Roman letters, qualify. Century ranges also allow Cyrillic Х
    at either end. Isolated І markers and the fractional literal ІІ1/2 use
    Latin I. Specific reviewed contexts recover damaged numerals. Every
    change preserves source offsets.
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

    def project_range(match: re.Match[str]) -> str:
        left, separator, right = match.groups()
        start = left.translate(_LOOKALIKES)
        end = right.translate(_LOOKALIKES)
        if _CANONICAL_RE.fullmatch(start) and _CANONICAL_RE.fullmatch(end):
            return start + separator + end
        return match.group()

    view = _CANDIDATE_RE.sub(project, text)
    view = _FRACTIONAL_I_RE.sub("II1/2", view)
    view = _ISOLATED_I_RE.sub("I", view)
    view = _REVIEWED_BRIGADE_II_RE.sub("II", view)
    view = _REVIEWED_V_RE.sub("V", view)
    view = _REVIEWED_ANNIVERSARY_XXV_RE.sub("XXV", view)
    return _CENTURY_RANGE_RE.sub(project_range, view)
