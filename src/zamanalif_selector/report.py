from __future__ import annotations

from collections import Counter
import json
from typing import Iterable, Mapping, Any

from .features import (
    CONDITIONAL_LETTERS,
    count_tatar_specific_letters,
    words_in,
    vowel_harmony_class,
)


def build_report(
    rows: Iterable[Mapping[str, Any]],
    *,
    seed: int,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    selected = [dict(row) for row in rows]
    coverage = _conditional_letter_coverage(selected)
    conditional_counts = Counter(
        int(row.get("conditional_letter_count") or 0) for row in selected
    )
    feature_coverage = Counter()
    conditional_contexts = Counter()
    conditional_words = Counter()
    mixed_harmony_words = Counter()
    mixed_harmony_conditional_words = Counter()
    for row in selected:
        feature_coverage.update(_json_counter(row.get("features_json")))
        conditional_contexts.update(_json_counter(row.get("conditional_contexts_json")))
        conditional_words.update(_json_counter(row.get("conditional_words_json")))
        mixed_harmony_words.update(_json_counter(row.get("mixed_harmony_words_json")))
        mixed_harmony_conditional_words.update(
            _json_counter(row.get("mixed_harmony_conditional_words_json"))
        )

    return {
        "selected_sentence_count": len(selected),
        "seed": seed,
        "config": dict(config),
        "selection_strategy": config.get("selection_strategy", "static_rank"),
        "language_filter": config.get("language_filter", "none"),
        "tatar_specific_letter_filter": _tatar_specific_letter_filter(selected, config),
        "quality_filter": _quality_filter(selected),
        "conditional_letter_coverage": coverage,
        "vowel_harmony_coverage": _vowel_harmony_coverage(selected),
        "mixed_harmony_sentence_count": _mixed_harmony_sentence_count(selected),
        "mixed_harmony_conditional_sentence_count": _mixed_harmony_conditional_sentence_count(
            selected
        ),
        "top_conditional_words_selected": conditional_words.most_common(100),
        "top_conditional_contexts_selected": conditional_contexts.most_common(100),
        "unique_conditional_words_selected": len(conditional_words),
        "unique_conditional_contexts_selected": len(conditional_contexts),
        "conditional_word_saturation": _saturation_summary(conditional_words),
        "conditional_context_saturation": _saturation_summary(conditional_contexts),
        "top_mixed_harmony_words_selected": mixed_harmony_words.most_common(100),
        "top_mixed_harmony_conditional_words_selected": mixed_harmony_conditional_words.most_common(
            100
        ),
        "sentences_with_at_least_one_conditional_letter": sum(
            1 for row in selected if int(row.get("conditional_letter_count") or 0) >= 1
        ),
        "sentences_with_two_or_more_conditional_letters": sum(
            1 for row in selected if int(row.get("conditional_letter_count") or 0) >= 2
        ),
        "sentences_with_three_or_more_conditional_letters": sum(
            1 for row in selected if int(row.get("conditional_letter_count") or 0) >= 3
        ),
        "conditional_letter_count_distribution": {
            str(key): conditional_counts[key] for key in sorted(conditional_counts)
        },
        "source_count": len({row.get("id") for row in selected}),
        "source_repetition_summary": _saturation_summary(
            Counter(str(row.get("id", "")) for row in selected)
        ),
        "top_features": feature_coverage.most_common(100),
    }


def _conditional_letter_coverage(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    per_letter_sentence_counts: Counter[str] = Counter()
    per_letter_word_counts: Counter[str] = Counter()
    per_letter_words: dict[str, Counter[str]] = {
        letter: Counter() for letter in sorted(CONDITIONAL_LETTERS)
    }

    for row in rows:
        sentence_letters = set()
        for word in words_in(str(row.get("sentence", ""))):
            word_letters = {ch for ch in word if ch in CONDITIONAL_LETTERS}
            for letter in word_letters:
                per_letter_words[letter][word] += 1
                per_letter_word_counts[letter] += word.count(letter)
                sentence_letters.add(letter)
        per_letter_sentence_counts.update(sentence_letters)

    result: dict[str, dict[str, Any]] = {}
    for letter in sorted(CONDITIONAL_LETTERS):
        result[letter] = {
            "sentence_count": per_letter_sentence_counts[letter],
            "word_occurrence_count": per_letter_word_counts[letter],
            "unique_words": len(per_letter_words[letter]),
            "top_words": per_letter_words[letter].most_common(50),
        }
    return result


def _vowel_harmony_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sentence_counts: Counter[str] = Counter()
    word_counts: Counter[str] = Counter()
    unique_words: dict[str, set[str]] = {
        "front_only": set(),
        "back_only": set(),
        "mixed_front_back": set(),
        "no_vowels": set(),
    }
    for row in rows:
        classes_in_sentence = set()
        for word in words_in(str(row.get("sentence", ""))):
            harmony = vowel_harmony_class(word)
            word_counts[harmony] += 1
            unique_words[harmony].add(word)
            classes_in_sentence.add(harmony)
        sentence_counts.update(classes_in_sentence)

    return {
        harmony: {
            "sentence_count": sentence_counts[harmony],
            "word_count": word_counts[harmony],
            "unique_words": len(unique_words[harmony]),
        }
        for harmony in ["front_only", "back_only", "mixed_front_back", "no_vowels"]
    }


def _mixed_harmony_sentence_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if any(
            vowel_harmony_class(word) == "mixed_front_back"
            for word in words_in(str(row.get("sentence", "")))
        )
    )


def _mixed_harmony_conditional_sentence_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if any(
            vowel_harmony_class(word) == "mixed_front_back"
            and any(char in CONDITIONAL_LETTERS for char in word)
            for word in words_in(str(row.get("sentence", "")))
        )
    )


