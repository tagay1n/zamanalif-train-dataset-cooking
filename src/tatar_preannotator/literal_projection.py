"""Offset-preserving literal conversions shared by export and corpus views."""
from __future__ import annotations

import re

from .roman_literals import roman_literal_view


# A degree sign distinguishes the unit from ordinary С -> S. Keep geographic
# С.Ш./С.ш. (north latitude) outside this rule, even with spaces after the dot.
_CELSIUS_RE = re.compile(r"°\s*С(?!\w|\.\s*[Шш]\b)")


def literal_source_view(text: str) -> str:
    """Return a derived Latin literal view without changing source offsets."""
    view = roman_literal_view(text)
    return _CELSIUS_RE.sub(lambda match: match.group()[:-1] + "C", view)
