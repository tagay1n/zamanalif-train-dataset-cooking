from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .conversion import DslError, parse_dsl
from .word_export import (
    TASK_SCHEMA_VERSION,
    ConversionBranches,
    conversion_branches,
    ensure_review_state_schema,
    normalize_word,
)


PROJECT_KEY = "contextual_homonym"
PROJECT_TITLE = "Contextual homonyms"
CONCRETE_ORIGINS = frozenset({"N", "RL", "U"})
CONTEXTUAL_NATIVE_FALLBACKS = {
    "г": "ğ",
    "к": "q",
}


class ContextualReviewError(ValueError):
    """Raised when contextual review data is inconsistent."""


@dataclass(frozen=True, order=True)
class OccurrenceKey:
    sample_id: str
    token_index: int


@dataclass(frozen=True)
class ContextualReview:
    sample_id: str
    token_index: int
    normalized_word: str
    zamanalif_dsl: str
    origin: str


@dataclass(frozen=True)
class ContextualExportResult:
    tasks: list[dict[str, Any]]
    occurrences: list[OccurrenceKey]
    report: dict[str, Any]


@dataclass(frozen=True)
class _Occurrence:
    key: OccurrenceKey
    sentence: str
    tokens: list[dict[str, Any]]
    token_text: str
    normalized_word: str
    label: str
    flagged: bool


def export_contextual_tasks_from_db(
    db_path: str | Path,
    *,
    max_items: int | None = None,
) -> ContextualExportResult:
    """Export one sentence-context task for each deferred homonym occurrence."""
    database = Path(db_path)
    if not database.exists():
        raise ContextualReviewError(f"database file does not exist: {database}")
    with closing(sqlite3.connect(database)) as conn, conn:
        from .conflict_resolver import ensure_word_resolution_schema

        ensure_review_state_schema(conn)
        ensure_contextual_review_schema(conn)
        ensure_word_resolution_schema(conn)
        resolutions = dict(
            conn.execute(
                "select normalized_word, decision from word_resolutions"
            ).fetchall()
        )
        reviewed_words = {
            str(row[0])
            for row in conn.execute(
                "select normalized_word from reviewed_words"
            ).fetchall()
        }
        raw_homonym_words: set[str] = set()
        for sample_id, _, raw_tokens in _annotation_rows(conn):
            tokens = _load_tokens(str(sample_id), raw_tokens)
            for token in tokens:
                if not isinstance(token, dict) or token.get("homonym") is not True:
                    continue
                text = token.get("text")
                normalized = normalize_word(text) if isinstance(text, str) else ""
                if normalized:
                    raw_homonym_words.add(normalized)

        explicit = {
            word for word, decision in resolutions.items() if decision == PROJECT_KEY
        }
        effective_words = explicit | {
            word
            for word in raw_homonym_words
            if resolutions.get(word) not in CONCRETE_ORIGINS
        }
        conflicting_reviews = sorted(effective_words & reviewed_words)
        if conflicting_reviews:
            raise ContextualReviewError(
                "contextual homonyms must not exist in reviewed_words: "
                + ", ".join(conflicting_reviews[:20])
            )

        all_occurrences: list[_Occurrence] = []
        for sample_id, sentence, raw_tokens in _annotation_rows(conn):
            tokens = _load_tokens(str(sample_id), raw_tokens)
            for token_index, token in enumerate(tokens):
                if not isinstance(token, dict):
                    continue
                text = token.get("text")
                normalized = normalize_word(text) if isinstance(text, str) else ""
                if not normalized or normalized not in effective_words:
                    continue
                label = token.get("label")
                if label not in CONCRETE_ORIGINS:
                    label = "U"
                all_occurrences.append(
                    _Occurrence(
                        key=OccurrenceKey(str(sample_id), token_index),
                        sentence=str(sentence),
                        tokens=tokens,
                        token_text=text,
                        normalized_word=normalized,
                        label=label,
                        flagged=token.get("homonym") is True,
                    )
                )
        completed = {
            OccurrenceKey(str(row[0]), int(row[1]))
            for row in conn.execute(
                "select sample_id, token_index from contextual_reviews"
            ).fetchall()
        }

    review_required = [
        occurrence
        for occurrence in all_occurrences
        if contextual_conversion_branches(occurrence.normalized_word).state
        != "origin_independent"
    ]
    eligible = [
        occurrence
        for occurrence in review_required
        if occurrence.normalized_word in effective_words
        and occurrence.key not in completed
    ]
    ordered = _round_robin_occurrences(eligible)
    if max_items is not None:
        ordered = ordered[:max_items]

    tasks = [_task_for_occurrence(item) for item in ordered]
    return ContextualExportResult(
        tasks=tasks,
        occurrences=[item.key for item in ordered],
        report={
            "project_key": PROJECT_KEY,
            "project_title": PROJECT_TITLE,
            "effective_homonym_word_count": len(effective_words),
            "review_required_homonym_word_count": len(
                {item.normalized_word for item in review_required}
            ),
            "auto_resolved_deterministic_occurrence_count": (
                len(all_occurrences) - len(review_required)
            ),
            "pending_occurrence_count": len(eligible),
            "exported_occurrence_count": len(ordered),
            "completed_occurrence_count": len(completed),
        },
    )


