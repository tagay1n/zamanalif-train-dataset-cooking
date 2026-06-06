from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import heapq
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Iterable, Mapping, Any

from .features import (
    ExtractedFeatures,
    TATAR_SPECIFIC_LETTERS,
    count_tatar_specific_letters,
    extract_features,
    feature_weight,
    has_min_tatar_specific_letters,
    sentence_quality,
    weighted_feature_sum,
    words_in,
)
from .io import read_jsonl


@dataclass(frozen=True)
class ScoredCandidate:
    row: dict[str, Any]
    extracted: ExtractedFeatures
    base_score: float
    diversity_score: float
    quality_penalty: float
    sort_key: str

    @property
    def score(self) -> float:
        return self.base_score + self.diversity_score - self.quality_penalty


@dataclass(frozen=True)
class StreamingSelectionResult:
    selected: list[dict[str, Any]]
    total_candidates: int
    shortlist_size: int


def score_candidates(
    rows: Iterable[Mapping[str, Any]],
    *,
    min_word_frequency: int = 2,
    max_word_frequency: int = 10_000,
    seed: int = 13,
    progress: Any | None = None,
) -> list[ScoredCandidate]:
    materialized = [dict(row) for row in rows]
    total = len(materialized)
    frequencies: Counter[str] = Counter()
    frequency_task = _add_progress_task(progress, "select frequencies", total)
    for row in materialized:
        frequencies.update(words_in(row["sentence"]))
        _advance_progress(progress, frequency_task)

    extracted: list[ExtractedFeatures] = []
    extraction_task = _add_progress_task(progress, "select features", total)
    for row in materialized:
        extracted.append(
            extract_features(
                row["sentence"],
                frequencies,
                min_word_frequency=min_word_frequency,
                max_word_frequency=max_word_frequency,
                metadata=row,
            )
        )
        _advance_progress(progress, extraction_task)

    feature_doc_counts = _feature_document_counts(
        (item.features for item in extracted),
        progress=progress,
        total=total,
    )
    scored: list[ScoredCandidate] = []
    scoring_task = _add_progress_task(progress, "select scoring", total)
    for row, item in zip(materialized, extracted):
        base_score = weighted_feature_sum(item.features)
        diversity_score = _diversity_score(item.features, feature_doc_counts, total)
        quality = sentence_quality(str(row["sentence"]))
        scored.append(
            ScoredCandidate(
                row=row,
                extracted=item,
                base_score=base_score,
                diversity_score=diversity_score,
                quality_penalty=quality.penalty,
                sort_key=_stable_sort_key(row, seed),
            )
        )
        _advance_progress(progress, scoring_task)
    return scored


def select_candidates(
    candidates: Iterable[ScoredCandidate],
    *,
    target_size: int = 10_000,
    conditional_target_ratio: float = 0.85,
    multi_conditional_target_ratio: float = 0.50,
) -> list[dict[str, Any]]:
    ranked = sorted(candidates, key=lambda item: (-item.score, item.sort_key))
    selected: list[ScoredCandidate] = []
    selected_keys: set[str] = set()

    def add_from(pool: Iterable[ScoredCandidate], limit: int) -> None:
        for item in pool:
            if len(selected) >= limit:
                return
            key = item.sort_key
            if key in selected_keys:
                continue
            selected.append(item)
            selected_keys.add(key)

    multi_target = min(target_size, round(target_size * multi_conditional_target_ratio))
    conditional_target = min(target_size, round(target_size * conditional_target_ratio))
    add_from((item for item in ranked if item.extracted.conditional_letter_count >= 2), multi_target)
    add_from((item for item in ranked if item.extracted.conditional_letter_count >= 1), conditional_target)
    add_from(ranked, target_size)

    output: list[dict[str, Any]] = []
    for rank, item in enumerate(selected[:target_size], start=1):
        output.append(_selected_row(item, rank))
    return output


