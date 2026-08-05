from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .contextual_review import (
    PROJECT_KEY as CONTEXTUAL_PROJECT_KEY,
    ContextualReviewError,
    InitialReferenceIdentity,
    OccurrenceKey,
    contextual_conversion_branches,
    contextual_review_occurrences,
    effective_contextual_homonym_words,
    ensure_contextual_review_schema,
    initial_reference_groups,
    validate_contextual_review,
)
from .conflict_resolver import ensure_word_resolution_schema
from .conversion import DslError, parse_dsl
from .morphology import (
    MorphIdentity,
    MorphologyAnalyzer,
    default_morphology_analyzer,
)
from .word_export import (
    CATCHALL_META_FIELDS,
    CATCHALL_TASK_SCHEMA_VERSION,
    CONTEXTUAL_DATA_FIELDS,
    CONTEXTUAL_META_FIELDS,
    DICTIONARY_DATA_FIELDS,
    DICTIONARY_META_FIELDS,
    TASK_SCHEMA_VERSION,
    classify_project,
    conversion_branches,
    dictionary_project_keys,
    eligible_catchall_words,
    ensure_review_state_schema,
    is_safe_family_member,
    normalize_word,
)


ORIGIN_CONTROL = "reviewed_origin"
CONVERSION_CONTROL = "corrected_zamanalif"
HOMONYM_CONTROL = "is_homonym"
HOMONYM_CHOICE = "Homonym"
REVIEWED_ORIGINS = frozenset({"N", "RL"})
SUGGESTED_ORIGINS = frozenset({"N", "RL", "U"})


class LabelStudioImportError(ValueError):
    """Raised when Label Studio annotations cannot be imported safely."""


@dataclass(frozen=True)
class ReviewedAnnotation:
    normalized_word: str
    zamanalif_dsl: str | None
    origin: str | None
    is_homonym: bool = False
    sample_id: str | None = None
    token_index: int | None = None
    family_members: tuple[str, ...] = ()
    morphology: MorphIdentity | None = None
    analyzer_revision: str | None = None
    suggested_origin: str | None = None


@dataclass(frozen=True)
class LabelStudioImportSummary:
    project_key: str
    total_tasks: int
    completed_tasks: int
    imported_items: int
    homonym_items: int
    inherited_items: int
    inherited_current_batch_items: int
    inherited_backfill_items: int
    inherited_literal_subword_items: int
    inherited_deterministic_divergent_items: int
    inherited_source_families: int
    contextual_initial_source_groups: int
    contextual_initial_propagated_items: int
    contextual_initial_backfill_items: int
    unchanged_items: int
    skipped_unannotated_tasks: int


@dataclass(frozen=True)
class ParsedLabelStudioExport:
    project_key: str
    annotations: tuple[ReviewedAnnotation, ...]
    total_tasks: int
    skipped_unannotated_tasks: int


@dataclass(frozen=True)
class LabelStudioAnnotationChange:
    task_id: str
    word: str
    suggested_origin: str
    reviewed_origin: str | None
    suggested_zamanalif: str
    reviewed_zamanalif: str | None
    is_homonym: bool = False


@dataclass(frozen=True)
class LabelStudioAuditSummary:
    project_key: str
    total_tasks: int
    completed_tasks: int
    unchanged_tasks: int
    skipped_unannotated_tasks: int
    origin_changes: int
    conversion_changes: int
    homonym_changes: int
    contextual_initial_source_groups: int
    contextual_initial_propagated_items: int
    contextual_initial_backfill_items: int
    changes: tuple[LabelStudioAnnotationChange, ...]


@dataclass(frozen=True)
class _ParsedTask:
    reviewed: ReviewedAnnotation
    task_id: str
    word: str
    suggested_origin: str
    suggested_zamanalif: str


@dataclass(frozen=True)
class _AnnotationDecision:
    is_homonym: bool
    origin: str | None = None
    zamanalif_dsl: str | None = None


@dataclass(frozen=True)
class _ContextualPropagationPlan:
    assignments: dict[OccurrenceKey, tuple[str, str, str]]
    source_keys: frozenset[OccurrenceKey]
    annotated_initial_identities: frozenset[InitialReferenceIdentity]
    identity_by_occurrence: dict[OccurrenceKey, InitialReferenceIdentity]
    existing: dict[OccurrenceKey, tuple[str, str, str]]
    propagated_items: int
    backfill_items: int


