from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .conflict_resolver import ensure_word_resolution_schema
from .conversion import DslError, parse_dsl
from .word_export import ensure_review_state_schema, normalize_word


ORIGIN_CONTROL = "reviewed_origin"
CONVERSION_CONTROL = "corrected_zamanalif"
ALLOWED_ORIGINS = frozenset({"N", "RL", "U"})


class LabelStudioImportError(ValueError):
    """Raised when Label Studio annotations cannot be imported safely."""


@dataclass(frozen=True)
class ReviewedAnnotation:
    """One validated word-level annotation from Label Studio."""

    normalized_word: str
    zamanalif_dsl: str
    origin: str


@dataclass(frozen=True)
class LabelStudioImportSummary:
    """Counts produced by a successful atomic annotation import."""

    total_tasks: int
    completed_tasks: int
    imported_words: int
    unchanged_words: int
    contextual_homonym_words: int
    skipped_unannotated_tasks: int
    contextual_homonym_examples: tuple[str, ...]


@dataclass(frozen=True)
class ParsedLabelStudioExport:
    annotations: tuple[ReviewedAnnotation, ...]
    total_tasks: int
    skipped_unannotated_tasks: int


@dataclass(frozen=True)
class LabelStudioAnnotationChange:
    """One human decision that differs from the exported suggestion."""

    task_id: str
    word: str
    suggested_origin: str
    reviewed_origin: str
    suggested_zamanalif: str
    reviewed_zamanalif: str


@dataclass(frozen=True)
class LabelStudioAuditSummary:
    """Read-only validation and change counts for a Label Studio export."""

    total_tasks: int
    completed_tasks: int
    unchanged_tasks: int
    skipped_unannotated_tasks: int
    origin_changes: int
    conversion_changes: int
    changes: tuple[LabelStudioAnnotationChange, ...]


@dataclass(frozen=True)
class _ParsedTask:
    reviewed: ReviewedAnnotation
    task_id: str
    word: str
    suggested_origin: str
    suggested_zamanalif: str