def select_candidates_adaptive(
    candidates: Iterable[ScoredCandidate],
    *,
    target_size: int = 10_000,
    conditional_target_ratio: float = 0.85,
    multi_conditional_target_ratio: float = 0.50,
    source_penalty: float = 0.15,
) -> list[dict[str, Any]]:
    materialized = list(candidates)
    return _adaptive_select_from_shortlist(
        materialized,
        total_candidates=len(materialized),
        feature_doc_counts=_feature_document_counts(item.extracted.features for item in materialized),
        target_size=target_size,
        conditional_target_ratio=conditional_target_ratio,
        multi_conditional_target_ratio=multi_conditional_target_ratio,
        source_penalty=source_penalty,
    )


def select_candidates_streaming(
    candidates_path: str | Path,
    *,
    target_size: int = 10_000,
    seed: int = 13,
    min_word_frequency: int = 2,
    max_word_frequency: int = 10_000,
    conditional_target_ratio: float = 0.85,
    multi_conditional_target_ratio: float = 0.50,
    shortlist_size: int = 250_000,
    source_penalty: float = 0.15,
    min_tatar_specific_letters: int = 2,
    progress: Any | None = None,
) -> StreamingSelectionResult:
    candidates_path = Path(candidates_path)
    total, frequencies = _stream_word_frequencies(
        candidates_path,
        min_tatar_specific_letters=min_tatar_specific_letters,
        progress=progress,
    )
    feature_doc_counts = _stream_feature_doc_counts(
        candidates_path,
        frequencies,
        total=total,
        min_word_frequency=min_word_frequency,
        max_word_frequency=max_word_frequency,
        min_tatar_specific_letters=min_tatar_specific_letters,
        progress=progress,
    )
    shortlist = _stream_shortlist(
        candidates_path,
        frequencies,
        feature_doc_counts,
        total=total,
        seed=seed,
        min_word_frequency=min_word_frequency,
        max_word_frequency=max_word_frequency,
        target_size=target_size,
        shortlist_size=shortlist_size,
        min_tatar_specific_letters=min_tatar_specific_letters,
        progress=progress,
    )
    selected = _adaptive_select_from_shortlist(
        shortlist,
        total_candidates=total,
        feature_doc_counts=feature_doc_counts,
        target_size=target_size,
        conditional_target_ratio=conditional_target_ratio,
        multi_conditional_target_ratio=multi_conditional_target_ratio,
        source_penalty=source_penalty,
        progress=progress,
    )
    return StreamingSelectionResult(
        selected=selected,
        total_candidates=total,
        shortlist_size=len(shortlist),
    )