def import_labelstudio_annotations(
    db_path: str | Path,
    input_path: str | Path,
    *,
    morphology_analyzer: MorphologyAnalyzer | None = None,
) -> LabelStudioImportSummary:
    """Strictly validate one project backup and atomically import its decisions."""
    database = Path(db_path)
    if not database.exists():
        raise LabelStudioImportError(f"database file does not exist: {database}")
    parsed = parse_labelstudio_export(input_path)
    now = datetime.now(timezone.utc).isoformat()
    imported = 0
    inherited = 0
    inherited_current_batch = 0
    inherited_backfill = 0
    inherited_literal_subwords = 0
    inherited_deterministic_divergent = 0
    inherited_source_families = 0
    contextual_initial_source_groups = 0
    contextual_initial_propagated = 0
    contextual_initial_backfill = 0
    unchanged = 0

    with closing(sqlite3.connect(database)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            ensure_review_state_schema(conn)
            ensure_contextual_review_schema(conn)
            ensure_word_resolution_schema(conn)
            effective_homonyms = effective_contextual_homonym_words(conn)
            if parsed.project_key == CONTEXTUAL_PROJECT_KEY:
                plan = _plan_contextual_propagation(
                    conn,
                    parsed.annotations,
                    effective_homonyms,
                )
                contextual_initial_source_groups = len(
                    plan.annotated_initial_identities
                )
                contextual_initial_propagated = plan.propagated_items
                contextual_initial_backfill = plan.backfill_items
                unchanged = sum(
                    plan.existing.get(key) == plan.assignments.get(key)
                    for key in plan.source_keys
                )
                for key, current in sorted(plan.assignments.items()):
                    if key in plan.existing:
                        continue
                    normalized_word, zamanalif_dsl, origin = current
                    conn.execute(
                        """
                        insert into contextual_reviews(
                            sample_id, token_index, normalized_word,
                            zamanalif_dsl, origin, updated_at
                        ) values (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            key.sample_id,
                            key.token_index,
                            normalized_word,
                            zamanalif_dsl,
                            origin,
                            now,
                        ),
                    )
                    imported += 1
            else:
                analyzer = morphology_analyzer or default_morphology_analyzer()
                homonym_items = [
                    item for item in parsed.annotations if item.is_homonym
                ]
                regular_items = [
                    item for item in parsed.annotations if not item.is_homonym
                ]
                existing_resolutions = {
                    str(row[0]): str(row[1])
                    for row in conn.execute(
                        "select normalized_word, decision from word_resolutions"
                    ).fetchall()
                }
                for item in homonym_items:
                    changed = _store_homonym_decision(
                        conn,
                        item.normalized_word,
                        existing_resolutions,
                        now,
                    )
                    imported += int(changed)
                    unchanged += int(not changed)

                effective_homonyms = effective_contextual_homonym_words(conn)
                regular_words = {
                    item.normalized_word for item in regular_items
                }
                homonyms = sorted(regular_words & effective_homonyms)
                if homonyms:
                    raise LabelStudioImportError(
                        "dictionary project contains contextual homonyms: "
                        + ", ".join(homonyms[:20])
                    )
                eligible = eligible_catchall_words(conn)
                existing = {
                    str(row[0]): (str(row[1]), str(row[2]))
                    for row in conn.execute(
                        """
                        select normalized_word, zamanalif_dsl, origin
                        from reviewed_words
                        """
                    ).fetchall()
                }
                derived_words = {
                    str(row[0])
                    for row in conn.execute(
                        "select normalized_word from reviewed_word_derivations"
                    ).fetchall()
                }
                analysis_words = (
                    set(eligible)
                    | (set(existing) - derived_words)
                    | regular_words
                )
                analyses = analyzer.analyze(sorted(analysis_words))
                if parsed.project_key == "catchall":
                    regular_items = [
                        _reconstruct_imported_family(
                            item,
                            analyses,
                            eligible,
                            analyzer,
                        )
                        for item in regular_items
                    ]
                for item in regular_items:
                    origin, zamanalif_dsl = _regular_values(item)
                    current = (zamanalif_dsl, origin)
                    previous = existing.get(item.normalized_word)
                    if previous is not None:
                        if previous != current:
                            raise LabelStudioImportError(
                                f"reviewed word conflict for {item.normalized_word!r}: "
                                f"database has {previous!r}, import has {current!r}"
                            )
                        unchanged += 1
                        conn.execute(
                            """
                            delete from reviewed_word_derivations
                            where normalized_word = ?
                            """,
                            (item.normalized_word,),
                        )
                        derived_words.discard(item.normalized_word)
                        continue
                    conn.execute(
                        """
                        insert into reviewed_words(
                            normalized_word, zamanalif_dsl, origin, updated_at
                        ) values (?, ?, ?, ?)
                        """,
                        (
                            item.normalized_word,
                            zamanalif_dsl,
                            origin,
                            now,
                        ),
                    )
                    existing[item.normalized_word] = current
                    imported += 1
                for item in regular_items:
                    if parsed.project_key != "catchall":
                        continue
                    inherited += _propagate_imported_family(
                        conn,
                        item,
                        analyses,
                        analyzer.revision,
                        existing,
                        derived_words,
                        now,
                    )
                inherited += _backfill_reviewed_families(
                    conn,
                    analyses,
                    analyzer.revision,
                    eligible,
                    existing,
                    derived_words,
                    now,
                )
                new_derivations = conn.execute(
                    """
                    select normalized_word, source_word
                    from reviewed_word_derivations
                    where created_at = ?
                    """,
                    (now,),
                ).fetchall()
                inherited_current_batch = sum(
                    str(row[1]) in regular_words for row in new_derivations
                )
                inherited_backfill = len(new_derivations) - inherited_current_batch
                inherited_literal_subwords = sum(
                    str(row[1]).startswith(str(row[0]))
                    for row in new_derivations
                )
                inherited_deterministic_divergent = (
                    len(new_derivations) - inherited_literal_subwords
                )
                inherited_source_families = len(
                    {str(row[1]) for row in new_derivations}
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return LabelStudioImportSummary(
        project_key=parsed.project_key,
        total_tasks=parsed.total_tasks,
        completed_tasks=len(parsed.annotations),
        imported_items=imported,
        homonym_items=sum(item.is_homonym for item in parsed.annotations),
        inherited_items=inherited,
        inherited_current_batch_items=inherited_current_batch,
        inherited_backfill_items=inherited_backfill,
        inherited_literal_subword_items=inherited_literal_subwords,
        inherited_deterministic_divergent_items=inherited_deterministic_divergent,
        inherited_source_families=inherited_source_families,
        contextual_initial_source_groups=contextual_initial_source_groups,
        contextual_initial_propagated_items=contextual_initial_propagated,
        contextual_initial_backfill_items=contextual_initial_backfill,
        unchanged_items=unchanged,
        skipped_unannotated_tasks=parsed.skipped_unannotated_tasks,
    )


def _plan_contextual_propagation(
    conn: sqlite3.Connection,
    annotations: tuple[ReviewedAnnotation, ...],
    effective_homonyms: set[str],
) -> _ContextualPropagationPlan:
    try:
        occurrences = [
            item
            for item in contextual_review_occurrences(conn, effective_homonyms)
            if contextual_conversion_branches(item.normalized_word).state
            != "origin_independent"
        ]
    except ContextualReviewError as exc:
        raise LabelStudioImportError(str(exc)) from exc
    occurrence_by_key = {item.key: item for item in occurrences}
    identity_by_occurrence, members_by_identity = initial_reference_groups(occurrences)
    existing = {
        OccurrenceKey(str(row[0]), int(row[1])): (
            str(row[2]),
            str(row[3]),
            str(row[4]),
        )
        for row in conn.execute(
            """
            select sample_id, token_index, normalized_word,
                   zamanalif_dsl, origin
            from contextual_reviews
            """
        ).fetchall()
    }

    existing_group_decisions: dict[InitialReferenceIdentity, tuple[str, str]] = {}
    for identity, members in members_by_identity.items():
        decisions = {
            (existing[key][1], existing[key][2])
            for key in members
            if key in existing
        }
        if len(decisions) > 1:
            raise LabelStudioImportError(
                "conflicting stored initial reviews for "
                f"{identity.display!r}: {sorted(decisions)!r}"
            )
        if decisions:
            existing_group_decisions[identity] = next(iter(decisions))

    source_keys: set[OccurrenceKey] = set()
    annotated_initial_identities: set[InitialReferenceIdentity] = set()
    decisions: dict[InitialReferenceIdentity | OccurrenceKey, tuple[str, str]] = {}
    for item in annotations:
        _validate_contextual_source(conn, item, effective_homonyms)
        origin, zamanalif_dsl = _regular_values(item)
        key = OccurrenceKey(str(item.sample_id), int(item.token_index))
        if key not in occurrence_by_key:
            raise LabelStudioImportError(
                f"contextual occurrence is no longer reviewable: {key}"
            )
        source_keys.add(key)
        identity = identity_by_occurrence.get(key)
        decision_key: InitialReferenceIdentity | OccurrenceKey = identity or key
        decision = (zamanalif_dsl, origin)
        previous = decisions.get(decision_key)
        if previous is not None and previous != decision:
            label = identity.display if identity is not None else str(key)
            raise LabelStudioImportError(
                f"conflicting annotations for initial reference {label!r}: "
                f"{previous!r} and {decision!r}"
            )
        decisions[decision_key] = decision
        if identity is not None:
            annotated_initial_identities.add(identity)

    for identity in annotated_initial_identities:
        stored = existing_group_decisions.get(identity)
        imported = decisions[identity]
        if stored is not None and stored != imported:
            raise LabelStudioImportError(
                f"initial review conflict for {identity.display!r}: "
                f"database has {stored!r}, import has {imported!r}"
            )

    assignments: dict[OccurrenceKey, tuple[str, str, str]] = {}

    def assign(key: OccurrenceKey, decision: tuple[str, str]) -> None:
        occurrence = occurrence_by_key[key]
        current = (occurrence.normalized_word, decision[0], decision[1])
        previous = existing.get(key)
        if previous is not None and previous != current:
            raise LabelStudioImportError(
                f"contextual review conflict for {key}: "
                f"database has {previous!r}, import implies {current!r}"
            )
        assignments[key] = current

    for decision_key, decision in decisions.items():
        if isinstance(decision_key, OccurrenceKey):
            assign(decision_key, decision)
            continue
        for key in members_by_identity[decision_key]:
            assign(key, decision)

    for identity, decision in existing_group_decisions.items():
        if identity in annotated_initial_identities:
            continue
        for key in members_by_identity[identity]:
            assign(key, decision)

    new_keys = set(assignments) - set(existing)
    propagated = sum(
        key not in source_keys
        and identity_by_occurrence.get(key) in annotated_initial_identities
        for key in new_keys
    )
    backfilled = sum(
        identity_by_occurrence.get(key) is not None
        and identity_by_occurrence[key] not in annotated_initial_identities
        for key in new_keys
    )
    return _ContextualPropagationPlan(
        assignments=assignments,
        source_keys=frozenset(source_keys),
        annotated_initial_identities=frozenset(annotated_initial_identities),
        identity_by_occurrence=identity_by_occurrence,
        existing=existing,
        propagated_items=propagated,
        backfill_items=backfilled,
    )


def _regular_values(item: ReviewedAnnotation) -> tuple[str, str]:
    if item.is_homonym or item.origin is None or item.zamanalif_dsl is None:
        raise LabelStudioImportError("regular annotation lacks origin or conversion")
    return item.origin, item.zamanalif_dsl


def _store_homonym_decision(
    conn: sqlite3.Connection,
    word: str,
    existing_resolutions: dict[str, str],
    now: str,
) -> bool:
    derived_members = [
        str(row[0])
        for row in conn.execute(
            """
            select normalized_word
            from reviewed_word_derivations
            where source_word = ?
            """,
            (word,),
        ).fetchall()
    ]
    removed_reviews = conn.execute(
        "delete from reviewed_words where normalized_word = ?",
        (word,),
    ).rowcount
    for member in derived_members:
        removed_reviews += conn.execute(
            "delete from reviewed_words where normalized_word = ?",
            (member,),
        ).rowcount
    removed_derivations = conn.execute(
        """
        delete from reviewed_word_derivations
        where normalized_word = ? or source_word = ?
        """,
        (word, word),
    ).rowcount

    previous = existing_resolutions.get(word)
    if (
        previous == "contextual_homonym"
        and removed_reviews == 0
        and removed_derivations == 0
    ):
        return False
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
    existing_resolutions[word] = "contextual_homonym"
    return True


def _reconstruct_imported_family(
    item: ReviewedAnnotation,
    analyses: dict[str, MorphIdentity | None],
    eligible: dict[str, Any],
    analyzer: MorphologyAnalyzer,
) -> ReviewedAnnotation:
    candidate = eligible.get(item.normalized_word)
    if candidate is None:
        raise LabelStudioImportError(
            f"catchall word is no longer eligible: {item.normalized_word!r}"
        )
    if candidate.origin != item.suggested_origin:
        raise LabelStudioImportError(
            "catchall word changed predicted origin: "
            f"{item.normalized_word!r}"
        )
    identity = analyses.get(item.normalized_word)
    if identity is None:
        members = (item.normalized_word,)
    else:
        related = sorted(
            (
                word
                for word, related_candidate in eligible.items()
                if word != item.normalized_word
                and is_safe_family_member(
                    item.normalized_word,
                    word,
                    identity.lemma,
                )
                and related_candidate.origin == item.suggested_origin
                and analyses.get(word) == identity
            ),
            key=lambda word: (-len(word), word),
        )
        members = (item.normalized_word, *related)
    return replace(
        item,
        family_members=members,
        morphology=identity,
        analyzer_revision=analyzer.revision,
    )


def _propagate_imported_family(
    conn: sqlite3.Connection,
    item: ReviewedAnnotation,
    analyses: dict[str, MorphIdentity | None],
    analyzer_revision: str,
    existing: dict[str, tuple[str, str]],
    derived_words: set[str],
    now: str,
) -> int:
    identity = item.morphology
    if identity is None or len(item.family_members) == 1:
        return 0
    origin, zamanalif_dsl = _regular_values(item)
    canonical = conversion_branches(item.normalized_word).suggestion(origin)
    if zamanalif_dsl != canonical:
        return 0

    inserted = 0
    for word in item.family_members[1:]:
        if not is_safe_family_member(
            item.normalized_word,
            word,
            identity.lemma,
        ):
            raise LabelStudioImportError(
                f"catchall family member is not safely covered: {word!r}"
            )
        if analyses.get(word) != identity:
            raise LabelStudioImportError(
                f"catchall family morphology changed for {word!r}"
            )
        if classify_project(word, origin)["key"] != "catchall":
            continue
        member_zamanalif = conversion_branches(word).suggestion(origin)
        if not member_zamanalif:
            continue
        inserted += _store_inherited_review(
            conn,
            word=word,
            zamanalif_dsl=member_zamanalif,
            origin=origin,
            source_word=item.normalized_word,
            identity=identity,
            analyzer_revision=analyzer_revision,
            existing=existing,
            derived_words=derived_words,
            now=now,
        )
    return inserted


def _backfill_reviewed_families(
    conn: sqlite3.Connection,
    analyses: dict[str, MorphIdentity | None],
    analyzer_revision: str,
    eligible: dict[str, Any],
    existing: dict[str, tuple[str, str]],
    derived_words: set[str],
    now: str,
) -> int:
    anchors: dict[tuple[MorphIdentity, str], list[str]] = {}
    for word, (zamanalif_dsl, origin) in existing.items():
        if word in derived_words or origin not in REVIEWED_ORIGINS:
            continue
        candidate = eligible.get(word)
        identity = analyses.get(word)
        if (
            candidate is None
            or candidate.origin != origin
            or identity is None
            or classify_project(word, origin)["key"] != "catchall"
            or conversion_branches(word).suggestion(origin) != zamanalif_dsl
        ):
            continue
        anchors.setdefault((identity, origin), []).append(word)

    inserted = 0
    for word, candidate in eligible.items():
        if word in existing:
            continue
        identity = analyses.get(word)
        if identity is None:
            continue
        sources = [
            source
            for source in anchors.get((identity, candidate.origin), [])
            if source.startswith(word)
        ]
        if not sources or classify_project(word, candidate.origin)["key"] != "catchall":
            continue
        source = min(
            sources,
            key=lambda value: (
                value != identity.lemma,
                len(value),
                value,
            ),
        )
        zamanalif_dsl = conversion_branches(word).suggestion(candidate.origin)
        if not zamanalif_dsl:
            continue
        inserted += _store_inherited_review(
            conn,
            word=word,
            zamanalif_dsl=zamanalif_dsl,
            origin=candidate.origin,
            source_word=source,
            identity=identity,
            analyzer_revision=analyzer_revision,
            existing=existing,
            derived_words=derived_words,
            now=now,
        )
    return inserted


def _store_inherited_review(
    conn: sqlite3.Connection,
    *,
    word: str,
    zamanalif_dsl: str,
    origin: str,
    source_word: str,
    identity: MorphIdentity,
    analyzer_revision: str,
    existing: dict[str, tuple[str, str]],
    derived_words: set[str],
    now: str,
) -> int:
    current = (zamanalif_dsl, origin)
    previous = existing.get(word)
    if previous is not None:
        if previous != current and word in derived_words:
            raise LabelStudioImportError(
                f"inherited review conflict for {word!r}: "
                f"database has {previous!r}, family implies {current!r}"
            )
        return 0
    conn.execute(
        """
        insert into reviewed_words(
            normalized_word, zamanalif_dsl, origin, updated_at
        ) values (?, ?, ?, ?)
        """,
        (word, zamanalif_dsl, origin, now),
    )
    conn.execute(
        """
        insert into reviewed_word_derivations(
            normalized_word, source_word, lemma, part_of_speech,
            analyzer_revision, created_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (
            word,
            source_word,
            identity.lemma,
            identity.part_of_speech,
            analyzer_revision,
            now,
        ),
    )
    existing[word] = current
    derived_words.add(word)
    return 1


def parse_labelstudio_export(input_path: str | Path) -> ParsedLabelStudioExport:
    payload = _load_tasks(input_path)
    project_key = _project_key(payload)
    annotations: list[ReviewedAnnotation] = []
    seen: set[str | tuple[str, int]] = set()
    skipped = 0
    for task_index, task in enumerate(payload):
        parsed = _parse_task(task, task_index, project_key)
        if parsed is None:
            skipped += 1
            continue
        reviewed = parsed.reviewed
        identity: str | tuple[str, int]
        if project_key == CONTEXTUAL_PROJECT_KEY:
            identity = (str(reviewed.sample_id), int(reviewed.token_index))
        else:
            identity = reviewed.normalized_word
        if identity in seen:
            raise LabelStudioImportError(
                f"duplicate task identity in Label Studio export: {identity!r}"
            )
        seen.add(identity)
        annotations.append(reviewed)
    return ParsedLabelStudioExport(
        project_key=project_key,
        annotations=tuple(annotations),
        total_tasks=len(payload),
        skipped_unannotated_tasks=skipped,
    )


def audit_labelstudio_export(
    db_path: str | Path,
    input_path: str | Path,
) -> LabelStudioAuditSummary:
    database = Path(db_path)
    if not database.exists():
        raise LabelStudioImportError(f"database file does not exist: {database}")
    payload = _load_tasks(input_path)
    project_key = _project_key(payload)
    changes: list[LabelStudioAnnotationChange] = []
    seen: set[str | tuple[str, int]] = set()
    skipped = 0
    unchanged = 0
    origin_changes = 0
    conversion_changes = 0
    homonym_changes = 0
    contextual_initial_source_groups = 0
    contextual_initial_propagated = 0
    contextual_initial_backfill = 0
    contextual_annotations: list[ReviewedAnnotation] = []

    with closing(sqlite3.connect(database)) as conn:
        effective_homonyms = effective_contextual_homonym_words(conn)
        for task_index, task in enumerate(payload):
            parsed = _parse_task(task, task_index, project_key)
            if parsed is None:
                skipped += 1
                continue
            reviewed = parsed.reviewed
            if project_key == CONTEXTUAL_PROJECT_KEY:
                identity: str | tuple[str, int] = (
                    str(reviewed.sample_id),
                    int(reviewed.token_index),
                )
                _validate_contextual_source(conn, reviewed, effective_homonyms)
                contextual_annotations.append(reviewed)
            else:
                identity = reviewed.normalized_word
                if (
                    not reviewed.is_homonym
                    and reviewed.normalized_word in effective_homonyms
                ):
                    raise LabelStudioImportError(
                        "dictionary project contains contextual homonym: "
                        f"{reviewed.normalized_word!r}"
                    )
            if identity in seen:
                raise LabelStudioImportError(
                    f"duplicate task identity in Label Studio export: {identity!r}"
                )
            seen.add(identity)
            if reviewed.is_homonym:
                homonym_changes += 1
                changes.append(
                    LabelStudioAnnotationChange(
                        task_id=parsed.task_id,
                        word=parsed.word,
                        suggested_origin=parsed.suggested_origin,
                        reviewed_origin=None,
                        suggested_zamanalif=parsed.suggested_zamanalif,
                        reviewed_zamanalif=None,
                        is_homonym=True,
                    )
                )
                continue
            origin, zamanalif_dsl = _regular_values(reviewed)
            origin_changed = origin != parsed.suggested_origin
            conversion_changed = zamanalif_dsl != parsed.suggested_zamanalif
            origin_changes += int(origin_changed)
            conversion_changes += int(conversion_changed)
            if not origin_changed and not conversion_changed:
                unchanged += 1
            else:
                changes.append(
                    LabelStudioAnnotationChange(
                        task_id=parsed.task_id,
                        word=parsed.word,
                        suggested_origin=parsed.suggested_origin,
                        reviewed_origin=origin,
                        suggested_zamanalif=parsed.suggested_zamanalif,
                        reviewed_zamanalif=zamanalif_dsl,
                    )
                )

        if project_key == CONTEXTUAL_PROJECT_KEY:
            plan = _plan_contextual_propagation(
                conn,
                tuple(contextual_annotations),
                effective_homonyms,
            )
            contextual_initial_source_groups = len(
                plan.annotated_initial_identities
            )
            contextual_initial_propagated = plan.propagated_items
            contextual_initial_backfill = plan.backfill_items

    completed = len(payload) - skipped
    return LabelStudioAuditSummary(
        project_key=project_key,
        total_tasks=len(payload),
        completed_tasks=completed,
        unchanged_tasks=unchanged,
        skipped_unannotated_tasks=skipped,
        origin_changes=origin_changes,
        conversion_changes=conversion_changes,
        homonym_changes=homonym_changes,
        contextual_initial_source_groups=contextual_initial_source_groups,
        contextual_initial_propagated_items=contextual_initial_propagated,
        contextual_initial_backfill_items=contextual_initial_backfill,
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
    allowed_keys = {"tasks", "total", "total_annotations", "total_predictions"}
    if (
        not isinstance(payload, dict)
        or "tasks" not in payload
        or not set(payload) <= allowed_keys
    ):
        raise LabelStudioImportError(
            "Label Studio backup must use the task API response schema"
        )
    tasks = payload["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise LabelStudioImportError("Label Studio backup tasks must be a non-empty list")
    for field in ("total", "total_annotations", "total_predictions"):
        value = payload.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise LabelStudioImportError(
                f"Label Studio backup has invalid {field}"
            )
    if payload.get("total") is not None and payload["total"] != len(tasks):
        raise LabelStudioImportError(
            "Label Studio backup total does not match tasks"
        )
    return tasks


def _project_key(tasks: list[Any]) -> str:
    keys: set[str] = set()
    for task_index, task in enumerate(tasks):
        meta = task.get("meta") if isinstance(task, dict) else None
        key = meta.get("project_key") if isinstance(meta, dict) else None
        if not isinstance(key, str) or not key:
            raise LabelStudioImportError(
                f"task {task_index} has invalid meta.project_key"
            )
        keys.add(key)
    if len(keys) != 1:
        raise LabelStudioImportError("Label Studio backup mixes project types")
    project_key = next(iter(keys))
    allowed = dictionary_project_keys() | {CONTEXTUAL_PROJECT_KEY}
    if project_key not in allowed:
        raise LabelStudioImportError(f"unknown project_key: {project_key!r}")
    return project_key


def _parse_task(
    task: Any,
    task_index: int,
    project_key: str,
) -> _ParsedTask | None:
    context = f"task {task_index}"
    if not isinstance(task, dict):
        raise LabelStudioImportError(f"{context} must be an object")
    data = task.get("data")
    if not isinstance(data, dict):
        raise LabelStudioImportError(f"{context}.data must be an object")
    expected_data_fields = (
        CONTEXTUAL_DATA_FIELDS
        if project_key == CONTEXTUAL_PROJECT_KEY
        else DICTIONARY_DATA_FIELDS
    )
    if set(data) != expected_data_fields:
        raise LabelStudioImportError(
            f"{context}.data contains unexpected or missing fields"
        )
    meta = task.get("meta")
    if project_key == CONTEXTUAL_PROJECT_KEY:
        expected_meta_fields = CONTEXTUAL_META_FIELDS
        expected_schema_version = TASK_SCHEMA_VERSION
    elif project_key == "catchall":
        expected_meta_fields = CATCHALL_META_FIELDS
        expected_schema_version = CATCHALL_TASK_SCHEMA_VERSION
    else:
        expected_meta_fields = DICTIONARY_META_FIELDS
        expected_schema_version = TASK_SCHEMA_VERSION
    if not isinstance(meta, dict) or set(meta) != expected_meta_fields:
        raise LabelStudioImportError(
            f"{context}.meta contains unexpected or missing fields"
        )
    if meta.get("schema_version") != expected_schema_version:
        raise LabelStudioImportError(f"{context} has unsupported schema_version")
    if meta.get("project_key") != project_key:
        raise LabelStudioImportError(f"{context} has inconsistent project_key")
    surface = data.get("cyrl_word")
    if not isinstance(surface, str) or not surface:
        raise LabelStudioImportError(f"{context} has invalid data.cyrl_word")
    normalized = normalize_word(surface)
    if not normalized:
        raise LabelStudioImportError(f"{context} has no Cyrillic word")
    suggested_origin = data.get("gemini_origin")
    if suggested_origin not in SUGGESTED_ORIGINS:
        raise LabelStudioImportError(f"{context} has invalid data.gemini_origin")
    suggested_zamanalif = data.get("auto_zamanalif")
    if not isinstance(suggested_zamanalif, str):
        raise LabelStudioImportError(f"{context} has invalid data.auto_zamanalif")

    sample_id: str | None = None
    token_index: int | None = None
    if project_key == CONTEXTUAL_PROJECT_KEY:
        sample_id = meta.get("sample_id")
        token_index = meta.get("token_index")
        if not isinstance(sample_id, str) or not sample_id:
            raise LabelStudioImportError(f"{context} has invalid meta.sample_id")
        if not isinstance(token_index, int) or isinstance(token_index, bool) or token_index < 0:
            raise LabelStudioImportError(f"{context} has invalid meta.token_index")
        contextual_zamanalif = {
            origin: data.get(field)
            for origin, field in (
                ("N", "native_zamanalif"),
                ("RL", "loanword_zamanalif"),
            )
        }
        if any(
            not isinstance(value, str)
            for value in contextual_zamanalif.values()
        ):
            raise LabelStudioImportError(
                f"{context} has invalid contextual Zamanalif variants"
            )
    else:
        contextual_zamanalif = None

    annotations = task.get("annotations")
    if not isinstance(annotations, list):
        raise LabelStudioImportError(f"{context}.annotations must be a list")
    decisions: set[_AnnotationDecision] = set()
    for annotation_index, annotation in enumerate(annotations):
        if not isinstance(annotation, dict):
            raise LabelStudioImportError(
                f"{context} annotation {annotation_index} must be an object"
            )
        if annotation.get("was_cancelled") is True:
            continue
        results = annotation.get("result")
        if not isinstance(results, list):
            raise LabelStudioImportError(
                f"{context} annotation {annotation_index}.result must be a list"
            )
        if not results:
            continue
        decisions.add(
            _parse_result(
                results,
                context,
                annotation_index,
                suggested_zamanalif,
                allow_homonym=project_key != CONTEXTUAL_PROJECT_KEY,
                require_origin=project_key == CONTEXTUAL_PROJECT_KEY,
                contextual_zamanalif=contextual_zamanalif,
            )
        )
    if not decisions:
        return None
    if len(decisions) != 1:
        raise LabelStudioImportError(f"{context} has conflicting annotations")
    decision = next(iter(decisions))
    reviewed = ReviewedAnnotation(
        normalized_word=normalized,
        zamanalif_dsl=decision.zamanalif_dsl,
        origin=(
            decision.origin
            if project_key == CONTEXTUAL_PROJECT_KEY
            else suggested_origin
        ),
        is_homonym=decision.is_homonym,
        sample_id=sample_id,
        token_index=token_index,
        family_members=(normalized,),
        suggested_origin=suggested_origin,
    )
    return _ParsedTask(
        reviewed=reviewed,
        task_id=str(task.get("id", task_index)),
        word=surface,
        suggested_origin=suggested_origin,
        suggested_zamanalif=suggested_zamanalif,
    )


def _parse_result(
    results: list[Any],
    task_context: str,
    annotation_index: int,
    suggested_zamanalif: str,
    *,
    allow_homonym: bool,
    require_origin: bool,
    contextual_zamanalif: dict[str, str] | None,
) -> _AnnotationDecision:
    context = f"{task_context} annotation {annotation_index}"
    origins: list[tuple[dict[str, Any], int]] = []
    conversions: list[tuple[dict[str, Any], int]] = []
    homonyms: list[tuple[dict[str, Any], int]] = []
    for result_index, result in enumerate(results):
        if not isinstance(result, dict):
            raise LabelStudioImportError(
                f"{context} result {result_index} must be an object"
            )
        control = result.get("from_name")
        if control == ORIGIN_CONTROL and require_origin:
            origins.append((result, result_index))
        elif control == CONVERSION_CONTROL:
            conversions.append((result, result_index))
        elif control == HOMONYM_CONTROL:
            homonyms.append((result, result_index))
        else:
            raise LabelStudioImportError(
                f"{context} result {result_index} has unexpected control: "
                f"{control!r}"
            )
    if homonyms:
        if not allow_homonym:
            raise LabelStudioImportError(
                f"{context} cannot mark a contextual task as homonym"
            )
        if len(homonyms) != 1:
            raise LabelStudioImportError(
                f"{context} must contain at most one {HOMONYM_CONTROL!r} result"
            )
        _parse_homonym(*homonyms[0], context=context)
        if len(origins) > 1 or len(conversions) > 1:
            raise LabelStudioImportError(
                f"{context} contains duplicate ignored controls"
            )
        return _AnnotationDecision(is_homonym=True)
    if require_origin and len(origins) != 1:
        raise LabelStudioImportError(
            f"{context} must contain exactly one {ORIGIN_CONTROL!r} result"
        )
    if require_origin and len(conversions) > 1:
        raise LabelStudioImportError(
            f"{context} must contain at most one {CONVERSION_CONTROL!r} result"
        )
    if not require_origin and len(conversions) != 1:
        raise LabelStudioImportError(
            f"{context} must contain exactly one {CONVERSION_CONTROL!r} result"
        )
    origin = None
    if require_origin:
        origin_result, origin_index = origins[0]
        origin = _parse_origin(origin_result, context, origin_index)
    if conversions and not _is_empty_conversion(*conversions[0], context=context):
        conversion_result, conversion_index = conversions[0]
        zamanalif_dsl = _parse_conversion(
            conversion_result,
            context,
            conversion_index,
            suggested_zamanalif,
        )
    elif require_origin:
        if contextual_zamanalif is None or origin is None:
            raise LabelStudioImportError(
                f"{context} is missing contextual Zamanalif variants"
            )
        zamanalif_dsl = contextual_zamanalif[origin]
        if not zamanalif_dsl:
            raise LabelStudioImportError(
                f"{context} selected origin {origin!r} has no exported conversion; "
                "enter a correction"
            )
        try:
            parse_dsl(zamanalif_dsl)
        except DslError as exc:
            raise LabelStudioImportError(
                f"{context} selected origin {origin!r} has invalid exported "
                f"Zamanalif DSL: {exc}"
            ) from exc
    else:
        raise LabelStudioImportError(
            f"{context} must contain exactly one {CONVERSION_CONTROL!r} result"
        )
    return _AnnotationDecision(
        is_homonym=False,
        origin=origin,
        zamanalif_dsl=zamanalif_dsl,
    )


def _is_empty_conversion(
    result: dict[str, Any],
    result_index: int,
    *,
    context: str,
) -> bool:
    if result.get("type") != "textarea":
        raise LabelStudioImportError(
            f"{context} result {result_index} conversion type must be 'textarea'"
        )
    value = result.get("value")
    texts = value.get("text") if isinstance(value, dict) else None
    return texts == [] or texts == [""]


def _parse_homonym(
    result: dict[str, Any],
    result_index: int,
    *,
    context: str,
) -> None:
    if result.get("type") != "choices":
        raise LabelStudioImportError(
            f"{context} result {result_index} homonym type must be 'choices'"
        )
    value = result.get("value")
    choices = value.get("choices") if isinstance(value, dict) else None
    if choices != [HOMONYM_CHOICE]:
        raise LabelStudioImportError(
            f"{context} result {result_index} has invalid homonym choice"
        )


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
    if origin not in REVIEWED_ORIGINS:
        raise LabelStudioImportError(
            f"{context} result {result_index} has invalid origin: {origin!r}"
        )
    return origin


def _parse_conversion(
    result: dict[str, Any],
    context: str,
    result_index: int,
    suggested_zamanalif: str,
) -> str:
    if result.get("type") != "textarea":
        raise LabelStudioImportError(
            f"{context} result {result_index} conversion type must be 'textarea'"
        )
    value = result.get("value")
    texts = value.get("text") if isinstance(value, dict) else None
    if (
        not isinstance(texts, list)
        or not texts
        or any(not isinstance(text, str) or not text for text in texts)
    ):
        raise LabelStudioImportError(
            f"{context} result {result_index} has invalid conversion text"
        )
    if len(texts) > 1 and texts[:-1] != [suggested_zamanalif]:
        raise LabelStudioImportError(
            f"{context} result {result_index} has invalid conversion history"
        )
    zamanalif_dsl = texts[-1]
    try:
        parse_dsl(zamanalif_dsl)
    except DslError as exc:
        raise LabelStudioImportError(
            f"{context} result {result_index} has invalid Zamanalif DSL: {exc}"
        ) from exc
    return zamanalif_dsl


def _validate_contextual_source(
    conn: sqlite3.Connection,
    item: ReviewedAnnotation,
    effective_homonyms: set[str],
) -> None:
    if item.sample_id is None or item.token_index is None:
        raise LabelStudioImportError("contextual annotation lacks occurrence identity")
    row = conn.execute(
        """
        select p.tokens_json
        from preannotation_state p
        where p.sample_id = ?
          and p.status = 'annotated'
          and p.tatar = 1
        """,
        (item.sample_id,),
    ).fetchone()
    if row is None:
        raise LabelStudioImportError(
            f"contextual sample is missing or not eligible: {item.sample_id!r}"
        )
    try:
        tokens = json.loads(row[0])
    except json.JSONDecodeError as exc:
        raise LabelStudioImportError(
            f"{item.sample_id}: invalid tokens_json in database: {exc}"
        ) from exc
    if not isinstance(tokens, list) or item.token_index >= len(tokens):
        raise LabelStudioImportError(
            f"{item.sample_id}: token index is stale: {item.token_index}"
        )
    token = tokens[item.token_index]
    text = token.get("text") if isinstance(token, dict) else None
    normalized = normalize_word(text) if isinstance(text, str) else ""
    if normalized != item.normalized_word:
        raise LabelStudioImportError(
            f"{item.sample_id}: contextual token changed at index {item.token_index}"
        )
    if normalized not in effective_homonyms:
        raise LabelStudioImportError(
            f"{item.sample_id}: word is no longer contextual: {normalized!r}"
        )
    if conversion_branches(normalized).state == "origin_independent":
        raise LabelStudioImportError(
            f"{item.sample_id}: word no longer requires contextual review: "
            f"{normalized!r}"
        )
    origin, zamanalif_dsl = _regular_values(item)
    try:
        validate_contextual_review(
            item.normalized_word,
            zamanalif_dsl,
            origin,
        )
    except ContextualReviewError as exc:
        raise LabelStudioImportError(str(exc)) from exc
