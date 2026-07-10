from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from pathlib import Path
import re
import sqlite3
from typing import Iterable

from tatar_preannotator.conversion import Choice, Literal, parse_dsl
from tatar_preannotator.word_export import (
    contains_rl_review_letter,
    convert_for_annotation,
    convert_for_annotation_dsl,
    vowel_harmony_class,
)


CYRILLIC_TOKEN_RE = re.compile(
    r"[А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]+(?:[-'’][А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]+)*"
)
ZAMANALIF_TOKEN_RE = re.compile(
    r"[A-Za-zÄÖÜÑĞŞÇİıäöüñğşçƏə]+(?:[-'’][A-Za-zÄÖÜÑĞŞÇİıäöüñğşçƏə]+)*"
)
POS_RE = re.compile(
    r"\b(?:n|v|a|adv|pref|pron|pl|gr|reflex|past|prep|cj|conj|int|num)\.?\b",
    flags=re.IGNORECASE,
)
LATIN_PREFIX_RE = re.compile(r"^[A-Za-zÄÖÜÑĞŞÇİıäöüñğşçƏə,.'’\-\s]+")
ENGLISH_NOISE = {
    "against",
    "as",
    "from",
    "into",
    "of",
    "one",
    "out",
    "over",
    "smth",
    "somebody",
    "something",
    "the",
    "to",
    "with",
}


@dataclass(frozen=True)
class AntatWordPair:
    cyrillic_word: str
    expected_zamanalif: str
    label: str
    headword: str
    align_id: int


@dataclass(frozen=True)
class AntatMismatch:
    pair: AntatWordPair
    actual_zamanalif: str


@dataclass(frozen=True)
class AntatRuleGap:
    pair: AntatWordPair
    native_zamanalif: str
    loanword_zamanalif: str


@dataclass(frozen=True)
class AntatCoverage:
    matched_native: list[AntatWordPair]
    matched_loanword: list[AntatWordPair]
    matched_both: list[AntatWordPair]
    rule_gaps: list[AntatRuleGap]

    def summary(self) -> dict[str, int]:
        """Return compact coverage counts for reports and assertion messages."""
        return {
            "matched_native": len(self.matched_native),
            "matched_loanword": len(self.matched_loanword),
            "matched_both": len(self.matched_both),
            "rule_gaps": len(self.rule_gaps),
            "total": (
                len(self.matched_native)
                + len(self.matched_loanword)
                + len(self.matched_both)
                + len(self.rule_gaps)
            ),
        }


def extract_antat_word_pairs(db_path: str | Path) -> list[AntatWordPair]:
    """Extract strict comparable word pairs from aligned Antat dictionary entries."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Antat SQLite database does not exist: {path}")
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select a.align_id, a.headword, c.html as cyrillic_html, z.html as zamanalif_html
            from antat_aligned_entries a
            join antat_entry_pages c
              on c.source_id = 29 and c.entry_id = a.cyrillic_entry_id
            join antat_entry_pages z
              on z.source_id = 30 and z.entry_id = a.zamanalif_entry_id
            where a.status = 'aligned'
            order by a.align_id
            """
        ).fetchall()
    finally:
        conn.close()

    pairs: dict[tuple[str, str, str], AntatWordPair] = {}
    for row in rows:
        headword = str(row["headword"])
        if headword.startswith("-"):
            continue
        cyrillic_tokens = _cyrillic_translation_tokens(str(row["cyrillic_html"]))
        zamanalif_tokens = _zamanalif_translation_tokens(str(row["zamanalif_html"]))
        if not cyrillic_tokens or len(cyrillic_tokens) != len(zamanalif_tokens):
            continue
        for cyrillic_word, expected_zamanalif in zip(cyrillic_tokens, zamanalif_tokens):
            if expected_zamanalif in ENGLISH_NOISE:
                continue
            label = infer_antat_label(cyrillic_word)
            key = (cyrillic_word, expected_zamanalif, label)
            pairs.setdefault(
                key,
                AntatWordPair(
                    cyrillic_word=cyrillic_word,
                    expected_zamanalif=expected_zamanalif,
                    label=label,
                    headword=headword,
                    align_id=int(row["align_id"]),
                ),
            )
    return list(pairs.values())


def antat_converter_mismatches(pairs: Iterable[AntatWordPair]) -> list[AntatMismatch]:
    """Return pairs where the local converter differs from Antat Zamanalif."""
    mismatches: list[AntatMismatch] = []
    for pair in pairs:
        actual = convert_for_annotation(pair.cyrillic_word, pair.label)
        if actual.casefold() != pair.expected_zamanalif.casefold():
            mismatches.append(AntatMismatch(pair=pair, actual_zamanalif=actual))
    return mismatches


