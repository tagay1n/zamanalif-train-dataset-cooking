from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

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
    skipped_unannotated_tasks: int


@dataclass(frozen=True)
class ParsedLabelStudioExport:
    annotations: tuple[ReviewedAnnotation, ...]
    total_tasks: int
    skipped_unannotated_tasks: int


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
            imported_words = {item.normalized_word for item in parsed.annotations}
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
            for item in parsed.annotations:
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
        skipped_unannotated_tasks=parsed.skipped_unannotated_tasks,
    )


def parse_labelstudio_export(input_path: str | Path) -> ParsedLabelStudioExport:
    """Read and validate the supported Label Studio JSON export shape."""
    path = Path(input_path)
    if not path.exists():
        raise LabelStudioImportError(f"Label Studio export does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LabelStudioImportError(f"cannot read Label Studio export: {exc}") from exc
    if not isinstance(payload, list):
        raise LabelStudioImportError("Label Studio export must be a JSON array")

    annotations: list[ReviewedAnnotation] = []
    seen_words: set[str] = set()
    skipped = 0
    for task_index, task in enumerate(payload):
        parsed = _parse_task(task, task_index)
        if parsed is None:
            skipped += 1
            continue
        if parsed.normalized_word in seen_words:
            raise LabelStudioImportError(
                f"duplicate normalized word in Label Studio export: "
                f"{parsed.normalized_word!r}"
            )
        seen_words.add(parsed.normalized_word)
        annotations.append(parsed)
    return ParsedLabelStudioExport(
        annotations=tuple(annotations),
        total_tasks=len(payload),
        skipped_unannotated_tasks=skipped,
    )


def _parse_task(task: Any, task_index: int) -> ReviewedAnnotation | None:
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
        decisions.add(_parse_result(result, context, annotation_index))

    if not decisions:
        return None
    if len(decisions) != 1:
        raise LabelStudioImportError(f"{context} has conflicting completed annotations")
    origin, zamanalif_dsl = next(iter(decisions))
    return ReviewedAnnotation(
        normalized_word=normalized,
        zamanalif_dsl=zamanalif_dsl,
        origin=origin,
    )


def _parse_result(
    results: list[Any],
    task_context: str,
    annotation_index: int,
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
            conversions.append(_parse_conversion(result, context, result_index))

    if len(origins) != 1:
        raise LabelStudioImportError(
            f"{context} must contain exactly one {ORIGIN_CONTROL!r} result"
        )
    if len(conversions) != 1:
        raise LabelStudioImportError(
            f"{context} must contain exactly one {CONVERSION_CONTROL!r} result"
        )
    return origins[0], conversions[0]


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


def _parse_conversion(result: dict[str, Any], context: str, result_index: int) -> str:
    if result.get("type") != "textarea":
        raise LabelStudioImportError(
            f"{context} result {result_index} conversion type must be 'textarea'"
        )
    value = result.get("value")
    texts = value.get("text") if isinstance(value, dict) else None
    if not isinstance(texts, list) or len(texts) != 1 or not isinstance(texts[0], str):
        raise LabelStudioImportError(
            f"{context} result {result_index} must contain exactly one text value"
        )
    zamanalif_dsl = texts[0]
    if not zamanalif_dsl:
        raise LabelStudioImportError(
            f"{context} result {result_index} conversion must not be empty"
        )
    try:
        parse_dsl(zamanalif_dsl)
    except DslError as exc:
        raise LabelStudioImportError(
            f"{context} result {result_index} has invalid Zamanalif DSL: {exc}"
        ) from exc
    return zamanalif_dsl
