from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re


END_PUNCT = frozenset(".!?…")
CLOSING_PUNCT = frozenset("\"'»”’)]}")
SPACE_RE = re.compile(r"[ \t\r\f\v]+")
TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"https?://\S+")
CYRILLIC_RE = re.compile(r"[А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]")
TOKEN_BEFORE_DOT_RE = re.compile(r"([A-Za-zА-Яа-яЁёӘәӨөҮүҖҗҢңҺһ.]+)\.$")

ABBREVIATIONS = frozenset(
    {
        "б.",
        "ел.",
        "елл.",
        "һ.б.",
        "һ. б.",
        "т.д.",
        "т. д.",
        "т.п.",
        "т. п.",
        "т.е.",
        "т. е.",
        "им.",
        "ул.",
        "пр.",
        "г.",
        "д.",
        "с.",
        "стр.",
        "рис.",
        "таб.",
        "см.",
        "акад.",
        "проф.",
        "доц.",
        "канд.",
        "д-р.",
    }
)


@dataclass(frozen=True)
class SentenceRecord:
    sentence: str
    start_char: int
    end_char: int


@dataclass(frozen=True)
class SentenceSplitResult:
    sentences: list[SentenceRecord]
    diagnostics: Counter[str]


def split_sentences(
    text: str,
    *,
    min_chars: int = 20,
    max_chars: int = 400,
) -> list[str]:
    return [
        record.sentence
        for record in split_sentence_records(
            text,
            min_chars=min_chars,
            max_chars=max_chars,
        ).sentences
    ]


def split_sentence_records(
    text: str,
    *,
    min_chars: int = 20,
    max_chars: int = 400,
) -> SentenceSplitResult:
    cleaned = _clean_text(text)
    diagnostics: Counter[str] = Counter()
    records: list[SentenceRecord] = []
    for start, end in _candidate_spans(cleaned):
        sentence = cleaned[start:end].strip()
        leading = len(cleaned[start:end]) - len(cleaned[start:end].lstrip())
        trailing = len(cleaned[start:end]) - len(cleaned[start:end].rstrip())
        adjusted_start = start + leading
        adjusted_end = end - trailing
        reason = _rejection_reason(sentence, min_chars=min_chars, max_chars=max_chars)
        if reason:
            diagnostics[f"rejected:{reason}"] += 1
            continue
        records.append(SentenceRecord(sentence, adjusted_start, adjusted_end))
        diagnostics["accepted"] += 1
    return SentenceSplitResult(records, diagnostics)


def _clean_text(text: str) -> str:
    text = TAG_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    text = text.replace("\u00a0", " ")
    lines = [SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _candidate_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\n" and _newline_is_boundary(text, start, index):
            spans.append((start, index))
            start = index + 1
        elif char in END_PUNCT and _punct_is_boundary(text, index):
            end = _consume_closing_punctuation(text, index + 1)
            spans.append((start, end))
            start = _consume_following_space(text, end)
            index = start
            continue
        index += 1
    if start < len(text):
        spans.append((start, len(text)))
    return [(start, end) for start, end in spans if text[start:end].strip()]


def _newline_is_boundary(text: str, start: int, index: int) -> bool:
    candidate = text[start:index].strip()
    if not candidate:
        return False
    if candidate[-1] in END_PUNCT or candidate[-1] in CLOSING_PUNCT:
        return True
    next_char = _next_nonspace(text, index + 1)
    return bool(next_char and next_char.upper() == next_char and CYRILLIC_RE.match(next_char))


def _punct_is_boundary(text: str, index: int) -> bool:
    if _inside_number(text, index):
        return False
    if text[index] == "." and _is_abbreviation(text[: index + 1]):
        return False
    after_closing = _consume_closing_punctuation(text, index + 1)
    next_char = _next_nonspace(text, after_closing)
    if next_char and next_char.lower() == next_char and CYRILLIC_RE.match(next_char):
        return False
    return after_closing >= len(text) or text[after_closing].isspace()


def _consume_closing_punctuation(text: str, index: int) -> int:
    while index < len(text) and text[index] in CLOSING_PUNCT:
        index += 1
    return index


def _consume_following_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _inside_number(text: str, index: int) -> bool:
    return (
        text[index] == "."
        and index > 0
        and index + 1 < len(text)
        and text[index - 1].isdigit()
        and text[index + 1].isdigit()
    )


def _is_abbreviation(prefix: str) -> bool:
    tail = prefix[-24:].lower()
    compact_tail = tail.replace(" ", "")
    if any(tail.endswith(abbr) or compact_tail.endswith(abbr.replace(" ", "")) for abbr in ABBREVIATIONS):
        return True
    match = TOKEN_BEFORE_DOT_RE.search(prefix)
    if not match:
        return False
    token = match.group(1)
    return len(token) == 1 or _looks_like_initial_chain(token)


def _looks_like_initial_chain(token: str) -> bool:
    pieces = [piece for piece in token.split(".") if piece]
    return len(pieces) >= 2 and all(len(piece) == 1 for piece in pieces)


def _next_nonspace(text: str, index: int) -> str | None:
    while index < len(text):
        if not text[index].isspace():
            return text[index]
        index += 1
    return None


def _rejection_reason(sentence: str, *, min_chars: int, max_chars: int) -> str | None:
    if len(sentence) < min_chars:
        return "too_short"
    if len(sentence) > max_chars:
        return "too_long"
    if not _looks_like_tatar_sentence(sentence):
        return "non_cyrillic"
    return None


def _looks_like_tatar_sentence(sentence: str) -> bool:
    letters = [ch for ch in sentence if ch.isalpha()]
    if not letters:
        return False
    cyrillic = sum(bool(CYRILLIC_RE.match(ch)) for ch in letters)
    return cyrillic / len(letters) >= 0.6