def _selected_row(item: ScoredCandidate, rank: int) -> dict[str, Any]:
    row = dict(item.row)
    row.pop("genre", None)
    row.pop("publish_year", None)
    row.pop("tatar_score", None)
    row.pop("russian_score", None)
    row.pop("language_reason", None)
    sentence = str(row.get("sentence", ""))
    tatar_specific_letters = "".join(
        sorted({char for char in sentence.lower() if char in TATAR_SPECIFIC_LETTERS})
    )
    row.update(
        {
            "selected_rank": rank,
            "score": round(item.score, 6),
            "base_score": round(item.base_score, 6),
            "diversity_score": round(item.diversity_score, 6),
            "quality_penalty": round(item.quality_penalty, 6),
            "quality_reasons": ",".join(sentence_quality(sentence).artifact_reasons),
            "conditional_letter_count": item.extracted.conditional_letter_count,
            "conditional_letters": "".join(sorted(item.extracted.conditional_letter_presence)),
            "tatar_specific_letter_count": count_tatar_specific_letters(sentence),
            "tatar_specific_letters": tatar_specific_letters,
            "features_json": json.dumps(
                dict(sorted(item.extracted.features.items())),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "conditional_words_json": json.dumps(
                dict(sorted(item.extracted.conditional_word_counts.items())),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "conditional_contexts_json": json.dumps(
                dict(sorted(item.extracted.conditional_context_counts.items())),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "mixed_harmony_words_json": json.dumps(
                dict(sorted(item.extracted.mixed_harmony_word_counts.items())),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "mixed_harmony_conditional_words_json": json.dumps(
                dict(sorted(item.extracted.mixed_harmony_conditional_word_counts.items())),
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
    )
    return row


def _stream_word_frequencies(
    candidates_path: Path,
    *,
    min_tatar_specific_letters: int,
    progress: Any | None = None,
) -> tuple[int, Counter[str]]:
    frequencies: Counter[str] = Counter()
    total = 0
    task = _add_progress_task(progress, "select word frequencies", None)
    for row in read_jsonl(candidates_path):
        if not _row_language_allowed(
            row, min_tatar_specific_letters=min_tatar_specific_letters
        ):
            continue
        total += 1
        frequencies.update(words_in(row["sentence"]))
        _advance_progress(progress, task, summary=f"rows={total}")
    return total, frequencies


def _stream_feature_doc_counts(
    candidates_path: Path,
    frequencies: Mapping[str, int],
    *,
    total: int,
    min_word_frequency: int,
    max_word_frequency: int,
    min_tatar_specific_letters: int,
    progress: Any | None = None,
) -> Counter[str]:
    feature_doc_counts: Counter[str] = Counter()
    task = _add_progress_task(progress, "select feature coverage", total)
    for index, row in enumerate(read_jsonl(candidates_path), start=1):
        if not _row_language_allowed(
            row, min_tatar_specific_letters=min_tatar_specific_letters
        ):
            continue
        extracted = extract_features(
            row["sentence"],
            frequencies,
            min_word_frequency=min_word_frequency,
            max_word_frequency=max_word_frequency,
            metadata=row,
        )
        feature_doc_counts.update(extracted.features.keys())
        _advance_progress(progress, task, summary=f"features={len(feature_doc_counts)}")
    return feature_doc_counts


def _stream_shortlist(
    candidates_path: Path,
    frequencies: Mapping[str, int],
    feature_doc_counts: Mapping[str, int],
    *,
    total: int,
    seed: int,
    min_word_frequency: int,
    max_word_frequency: int,
    target_size: int,
    shortlist_size: int,
    min_tatar_specific_letters: int,
    progress: Any | None = None,
) -> list[ScoredCandidate]:
    per_pool_limit = max(target_size * 2, max(1, shortlist_size // 4))
    pools: dict[str, list[tuple[float, str, int, ScoredCandidate]]] = {
        "multi": [],
        "conditional": [],
        "mixed": [],
        "overall": [],
    }
    task = _add_progress_task(progress, "select shortlist", total)
    for sequence, row in enumerate(read_jsonl(candidates_path)):
        if not _row_language_allowed(
            row, min_tatar_specific_letters=min_tatar_specific_letters
        ):
            continue
        extracted = extract_features(
            row["sentence"],
            frequencies,
            min_word_frequency=min_word_frequency,
            max_word_frequency=max_word_frequency,
            metadata=row,
        )
        base_score = weighted_feature_sum(extracted.features)
        diversity_score = _diversity_score(extracted.features, feature_doc_counts, total)
        quality = sentence_quality(str(row["sentence"]))
        item = ScoredCandidate(
            row=dict(row),
            extracted=extracted,
            base_score=base_score,
            diversity_score=diversity_score,
            quality_penalty=quality.penalty,
            sort_key=_stable_sort_key(row, seed),
        )
        if extracted.conditional_letter_count >= 2:
            _push_shortlist(pools["multi"], per_pool_limit, item, sequence)
        if extracted.conditional_letter_count >= 1:
            _push_shortlist(pools["conditional"], per_pool_limit, item, sequence)
        if extracted.mixed_harmony_word_counts:
            _push_shortlist(pools["mixed"], per_pool_limit, item, sequence)
        _push_shortlist(pools["overall"], per_pool_limit, item, sequence)
        shortlist_count = sum(len(pool) for pool in pools.values())
        _advance_progress(progress, task, summary=f"pool={shortlist_count}")

    by_key: dict[str, ScoredCandidate] = {}
    for pool in pools.values():
        for _, _, _, item in pool:
            existing = by_key.get(item.sort_key)
            if existing is None or _candidate_rank_key(item) < _candidate_rank_key(existing):
                by_key[item.sort_key] = item
    ranked = sorted(by_key.values(), key=_candidate_rank_key)
    return ranked[:shortlist_size]


def _push_shortlist(
    heap: list[tuple[float, str, int, ScoredCandidate]],
    limit: int,
    item: ScoredCandidate,
    sequence: int,
) -> None:
    entry = (item.score, item.sort_key, sequence, item)
    if len(heap) < limit:
        heapq.heappush(heap, entry)
        return
    if item.score > heap[0][0] or (item.score == heap[0][0] and item.sort_key < heap[0][1]):
        heapq.heapreplace(heap, entry)


def _adaptive_select_from_shortlist(
    shortlist: list[ScoredCandidate],
    *,
    total_candidates: int,
    feature_doc_counts: Mapping[str, int],
    target_size: int,
    conditional_target_ratio: float,
    multi_conditional_target_ratio: float,
    source_penalty: float,
    progress: Any | None = None,
) -> list[dict[str, Any]]:
    selected: list[tuple[ScoredCandidate, float]] = []
    selected_keys: set[str] = set()
    selected_sentence_keys: set[str] = set()
    selected_feature_counts: Counter[str] = Counter()
    selected_source_counts: Counter[str] = Counter()

    def add_stage(pool: list[ScoredCandidate], limit: int, description: str) -> None:
        if len(selected) >= limit:
            return
        _adaptive_fill(
            pool,
            limit=limit,
            selected=selected,
            selected_keys=selected_keys,
            selected_sentence_keys=selected_sentence_keys,
            selected_feature_counts=selected_feature_counts,
            selected_source_counts=selected_source_counts,
            total_candidates=total_candidates,
            feature_doc_counts=feature_doc_counts,
            source_penalty=source_penalty,
            progress=progress,
            description=description,
        )

    multi_target = min(target_size, round(target_size * multi_conditional_target_ratio))
    conditional_target = min(target_size, round(target_size * conditional_target_ratio))
    add_stage(
        [item for item in shortlist if item.extracted.conditional_letter_count >= 2],
        multi_target,
        "adaptive multi",
    )
    add_stage(
        [item for item in shortlist if item.extracted.conditional_letter_count >= 1],
        conditional_target,
        "adaptive conditional",
    )
    add_stage(shortlist, target_size, "adaptive final")

    output: list[dict[str, Any]] = []
    for rank, (item, gain) in enumerate(selected[:target_size], start=1):
        row = _selected_row(item, rank)
        row["adaptive_gain"] = round(gain, 6)
        output.append(row)
    return output


def _adaptive_fill(
    pool: list[ScoredCandidate],
    *,
    limit: int,
    selected: list[tuple[ScoredCandidate, float]],
    selected_keys: set[str],
    selected_sentence_keys: set[str],
    selected_feature_counts: Counter[str],
    selected_source_counts: Counter[str],
    total_candidates: int,
    feature_doc_counts: Mapping[str, int],
    source_penalty: float,
    progress: Any | None,
    description: str,
) -> None:
    heap: list[tuple[float, str, int, ScoredCandidate]] = []
    sequence = 0
    for item in pool:
        if item.sort_key in selected_keys:
            continue
        sentence_key = _sentence_key(item.row.get("sentence", ""))
        if sentence_key in selected_sentence_keys:
            continue
        gain = _marginal_gain(
            item,
            selected_feature_counts,
            selected_source_counts,
            total_candidates=total_candidates,
            feature_doc_counts=feature_doc_counts,
            source_penalty=source_penalty,
        )
        heapq.heappush(heap, (-gain, item.sort_key, sequence, item))
        sequence += 1

    task = _add_progress_task(progress, description, max(0, limit - len(selected)))
    while heap and len(selected) < limit:
        _, _, _, item = heapq.heappop(heap)
        if item.sort_key in selected_keys:
            continue
        sentence_key = _sentence_key(item.row.get("sentence", ""))
        if sentence_key in selected_sentence_keys:
            continue
        gain = _marginal_gain(
            item,
            selected_feature_counts,
            selected_source_counts,
            total_candidates=total_candidates,
            feature_doc_counts=feature_doc_counts,
            source_penalty=source_penalty,
        )
        next_best = -heap[0][0] if heap else -1.0
        if gain + 1e-9 < next_best:
            heapq.heappush(heap, (-gain, item.sort_key, sequence, item))
            sequence += 1
            continue

        selected.append((item, gain))
        selected_keys.add(item.sort_key)
        selected_sentence_keys.add(sentence_key)
        selected_feature_counts.update(item.extracted.features)
        selected_source_counts[str(item.row.get("id", ""))] += 1
        _advance_progress(progress, task, summary=f"selected={len(selected)}")


def _marginal_gain(
    item: ScoredCandidate,
    selected_feature_counts: Mapping[str, int],
    selected_source_counts: Mapping[str, int],
    *,
    total_candidates: int,
    feature_doc_counts: Mapping[str, int],
    source_penalty: float,
) -> float:
    gain = 0.0
    for name, count in item.extracted.features.items():
        rarity = math.log((total_candidates + 1) / (feature_doc_counts.get(name, 0) + 1)) + 1.0
        decay = math.sqrt(1 + selected_feature_counts.get(name, 0))
        gain += feature_weight(name) * count * rarity / decay
    source_count = selected_source_counts.get(str(item.row.get("id", "")), 0)
    if source_count and source_penalty:
        gain /= 1 + source_penalty * source_count
    return gain


def _feature_document_counts(
    features: Iterable[Counter[str]],
    *,
    progress: Any | None = None,
    total: int | None = None,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    task = _add_progress_task(progress, "select feature coverage", total)
    for feature_counter in features:
        counts.update(feature_counter.keys())
        _advance_progress(progress, task)
    return counts


def _diversity_score(features: Mapping[str, int], doc_counts: Mapping[str, int], total: int) -> float:
    score = 0.0
    for name, count in features.items():
        rarity = math.log((total + 1) / (doc_counts.get(name, 0) + 1)) + 1.0
        score += feature_weight(name) * count * rarity
    return score


def _stable_sort_key(row: Mapping[str, Any], seed: int) -> str:
    source = "|".join(
        [
            str(seed),
            str(row.get("id", "")),
            str(row.get("source_sentence_index", "")),
            str(row.get("sentence", "")),
        ]
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _candidate_rank_key(item: ScoredCandidate) -> tuple[float, str]:
    return (-item.score, item.sort_key)


def _sentence_key(sentence: object) -> str:
    normalized = " ".join(str(sentence).strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _row_language_allowed(row: Mapping[str, Any], *, min_tatar_specific_letters: int) -> bool:
    sentence = str(row.get("sentence", ""))
    if not has_min_tatar_specific_letters(sentence, min_count=min_tatar_specific_letters):
        return False
    return not sentence_quality(sentence).is_artifact


def shuffled_rows(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    copied = list(rows)
    random.Random(seed).shuffle(copied)
    return copied


def _add_progress_task(progress: Any | None, description: str, total: int | None) -> int | None:
    if progress is None:
        return None
    return progress.add_task(description, total=total)


def _advance_progress(progress: Any | None, task_id: int | None, **fields: object) -> None:
    if progress is not None and task_id is not None:
        progress.advance(task_id, **fields)