def _json_counter(value: object) -> Counter[str]:
    if not value:
        return Counter()
    if isinstance(value, dict):
        return Counter(value)
    return Counter(json.loads(str(value)))


def _value_distribution(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts = Counter(str(row.get(key, "")) for row in rows if row.get(key) not in (None, ""))
    return {value: counts[value] for value in sorted(counts)}


def _saturation_summary(counts: Counter[str]) -> dict[str, Any]:
    if not counts:
        return {
            "unique": 0,
            "max_count": 0,
            "top_10_total": 0,
            "top_10_share": 0.0,
        }
    total = sum(counts.values())
    top_10_total = sum(count for _, count in counts.most_common(10))
    return {
        "unique": len(counts),
        "max_count": counts.most_common(1)[0][1],
        "top_10_total": top_10_total,
        "top_10_share": round(top_10_total / total, 6) if total else 0.0,
    }


def _tatar_specific_letter_filter(
    rows: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    counts = Counter(
        int(row.get("tatar_specific_letter_count") or count_tatar_specific_letters(
            str(row.get("sentence", ""))
        ))
        for row in rows
    )
    return {
        "min_count": int(config.get("min_tatar_specific_letters") or 0),
        "distribution": {str(key): counts[key] for key in sorted(counts)},
    }


def _quality_filter(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reason_counts: Counter[str] = Counter()
    penalty_counts: Counter[str] = Counter()
    for row in rows:
        penalty = float(row.get("quality_penalty") or 0.0)
        penalty_bucket = "0" if penalty <= 0 else str(int(penalty // 25 * 25))
        penalty_counts[penalty_bucket] += 1
        reasons = str(row.get("quality_reasons") or "")
        for reason in reasons.split(","):
            if reason:
                reason_counts[reason] += 1
    return {
        "selected_with_quality_penalty": sum(
            1 for row in rows if float(row.get("quality_penalty") or 0.0) > 0
        ),
        "selected_with_artifact_reasons": sum(1 for row in rows if row.get("quality_reasons")),
        "quality_penalty_distribution": {
            key: penalty_counts[key] for key in sorted(penalty_counts, key=int)
        },
        "artifact_reason_distribution": {
            key: reason_counts[key] for key in sorted(reason_counts)
        },
    }