def _annotation_rows(
    conn: sqlite3.Connection,
) -> Iterable[tuple[Any, Any, Any]]:
    return conn.execute(
        """
        select s.id, s.text, p.tokens_json
        from preannotation_state p
        join samples s on s.id = p.sample_id
        where p.status = 'annotated'
          and p.tatar = 1
          and p.tokens_json is not null
        order by s.id
        """
    )


def _load_tokens(sample_id: str, raw_tokens: str) -> list[Any]:
    try:
        tokens = json.loads(raw_tokens)
    except json.JSONDecodeError as exc:
        raise ContextualReviewError(
            f"{sample_id}: invalid tokens_json in database: {exc}"
        ) from exc
    if not isinstance(tokens, list):
        raise ContextualReviewError(f"{sample_id}: tokens_json must contain a list")
    return tokens


def effective_contextual_homonym_words(conn: sqlite3.Connection) -> set[str]:
    """Return homonyms that have not been cleared by a concrete word resolution."""
    resolutions = dict(
        conn.execute(
            "select normalized_word, decision from word_resolutions"
        ).fetchall()
    )
    words = {
        word for word, decision in resolutions.items() if decision == PROJECT_KEY
    }
    rows = conn.execute(
        """
        select sample_id, tokens_json
        from preannotation_state
        where status = 'annotated'
          and tatar = 1
          and tokens_json is not null
        """
    ).fetchall()
    for sample_id, raw_tokens in rows:
        try:
            tokens = json.loads(raw_tokens)
        except json.JSONDecodeError as exc:
            raise ContextualReviewError(
                f"{sample_id}: invalid tokens_json in database: {exc}"
            ) from exc
        if not isinstance(tokens, list):
            raise ContextualReviewError(f"{sample_id}: tokens_json must contain a list")
        for token in tokens:
            if not isinstance(token, dict) or token.get("homonym") is not True:
                continue
            text = token.get("text")
            normalized = normalize_word(text) if isinstance(text, str) else ""
            if normalized and resolutions.get(normalized) not in CONCRETE_ORIGINS:
                words.add(normalized)
    return words


def _round_robin_occurrences(items: list[_Occurrence]) -> list[_Occurrence]:
    grouped: dict[str, list[_Occurrence]] = defaultdict(list)
    for item in items:
        grouped[item.normalized_word].append(item)
    for occurrences in grouped.values():
        occurrences.sort(
            key=lambda item: (
                not item.flagged,
                item.key.sample_id,
                item.key.token_index,
            )
        )
    words = sorted(grouped)
    result: list[_Occurrence] = []
    offset = 0
    while True:
        added = False
        for word in words:
            occurrences = grouped[word]
            if offset < len(occurrences):
                result.append(occurrences[offset])
                added = True
        if not added:
            return result
        offset += 1


def _task_for_occurrence(item: _Occurrence) -> dict[str, Any]:
    _validate_token_alignment(item.key.sample_id, item.sentence, item.tokens)
    branches = contextual_conversion_branches(item.normalized_word)
    suggestion = branches.suggestion(item.label)
    return {
        "data": {
            "cyrl_word": item.token_text,
            "sentence": item.sentence,
            "context_html": _highlighted_context(
                item.sentence,
                item.tokens,
                item.key.token_index,
            ),
            "gemini_origin": item.label,
            "auto_zamanalif": suggestion,
            "native_zamanalif": branches.native_dsl,
            "loanword_zamanalif": branches.loanword_dsl,
            "hints_html": _contextual_hints(branches.native_dsl, branches.loanword_dsl),
        },
        "meta": {
            "schema_version": TASK_SCHEMA_VERSION,
            "project_key": PROJECT_KEY,
            "sample_id": item.key.sample_id,
            "token_index": item.key.token_index,
        },
    }


