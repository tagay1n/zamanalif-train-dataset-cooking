from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Iterable, Mapping


CONDITIONAL_LETTERS = frozenset("уүгквяюец")
DETERMINISTIC_TATAR_LETTERS = frozenset("әөҗңһ")
TATAR_SPECIFIC_LETTERS = frozenset("әөүҗңһ")
FRONT_VOWELS = frozenset("әеөүи")
BACK_VOWELS = frozenset("аоуы")
RUSSIAN_ONLY_LETTERS = frozenset("ёщъь")
MIN_TATAR_SPECIFIC_PER_WORD = 0.12

DEFAULT_WEIGHTS: dict[str, float] = {
    "ambig": 10.0,
    "cond_ctx": 8.0,
    "cond_word": 6.0,
    "vowel_harmony_word": 6.0,
    "cond_letter": 5.0,
    "vowel_harmony": 2.0,
    "char4": 2.0,
    "char3": 1.5,
    "char2": 1.0,
    "word": 0.8,
    "deterministic_letter": 0.5,
    "meta": 0.5,
    "style": 0.3,
}

WORD_RE = re.compile(r"[А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]+")


@dataclass(frozen=True)
class ExtractedFeatures:
    features: Counter[str]
    conditional_letter_count: int
    conditional_letter_presence: frozenset[str]
    conditional_word_counts: Counter[str]
    conditional_context_counts: Counter[str]
    mixed_harmony_word_counts: Counter[str]
    mixed_harmony_conditional_word_counts: Counter[str]


@dataclass(frozen=True)
class SentenceQuality:
    word_count: int
    tatar_specific_letter_count: int
    tatar_specific_per_word: float
    russian_only_letter_count: int
    uppercase_letter_ratio: float
    markdown_marker_count: int
    nonempty_line_count: int
    list_marker_count: int
    glossary_separator_count: int
    comma_count: int
    artifact_reasons: tuple[str, ...]
    penalty: float

    @property
    def is_artifact(self) -> bool:
        return bool(self.artifact_reasons)


def words_in(text: str) -> list[str]:
    return [match.group(0).lower().replace("ё", "е") for match in WORD_RE.finditer(text)]