def import_labelstudio_annotations(
    db_path: str | Path,
    input_path: str | Path,
) -> LabelStudioImportSummary:
    """Validate a Label Studio JSON export and atomically store approved words."""
    database = Path(db_path)
    if not database.exists():
        raise LabelStudioImportError(f"database file does not exist: {database}")
    parsed = parse_labelstudio_export(input_path)
    now = datetime.now(timezone.utc).isoformat()

    imported = 0
    unchanged = 0
    with closing(sqlite3.connect(database)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            ensure_review_state_schema(conn)
            ensure_word_resolution_schema(conn)
            contextual_homonyms = _load_contextual_homonym_words(conn)
            reviewable = [
                item
                for item in parsed.annotations
                if item.normalized_word not in contextual_homonyms
            ]
            deferred_homonyms = sorted(
                {item.normalized_word for item in parsed.annotations}
                & contextual_homonyms
            )
            imported_words = {item.normalized_word for item in reviewable}
            existing = {
                row[0]: (row[1], row[2])
                for row in conn.execute(
                    """
                    select normalized_word, zamanalif_dsl, origin
                    from reviewed_words
                    """
                ).fetchall()
                if row[0] in imported_words
            }
            for word in deferred_homonyms:
                conn.execute(
                    """
                    insert into word_resolutions(normalized_word, decision, updated_at)
                    values (?, 'contextual_homonym', ?)
                    on conflict(normalized_word) do update set
                        decision=excluded.decision,
                        updated_at=excluded.updated_at
                    """,
                    (word, now),
                )
            for item in reviewable:
                previous = existing.get(item.normalized_word)
                current = (item.zamanalif_dsl, item.origin)
                if previous is not None:
                    if previous != current:
                        raise LabelStudioImportError(
                            f"reviewed word conflict for {item.normalized_word!r}: "
                            f"database has {previous!r}, import has {current!r}"
                        )
                    unchanged += 1
                    continue
                conn.execute(
                    """
                    insert into reviewed_words(
                        normalized_word, zamanalif_dsl, origin, updated_at
                    ) values (?, ?, ?, ?)
                    """,
                    (item.normalized_word, item.zamanalif_dsl, item.origin, now),
                )
                imported += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return LabelStudioImportSummary(
        total_tasks=parsed.total_tasks,
        completed_tasks=len(parsed.annotations),
        imported_words=imported,
        unchanged_words=unchanged,
        contextual_homonym_words=len(deferred_homonyms),
        skipped_unannotated_tasks=parsed.skipped_unannotated_tasks,
        contextual_homonym_examples=tuple(deferred_homonyms[:20]),
    )


def parse_labelstudio_export(input_path: str | Path) -> ParsedLabelStudioExport:
    """Read and validate the supported Label Studio JSON export shape."""
    payload = _load_tasks(input_path)

    annotations: list[ReviewedAnnotation] = []
    seen_words: set[str] = set()
    skipped = 0
    for task_index, task in enumerate(payload):
        parsed = _parse_task(task, task_index)
        if parsed is None:
            skipped += 1
            continue
        reviewed = parsed.reviewed
        if reviewed.normalized_word in seen_words:
            raise LabelStudioImportError(
                f"duplicate normalized word in Label Studio export: "
                f"{reviewed.normalized_word!r}"
            )
        seen_words.add(reviewed.normalized_word)
        annotations.append(reviewed)
    return ParsedLabelStudioExport(
        annotations=tuple(annotations),
        total_tasks=len(payload),
        skipped_unannotated_tasks=skipped,
    )


def audit_labelstudio_export(input_path: str | Path) -> LabelStudioAuditSummary:
    """Validate an export without writing it and report genuine human edits."""
    payload = _load_tasks(input_path)
    changes: list[LabelStudioAnnotationChange] = []
    seen_words: set[str] = set()
    skipped = 0
    completed = 0
    unchanged = 0
    origin_changes = 0
    conversion_changes = 0

    for task_index, task in enumerate(payload):
        parsed = _parse_task(task, task_index)
        if parsed is None:
            skipped += 1
            continue
        reviewed = parsed.reviewed
        if reviewed.normalized_word in seen_words:
            raise LabelStudioImportError(
                f"duplicate normalized word in Label Studio export: "
                f"{reviewed.normalized_word!r}"
            )
        seen_words.add(reviewed.normalized_word)
        completed += 1

        origin_changed = reviewed.origin != parsed.suggested_origin
        conversion_changed = reviewed.zamanalif_dsl != parsed.suggested_zamanalif
        origin_changes += int(origin_changed)
        conversion_changes += int(conversion_changed)
        if not origin_changed and not conversion_changed:
            unchanged += 1
            continue
        changes.append(
            LabelStudioAnnotationChange(
                task_id=parsed.task_id,
                word=parsed.word,
                suggested_origin=parsed.suggested_origin,
                reviewed_origin=reviewed.origin,
                suggested_zamanalif=parsed.suggested_zamanalif,
                reviewed_zamanalif=reviewed.zamanalif_dsl,
            )
        )

    return LabelStudioAuditSummary(
        total_tasks=len(payload),
        completed_tasks=completed,
        unchanged_tasks=unchanged,
        skipped_unannotated_tasks=skipped,
        origin_changes=origin_changes,
        conversion_changes=conversion_changes,
        changes=tuple(changes),
    )


def _load_tasks(input_path: str | Path) -> list[Any]:
    path = Path(input_path)
    if not path.exists():
        raise LabelStudioImportError(f"Label Studio export does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LabelStudioImportError(f"cannot read Label Studio export: {exc}") from exc
    if isinstance(payload, dict):
        payload = payload.get("tasks")
        if not isinstance(payload, list):
            raise LabelStudioImportError(
                "Label Studio API response must contain a tasks list"
            )
    if not isinstance(payload, list):
        raise LabelStudioImportError(
            "Label Studio export must be a JSON array or task API response"
        )
    return payload


def _load_contextual_homonym_words(conn: sqlite3.Connection) -> set[str]:
    words = {
        str(row[0])
        for row in conn.execute(
            """
            select normalized_word
            from word_resolutions
            where decision = 'contextual_homonym'
            """
        ).fetchall()
    }
    table_exists = conn.execute(
        """
        select 1
        from sqlite_master
        where type = 'table' and name = 'preannotation_state'
        """
    ).fetchone()
    if table_exists is None:
        return words

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
            raise LabelStudioImportError(
                f"{sample_id}: invalid tokens_json in database: {exc}"
            ) from exc
        if not isinstance(tokens, list):
            raise LabelStudioImportError(
                f"{sample_id}: tokens_json must contain a token list"
            )
        for token in tokens:
            if not isinstance(token, dict) or token.get("homonym") is not True:
                continue
            text = token.get("text")
            if not isinstance(text, str):
                continue
            normalized = normalize_word(text)
            if normalized:
                words.add(normalized)
    return words


def _parse_task(task: Any, task_index: int) -> _ParsedTask | None:
    context = f"task {task_index}"
    if not isinstance(task, dict):
        raise LabelStudioImportError(f"{context} must be an object")
    data = task.get("data")
    if not isinstance(data, dict):
        raise LabelStudioImportError(f"{context}.data must be an object")
    surface = data.get("cyrl_word")
    if not isinstance(surface, str) or not surface:
        raise LabelStudioImportError(f"{context} has invalid data.cyrl_word")
    normalized = normalize_word(surface)
    if not normalized:
        raise LabelStudioImportError(f"{context} has no Cyrillic word in data.cyrl_word")
    suggested_origin = data.get("gemini_origin")
    if suggested_origin is not None and suggested_origin not in ALLOWED_ORIGINS:
        raise LabelStudioImportError(f"{context} has invalid data.gemini_origin")
    suggested_zamanalif = data.get("auto_zamanalif")
    if suggested_zamanalif is not None and (
        not isinstance(suggested_zamanalif, str) or not suggested_zamanalif
    ):
        raise LabelStudioImportError(f"{context} has invalid data.auto_zamanalif")

    raw_annotations = task.get("annotations", [])
    if raw_annotations is None:
        raw_annotations = []
    if not isinstance(raw_annotations, list):
        raise LabelStudioImportError(f"{context}.annotations must be a list")

    decisions: set[tuple[str, str]] = set()
    for annotation_index, annotation in enumerate(raw_annotations):
        if not isinstance(annotation, dict):
            raise LabelStudioImportError(
                f"{context} annotation {annotation_index} must be an object"
            )
        if annotation.get("was_cancelled") is True:
            continue
        result = annotation.get("result", [])
        if result is None:
            result = []
        if not isinstance(result, list):
            raise LabelStudioImportError(
                f"{context} annotation {annotation_index}.result must be a list"
            )
        if not result:
            continue
        decisions.add(
            _parse_result(
                result,
                context,
                annotation_index,
                suggested_origin=suggested_origin,
                suggested_zamanalif=suggested_zamanalif,
            )
        )

    if not decisions:
        return None
    if len(decisions) != 1:
        raise LabelStudioImportError(f"{context} has conflicting completed annotations")
    origin, zamanalif_dsl = next(iter(decisions))
    audit_origin = suggested_origin if suggested_origin is not None else origin
    audit_zamanalif = (
        suggested_zamanalif
        if suggested_zamanalif is not None
        else zamanalif_dsl
    )
    return _ParsedTask(
        reviewed=ReviewedAnnotation(
            normalized_word=normalized,
            zamanalif_dsl=zamanalif_dsl,
            origin=origin,
        ),
        task_id=str(task.get("id", task_index)),
        word=surface,
        suggested_origin=audit_origin,
        suggested_zamanalif=audit_zamanalif,
    )


def _parse_result(
    results: list[Any],
    task_context: str,
    annotation_index: int,
    *,
    suggested_origin: str | None,
    suggested_zamanalif: str | None,
) -> tuple[str, str]:
    context = f"{task_context} annotation {annotation_index}"
    origins: list[str] = []
    conversions: list[str] = []
    for result_index, result in enumerate(results):
        if not isinstance(result, dict):
            raise LabelStudioImportError(
                f"{context} result {result_index} must be an object"
            )
        from_name = result.get("from_name")
        if from_name == ORIGIN_CONTROL:
            origins.append(_parse_origin(result, context, result_index))
        elif from_name == CONVERSION_CONTROL:
            conversions.append(
                _parse_conversion(
                    result,
                    context,
                    result_index,
                    suggested_zamanalif=suggested_zamanalif,
                )
            )

    if len(origins) > 1:
        raise LabelStudioImportError(
            f"{context} must contain at most one {ORIGIN_CONTROL!r} result"
        )
    if len(conversions) != 1:
        raise LabelStudioImportError(
            f"{context} must contain exactly one {CONVERSION_CONTROL!r} result"
        )
    if not origins and suggested_origin is None:
        raise LabelStudioImportError(
            f"{context} must contain exactly one {ORIGIN_CONTROL!r} result "
            "when data.gemini_origin is absent"
        )
    return origins[0] if origins else suggested_origin, conversions[0]


def _parse_origin(result: dict[str, Any], context: str, result_index: int) -> str:
    if result.get("type") != "choices":
        raise LabelStudioImportError(
            f"{context} result {result_index} origin type must be 'choices'"
        )
    value = result.get("value")
    choices = value.get("choices") if isinstance(value, dict) else None
    if not isinstance(choices, list) or len(choices) != 1:
        raise LabelStudioImportError(
            f"{context} result {result_index} must select exactly one origin"
        )
    origin = choices[0]
    if origin not in ALLOWED_ORIGINS:
        raise LabelStudioImportError(
            f"{context} result {result_index} has invalid origin: {origin!r}"
        )
    return origin


def _parse_conversion(
    result: dict[str, Any],
    context: str,
    result_index: int,
    *,
    suggested_zamanalif: str | None,
) -> str:
    if result.get("type") != "textarea":
        raise LabelStudioImportError(
            f"{context} result {result_index} conversion type must be 'textarea'"
        )
    value = result.get("value")
    texts = value.get("text") if isinstance(value, dict) else None
    if not isinstance(texts, list) or not texts or any(
        not isinstance(text, str) for text in texts
    ):
        raise LabelStudioImportError(
            f"{context} result {result_index} must contain non-empty text values"
        )
    if any(not text for text in texts):
        raise LabelStudioImportError(
            f"{context} result {result_index} conversion must not be empty"
        )
    if len(texts) > 1 and (
        suggested_zamanalif is None or texts[0] != suggested_zamanalif
    ):
        raise LabelStudioImportError(
            f"{context} result {result_index} has multiple conversion values "
            "without the exported suggestion first"
        )
    zamanalif_dsl = texts[-1]
    try:
        parse_dsl(zamanalif_dsl)
    except DslError as exc:
        raise LabelStudioImportError(
            f"{context} result {result_index} has invalid Zamanalif DSL: {exc}"
        ) from exc
    return zamanalif_dsl