def contextual_conversion_branches(word: str) -> ConversionBranches:
    """Return homonym variants with explicit native fallbacks for isolated letters."""
    branches = conversion_branches(word)
    fallback = CONTEXTUAL_NATIVE_FALLBACKS.get(word)
    if branches.native_dsl or not branches.loanword_dsl or fallback is None:
        return branches
    return ConversionBranches(
        native_dsl=fallback,
        loanword_dsl=branches.loanword_dsl,
    )


def _validate_token_alignment(
    sample_id: str,
    sentence: str,
    tokens: list[Any],
) -> None:
    cursor = 0
    for token_index, token in enumerate(tokens):
        if not isinstance(token, dict):
            raise ContextualReviewError(
                f"{sample_id}: token {token_index} must be an object"
            )
        text = token.get("text")
        if not isinstance(text, str) or not text:
            raise ContextualReviewError(
                f"{sample_id}: token {token_index} has invalid text"
            )
        found = sentence.find(text, cursor)
        if found < 0:
            raise ContextualReviewError(
                f"{sample_id}: token {token_index} is missing or out of order: {text!r}"
            )
        cursor = found + len(text)


def _highlighted_context(
    sentence: str,
    tokens: list[dict[str, Any]],
    target_index: int,
) -> str:
    cursor = 0
    pieces: list[str] = []
    for token_index, token in enumerate(tokens):
        text = str(token["text"])
        found = sentence.find(text, cursor)
        pieces.append(escape(sentence[cursor:found]))
        rendered = escape(text)
        pieces.append(f"<mark>{rendered}</mark>" if token_index == target_index else rendered)
        cursor = found + len(text)
    pieces.append(escape(sentence[cursor:]))
    return "".join(pieces)


def _contextual_hints(native: str, loanword: str) -> str:
    return (
        "<ul>"
        f"<li><b>N</b>: {escape(native or 'unavailable')}</li>"
        f"<li><b>RL</b>: {escape(loanword or 'unavailable')}</li>"
        "</ul>"
    )


def ensure_contextual_review_schema(conn: sqlite3.Connection) -> None:
    conn.execute("drop table if exists exported_contextual_occurrences")
    conn.execute(
        """
        create table if not exists contextual_reviews (
            sample_id text not null,
            token_index integer not null check(token_index >= 0),
            normalized_word text not null,
            zamanalif_dsl text not null,
            origin text not null check(origin in ('N', 'RL')),
            updated_at text not null,
            primary key(sample_id, token_index)
        )
        """
    )


def load_contextual_reviews(
    db_path: str | Path,
) -> dict[OccurrenceKey, ContextualReview]:
    with closing(sqlite3.connect(db_path)) as conn, conn:
        ensure_contextual_review_schema(conn)
        rows = conn.execute(
            """
            select sample_id, token_index, normalized_word, zamanalif_dsl, origin
            from contextual_reviews
            order by sample_id, token_index
            """
        ).fetchall()
    return {
        OccurrenceKey(str(row[0]), int(row[1])): ContextualReview(
            sample_id=str(row[0]),
            token_index=int(row[1]),
            normalized_word=str(row[2]),
            zamanalif_dsl=str(row[3]),
            origin=str(row[4]),
        )
        for row in rows
    }


def validate_contextual_review(
    normalized_word: str,
    zamanalif_dsl: str,
    origin: str,
) -> None:
    if origin not in {"N", "RL"}:
        raise ContextualReviewError("contextual origin must be N or RL")
    if normalize_word(normalized_word) != normalized_word:
        raise ContextualReviewError("contextual word must be normalized")
    try:
        parse_dsl(zamanalif_dsl)
    except DslError as exc:
        raise ContextualReviewError(f"invalid contextual Zamanalif DSL: {exc}") from exc


def contextual_project_instructions() -> str:
    return """<section>
  <h2>Contextual homonyms</h2>
  <p>Review the highlighted word using its complete sentence context.</p>
  <ol>
    <li>Compare the native and loanword Zamanalif variants.</li>
    <li>Choose N for the Tatar/native meaning or RL for the Russian/loanword meaning.</li>
    <li>Enter a correction only when the selected variant itself is wrong.</li>
    <li>Skip the task when the meaning cannot be determined reliably.</li>
  </ol>
</section>
"""