def word_frequencies(sentences: Iterable[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for sentence in sentences:
        counts.update(words_in(sentence))
    return counts


def extract_features(
    sentence: str,
    word_freq: Mapping[str, int] | None = None,
    *,
    min_word_frequency: int = 2,
    max_word_frequency: int = 10_000,
    metadata: Mapping[str, object] | None = None,
) -> ExtractedFeatures:
    features: Counter[str] = Counter()
    conditional_word_counts: Counter[str] = Counter()
    conditional_context_counts: Counter[str] = Counter()
    mixed_harmony_word_counts: Counter[str] = Counter()
    mixed_harmony_conditional_word_counts: Counter[str] = Counter()
    conditional_presence: set[str] = set()
    conditional_occurrences = 0
    words = words_in(sentence)
    freq = word_freq or {}

    for word in words:
        features[f"word:{word}"] += 1
        for n in (2, 3, 4):
            for gram in _char_ngrams(word, n):
                features[f"char{n}:{gram}"] += 1

        word_has_conditional = any(ch in CONDITIONAL_LETTERS for ch in word)
        harmony_class = vowel_harmony_class(word)
        features[f"vowel_harmony:{harmony_class}"] += 1
        if harmony_class == "mixed_front_back" and _word_frequency_allowed(
            word, freq, min_word_frequency, max_word_frequency
        ):
            features[f"vowel_harmony_word:{word}"] += 1
            mixed_harmony_word_counts[word] += 1
            if word_has_conditional:
                mixed_harmony_conditional_word_counts[word] += 1
                _add_mixed_harmony_ambiguity_features(features, word)

        if word_has_conditional and _word_frequency_allowed(
            word, freq, min_word_frequency, max_word_frequency
        ):
            features[f"cond_word:{word}"] += 1
            conditional_word_counts[word] += 1

        for index, letter in enumerate(word):
            if letter in DETERMINISTIC_TATAR_LETTERS:
                features[f"deterministic_letter:{letter}"] += 1

            if letter not in CONDITIONAL_LETTERS:
                continue

            conditional_occurrences += 1
            conditional_presence.add(letter)
            features[f"cond_letter:{letter}"] += 1
            prev_char = word[index - 1] if index > 0 else "^"
            next_char = word[index + 1] if index + 1 < len(word) else "$"
            ctx = f"{prev_char}*{letter}*{next_char}"
            features[f"cond_ctx:{ctx}"] += 1
            features[f"cond_bigram_left:{prev_char}{letter}"] += 1
            features[f"cond_bigram_right:{letter}{next_char}"] += 1
            conditional_context_counts[ctx] += 1

            position = _position(index, len(word))
            features[f"cond_position:{letter}:{position}"] += 1
            _add_ambiguity_features(features, word, index, letter)

    _add_metadata_features(features, metadata or {})
    _add_style_features(features, sentence, words)

    return ExtractedFeatures(
        features=features,
        conditional_letter_count=conditional_occurrences,
        conditional_letter_presence=frozenset(conditional_presence),
        conditional_word_counts=conditional_word_counts,
        conditional_context_counts=conditional_context_counts,
        mixed_harmony_word_counts=mixed_harmony_word_counts,
        mixed_harmony_conditional_word_counts=mixed_harmony_conditional_word_counts,
    )


def feature_weight(feature: str, weights: Mapping[str, float] | None = None) -> float:
    prefix = feature.split(":", 1)[0]
    return (weights or DEFAULT_WEIGHTS).get(prefix, 0.0)


def weighted_feature_sum(
    features: Mapping[str, int], weights: Mapping[str, float] | None = None
) -> float:
    return sum(feature_weight(name, weights) * count for name, count in features.items())


def vowel_harmony_class(word: str) -> str:
    has_front = any(char in FRONT_VOWELS for char in word)
    has_back = any(char in BACK_VOWELS for char in word)
    if has_front and has_back:
        return "mixed_front_back"
    if has_front:
        return "front_only"
    if has_back:
        return "back_only"
    return "no_vowels"


def has_conditional_letter(text: str) -> bool:
    return any(char in CONDITIONAL_LETTERS for word in words_in(text) for char in word)


def has_mixed_harmony_word(text: str) -> bool:
    return any(vowel_harmony_class(word) == "mixed_front_back" for word in words_in(text))


def count_tatar_specific_letters(text: str) -> int:
    return sum(char in TATAR_SPECIFIC_LETTERS for char in text.lower())


def has_min_tatar_specific_letters(text: str, min_count: int = 2) -> bool:
    return count_tatar_specific_letters(text) >= min_count


def sentence_quality(text: str) -> SentenceQuality:
    words = words_in(text)
    word_count = len(words)
    specific_count = count_tatar_specific_letters(text)
    specific_per_word = specific_count / word_count if word_count else 0.0
    lower = text.lower()
    russian_only_count = sum(char in RUSSIAN_ONLY_LETTERS for char in lower)
    letters = [char for char in text if char.isalpha()]
    uppercase_ratio = (
        sum(char.isupper() for char in letters) / len(letters) if letters else 0.0
    )
    nonempty_line_count = sum(1 for line in text.splitlines() if line.strip())
    markdown_marker_count = (
        text.count("##")
        + text.count("**")
        + text.count("[^")
        + len(re.findall(r"(?m)^\s*>", text))
    )
    list_marker_count = len(re.findall(r"(?m)^\s*[-—]\s+", text))
    glossary_separator_count = text.count("—")
    comma_count = text.count(",")

    reasons: list[str] = []
    if markdown_marker_count:
        reasons.append("markdown")
    if nonempty_line_count >= 4 or list_marker_count >= 3:
        reasons.append("list_block")
    if glossary_separator_count >= 4:
        reasons.append("glossary_block")
    if len(letters) >= 20 and uppercase_ratio >= 0.45:
        reasons.append("uppercase_block")
    if word_count >= 8 and specific_per_word < MIN_TATAR_SPECIFIC_PER_WORD:
        reasons.append("low_tatar_specific_density")
    if comma_count >= 12 and specific_per_word < 0.50:
        reasons.append("comma_heavy_list")
    if russian_only_count >= 8 and specific_per_word < 0.30:
        reasons.append("russian_orthography_heavy")

    penalty = _quality_penalty(
        specific_per_word=specific_per_word,
        russian_only_count=russian_only_count,
        uppercase_ratio=uppercase_ratio,
        markdown_marker_count=markdown_marker_count,
        nonempty_line_count=nonempty_line_count,
        list_marker_count=list_marker_count,
        glossary_separator_count=glossary_separator_count,
        comma_count=comma_count,
    )

    return SentenceQuality(
        word_count=word_count,
        tatar_specific_letter_count=specific_count,
        tatar_specific_per_word=round(specific_per_word, 6),
        russian_only_letter_count=russian_only_count,
        uppercase_letter_ratio=round(uppercase_ratio, 6),
        markdown_marker_count=markdown_marker_count,
        nonempty_line_count=nonempty_line_count,
        list_marker_count=list_marker_count,
        glossary_separator_count=glossary_separator_count,
        comma_count=comma_count,
        artifact_reasons=tuple(reasons),
        penalty=round(penalty, 6),
    )


def _quality_penalty(
    *,
    specific_per_word: float,
    russian_only_count: int,
    uppercase_ratio: float,
    markdown_marker_count: int,
    nonempty_line_count: int,
    list_marker_count: int,
    glossary_separator_count: int,
    comma_count: int,
) -> float:
    penalty = 0.0
    if specific_per_word < 0.35:
        penalty += (0.35 - specific_per_word) * 120.0
    penalty += min(russian_only_count, 12) * 6.0
    penalty += markdown_marker_count * 80.0
    penalty += max(0, nonempty_line_count - 2) * 25.0
    penalty += list_marker_count * 30.0
    penalty += glossary_separator_count * 15.0
    penalty += max(0, comma_count - 8) * 8.0
    if uppercase_ratio > 0.25:
        penalty += (uppercase_ratio - 0.25) * 120.0
    return penalty


def _char_ngrams(word: str, n: int) -> Iterable[str]:
    if len(word) < n:
        return ()
    return (word[i : i + n] for i in range(len(word) - n + 1))


def _word_frequency_allowed(
    word: str, freq: Mapping[str, int], min_frequency: int, max_frequency: int
) -> bool:
    count = freq.get(word)
    if count is None:
        return True
    return min_frequency <= count <= max_frequency


def _position(index: int, word_length: int) -> str:
    if index == 0:
        return "initial"
    if index == word_length - 1:
        return "final"
    return "middle"


def _add_ambiguity_features(features: Counter[str], word: str, index: int, letter: str) -> None:
    if letter == "к":
        _add_k_or_g_vowel_feature(features, word, index, "k")
    elif letter == "г":
        _add_k_or_g_vowel_feature(features, word, index, "g")
    elif letter == "в":
        features["ambig:v_letter"] += 1
    elif letter == "у":
        features["ambig:u_letter"] += 1
    elif letter == "ү":
        features["ambig:ü_letter"] += 1
    elif letter == "е":
        features["ambig:initial_e" if index == 0 else "ambig:internal_e"] += 1
    elif letter == "я":
        features["ambig:initial_ya" if index == 0 else "ambig:internal_ya"] += 1
    elif letter == "ю":
        features["ambig:initial_yu" if index == 0 else "ambig:internal_yu"] += 1
    elif letter == "ц":
        features["ambig:ts_letter"] += 1


def _add_mixed_harmony_ambiguity_features(features: Counter[str], word: str) -> None:
    features["ambig:mixed_harmony_conditional_word"] += 1
    if "к" in word:
        features["ambig:mixed_harmony_k"] += 1
    if "г" in word:
        features["ambig:mixed_harmony_g"] += 1
    if "е" in word:
        features["ambig:mixed_harmony_e"] += 1
    if "я" in word:
        features["ambig:mixed_harmony_ya"] += 1
    if "ю" in word:
        features["ambig:mixed_harmony_yu"] += 1
    if "у" in word:
        features["ambig:mixed_harmony_u"] += 1
    if "ү" in word:
        features["ambig:mixed_harmony_ü"] += 1
    if "в" in word:
        features["ambig:mixed_harmony_v"] += 1
    if "ц" in word:
        features["ambig:mixed_harmony_ts"] += 1


def _add_k_or_g_vowel_feature(features: Counter[str], word: str, index: int, latin_name: str) -> None:
    vowel = _next_vowel(word, index + 1)
    if vowel in FRONT_VOWELS:
        features[f"ambig:{latin_name}_before_front_vowel"] += 1
    elif vowel in BACK_VOWELS:
        features[f"ambig:{latin_name}_before_back_vowel"] += 1


def _next_vowel(word: str, start: int) -> str | None:
    for char in word[start:]:
        if char in FRONT_VOWELS or char in BACK_VOWELS:
            return char
    return None


def _add_metadata_features(features: Counter[str], metadata: Mapping[str, object]) -> None:
    genre = metadata.get("genre")
    if genre:
        features[f"meta:genre:{_clean_meta_value(genre)}"] += 1
    year = metadata.get("publish_year")
    if year:
        features[f"meta:year:{year}"] += 1


def _add_style_features(features: Counter[str], sentence: str, words: list[str]) -> None:
    word_count = len(words)
    if word_count <= 8:
        features["style:length:short"] += 1
    elif word_count <= 20:
        features["style:length:medium"] += 1
    else:
        features["style:length:long"] += 1
    if any(ch.isdigit() for ch in sentence):
        features["style:has_digit"] += 1
    if any(ch in "\"'«»“”„" for ch in sentence):
        features["style:has_quote"] += 1
    if any(ch in ",;:()" for ch in sentence):
        features["style:has_inner_punct"] += 1


def _clean_meta_value(value: object) -> str:
    return re.sub(r"\s+", "_", str(value).strip().lower())