def antat_rule_coverage(pairs: Iterable[AntatWordPair]) -> AntatCoverage:
    """Check whether Antat pairs are covered by native or loanword converter rules."""
    matched_native: list[AntatWordPair] = []
    matched_loanword: list[AntatWordPair] = []
    matched_both: list[AntatWordPair] = []
    rule_gaps: list[AntatRuleGap] = []
    for pair in pairs:
        native = convert_for_annotation_dsl(pair.cyrillic_word, "N")
        loanword = convert_for_annotation_dsl(pair.cyrillic_word, "RL")
        expected = _normalize_zamanalif(pair.expected_zamanalif)
        native_matches = expected in _dsl_resolutions(native)
        loanword_matches = expected in _dsl_resolutions(loanword)
        if native_matches and loanword_matches:
            matched_both.append(pair)
        elif native_matches:
            matched_native.append(pair)
        elif loanword_matches:
            matched_loanword.append(pair)
        else:
            rule_gaps.append(
                AntatRuleGap(
                    pair=pair,
                    native_zamanalif=native,
                    loanword_zamanalif=loanword,
                )
            )
    return AntatCoverage(
        matched_native=matched_native,
        matched_loanword=matched_loanword,
        matched_both=matched_both,
        rule_gaps=rule_gaps,
    )


def _normalize_zamanalif(value: str) -> str:
    return value.replace("’", "'").casefold()


def _dsl_resolutions(value: str) -> set[str]:
    if not value:
        return set()

    outputs = [""]
    for segment in parse_dsl(value).segments:
        if isinstance(segment, Literal):
            outputs = [output + segment.text for output in outputs]
        elif isinstance(segment, Choice):
            outputs = [
                output + option_text
                for output in outputs
                for _, option_text in segment.options
            ]
    return {_normalize_zamanalif(output) for output in outputs}


def format_mismatches(mismatches: list[AntatMismatch], *, limit: int = 50) -> str:
    """Format mismatches so unittest failures are reviewable."""
    shown = mismatches[:limit]
    lines = [
        f"{item.pair.cyrillic_word!r} -> expected {item.pair.expected_zamanalif!r}, "
        f"got {item.actual_zamanalif!r}, label={item.pair.label}, "
        f"headword={item.pair.headword!r}, align_id={item.pair.align_id}"
        for item in shown
    ]
    if len(mismatches) > limit:
        lines.append(f"... {len(mismatches) - limit} more mismatches")
    return "\n".join(lines)


def format_rule_gaps(rule_gaps: list[AntatRuleGap], *, limit: int = 50) -> str:
    """Format uncovered Antat pairs so unittest failures point at missing rules."""
    shown = rule_gaps[:limit]
    lines = [
        f"{item.pair.cyrillic_word!r} -> expected {item.pair.expected_zamanalif!r}, "
        f"N got {item.native_zamanalif!r}, RL got {item.loanword_zamanalif!r}, "
        f"letters={_review_letters(item.pair.cyrillic_word)!r}, "
        f"headword={item.pair.headword!r}, align_id={item.pair.align_id}"
        for item in shown
    ]
    if len(rule_gaps) > limit:
        lines.append(f"... {len(rule_gaps) - limit} more rule gaps")
    return "\n".join(lines)


def infer_antat_label(cyrillic_word: str) -> str:
    """Infer the converter branch for Antat checks without Gemini labels."""
    if any(char in cyrillic_word for char in "ёьъщ"):
        return "RL"
    if contains_rl_review_letter(cyrillic_word) and vowel_harmony_class(cyrillic_word) == "mixed_front_back":
        return "RL"
    return "N"


def _cyrillic_translation_tokens(html: str) -> list[str]:
    body = _translation_body_text(html)
    return [
        token.lower()
        for token in CYRILLIC_TOKEN_RE.findall(body)
        if len(token) > 1 and _has_cyrillic_letter(token)
    ]


def _zamanalif_translation_tokens(html: str) -> list[str]:
    body = _translation_body_text(html)
    return [
        token.casefold()
        for token in ZAMANALIF_TOKEN_RE.findall(body)
        if len(token) > 1 and not POS_RE.fullmatch(token)
    ]


def _translation_body_text(html: str) -> str:
    text = _paragraph_text(html)
    text = _strip_headword_pronunciation_and_pos(text)
    return re.sub(r"\s+", " ", text).strip()


def _paragraph_text(html: str) -> str:
    paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", html, flags=re.IGNORECASE | re.DOTALL)
    text = " ".join(paragraphs)
    text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"\[[^\]]*]", " ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_headword_pronunciation_and_pos(text: str) -> str:
    pos_match = POS_RE.search(text)
    if pos_match and pos_match.start() < 80:
        return text[pos_match.end() :]
    return LATIN_PREFIX_RE.sub(" ", text)


def _has_cyrillic_letter(value: str) -> bool:
    return any(CYRILLIC_TOKEN_RE.fullmatch(char) for char in value)


def _review_letters(value: str) -> str:
    return "".join(sorted({char for char in value if char in "уүгквяюецёыьъщ"}))
