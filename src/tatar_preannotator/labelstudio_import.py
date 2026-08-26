from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
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
    FOCUSED_DICTIONARY_TASK_SCHEMA_VERSION,
    FOCUSED_DICTIONARY_DATA_FIELDS,
    LEGACY_FOCUSED_DICTIONARY_TASK_SCHEMA_VERSION,
    ReviewedVariant,
    TASK_SCHEMA_VERSION,
    annotation_display_variants,
    annotation_variants,
    classify_project,
    conversion_branches,
    dictionary_project_keys,
    eligible_project_words,
    ensure_review_state_schema,
    is_safe_family_member,
    native_hamza_family,
    normalize_word,
)


ORIGIN_CONTROL = "reviewed_origin"
CONVERSION_CONTROL = "corrected_zamanalif"
VARIANTS_CONTROL = "reviewed_zamanalif_variants"
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
    variants: tuple[ReviewedVariant, ...] = ()


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
    suggested_variants: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AnnotationDecision:
    is_homonym: bool
    origin: str | None = None
    zamanalif_dsl: str | None = None
    variants: tuple[ReviewedVariant, ...] = ()


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
                project_eligible = eligible_project_words(conn, parsed.project_key)
                existing = {
                    str(row[0]): (str(row[1]), str(row[2]))
                    for row in conn.execute(
                        """
                        select normalized_word, zamanalif_dsl, origin
                        from reviewed_words
                        """
                    ).fetchall()
                }
                existing_variants = _load_stored_variants(conn)
                derived_words = {
                    str(row[0])
                    for row in conn.execute(
                        "select normalized_word from reviewed_word_derivations"
                    ).fetchall()
                }
                analysis_words = (
                    set(project_eligible)
                    | (set(existing) - derived_words)
                    | regular_words
                )
                analyses = analyzer.analyze(sorted(analysis_words))
                if parsed.project_key != "hamza":
                    regular_items = [
                        _reconstruct_imported_family(
                            item,
                            analyses,
                            project_eligible,
                            analyzer,
                            parsed.project_key,
                        )
                        for item in regular_items
                    ]
                elif parsed.project_key == "hamza":
                    regular_items = [
                        _reconstruct_imported_hamza_family(
                            item,
                            project_eligible,
                        )
                        for item in regular_items
                    ]
                for item in regular_items:
                    origin, zamanalif_dsl = _regular_values(item)
                    current = (zamanalif_dsl, origin)
                    previous = existing.get(item.normalized_word)
                    if previous is not None:
                        if previous != current or existing_variants.get(
                            item.normalized_word, ()
                        ) != item.variants:
                            raise LabelStudioImportError(
                                f"reviewed word conflict for {item.normalized_word!r}: "
                                "database and import decisions differ"
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
                    _replace_stored_variants(
                        conn,
                        item.normalized_word,
                        item.variants,
                        now,
                    )
                    existing_variants[item.normalized_word] = item.variants
                    imported += 1
                for item in regular_items:
                    if parsed.project_key != "hamza":
                        inherited += _propagate_imported_family(
                            conn,
                            item,
                            analyses,
                            analyzer.revision,
                            parsed.project_key,
                            existing,
                            existing_variants,
                            derived_words,
                            now,
                        )
                    elif parsed.project_key == "hamza":
                        inherited += _propagate_imported_hamza_family(
                            conn,
                            item,
                            existing,
                            derived_words,
                            now,
                        )
                if parsed.project_key != "hamza":
                    inherited += _backfill_reviewed_families(
                        conn,
                        analyses,
                        analyzer.revision,
                        project_eligible,
                        parsed.project_key,
                        existing,
                        existing_variants,
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


def _load_stored_variants(
    conn: sqlite3.Connection,
) -> dict[str, tuple[ReviewedVariant, ...]]:
    grouped: dict[str, list[ReviewedVariant]] = {}
    for word, _, zamanalif, policies_json in conn.execute(
        """
        select normalized_word, position, zamanalif, policies_json
        from reviewed_word_variants
        order by normalized_word, position
        """
    ).fetchall():
        raw_policies = json.loads(str(policies_json))
        policies = tuple(
            tuple((str(rule), str(option)) for rule, option in policy.items())
            for policy in raw_policies
        )
        grouped.setdefault(str(word), []).append(
            ReviewedVariant(str(zamanalif), policies)
        )
    return {word: tuple(variants) for word, variants in grouped.items()}


def _replace_stored_variants(
    conn: sqlite3.Connection,
    word: str,
    variants: tuple[ReviewedVariant, ...],
    now: str,
) -> None:
    conn.execute(
        "delete from reviewed_word_variants where normalized_word = ?",
        (word,),
    )
    conn.executemany(
        """
        insert into reviewed_word_variants(
            normalized_word, position, zamanalif, policies_json, updated_at
        ) values (?, ?, ?, ?, ?)
        """,
        [
            (
                word,
                position,
                variant.zamanalif,
                json.dumps(
                    [dict(policy) for policy in variant.policies],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                now,
            )
            for position, variant in enumerate(variants)
        ],
    )


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
    conn.executemany(
        "delete from reviewed_word_variants where normalized_word = ?",
        [(item,) for item in (word, *derived_members)],
    )
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
    project_key: str,
) -> ReviewedAnnotation:
    candidate = eligible.get(item.normalized_word)
    if candidate is None:
        raise LabelStudioImportError(
            f"{project_key} word is no longer eligible: {item.normalized_word!r}"
        )
    if candidate.origin != item.suggested_origin:
        raise LabelStudioImportError(
            f"{project_key} word changed predicted origin: "
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


def _reconstruct_imported_hamza_family(
    item: ReviewedAnnotation,
    eligible: dict[str, Any],
) -> ReviewedAnnotation:
    candidate = eligible.get(item.normalized_word)
    if candidate is None:
        raise LabelStudioImportError(
            f"hamza word is no longer eligible: {item.normalized_word!r}"
        )
    if candidate.origin != item.suggested_origin:
        raise LabelStudioImportError(
            f"hamza word changed predicted origin: {item.normalized_word!r}"
        )
    family = native_hamza_family(item.normalized_word)
    if family is None:
        raise LabelStudioImportError(
            f"hamza word has no verified lexical family: {item.normalized_word!r}"
        )
    related = sorted(
        (
            word
            for word, related_candidate in eligible.items()
            if word != item.normalized_word
            and related_candidate.origin == item.suggested_origin
            and native_hamza_family(word) == family
        ),
        key=lambda word: (len(word), word),
    )
    return replace(
        item,
        family_members=(item.normalized_word, *related),
        morphology=MorphIdentity(family, "hamza"),
        analyzer_revision="lexical-hamza-v1",
    )


def _propagate_imported_family(
    conn: sqlite3.Connection,
    item: ReviewedAnnotation,
    analyses: dict[str, MorphIdentity | None],
    analyzer_revision: str,
    project_key: str,
    existing: dict[str, tuple[str, str]],
    existing_variants: dict[str, tuple[ReviewedVariant, ...]],
    derived_words: set[str],
    now: str,
) -> int:
    identity = item.morphology
    if identity is None or len(item.family_members) == 1:
        return 0
    origin, zamanalif_dsl = _regular_values(item)
    canonical = conversion_branches(item.normalized_word).suggestion(origin)
    inserted = 0
    for word in item.family_members[1:]:
        if not is_safe_family_member(
            item.normalized_word,
            word,
            identity.lemma,
        ):
            raise LabelStudioImportError(
                f"{project_key} family member is not safely covered: {word!r}"
            )
        if analyses.get(word) != identity:
            raise LabelStudioImportError(
                f"{project_key} family morphology changed for {word!r}"
            )
        if classify_project(word, origin)["key"] != project_key:
            continue
        member_canonical = conversion_branches(word).suggestion(origin)
        collapsed = _collapsed_family_member_variant(
            canonical,
            zamanalif_dsl,
            item.variants,
            member_canonical,
        )
        if collapsed is not None:
            member_zamanalif = collapsed.zamanalif
            member_variants = (collapsed,)
        else:
            member_zamanalif = _family_member_zamanalif(
                canonical,
                zamanalif_dsl,
                member_canonical,
            )
            if not member_zamanalif:
                continue
            member_variants = _family_member_variants(
                canonical,
                item.variants,
                member_canonical,
            )
        if item.variants and not member_variants:
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
            variants=member_variants,
            existing_variants=existing_variants,
            derived_words=derived_words,
            now=now,
        )
    return inserted


def _propagate_imported_hamza_family(
    conn: sqlite3.Connection,
    item: ReviewedAnnotation,
    existing: dict[str, tuple[str, str]],
    derived_words: set[str],
    now: str,
) -> int:
    identity = item.morphology
    if identity is None or len(item.family_members) == 1:
        return 0
    origin, zamanalif_dsl = _regular_values(item)
    canonical = conversion_branches(item.normalized_word).suggestion(origin)
    inserted = 0
    for word in item.family_members[1:]:
        if native_hamza_family(word) != identity.lemma:
            raise LabelStudioImportError(
                f"hamza family changed for {word!r}"
            )
        if classify_project(word, origin)["key"] != "hamza":
            raise LabelStudioImportError(
                f"hamza family member changed project for {word!r}"
            )
        member_canonical = conversion_branches(word).suggestion(origin)
        member_zamanalif = _family_member_zamanalif(
            canonical,
            zamanalif_dsl,
            member_canonical,
        )
        if not member_zamanalif:
            continue
        inserted += _store_inherited_review(
            conn,
            word=word,
            zamanalif_dsl=member_zamanalif,
            origin=origin,
            source_word=item.normalized_word,
            identity=identity,
            analyzer_revision=item.analyzer_revision or "lexical-hamza-v1",
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
    project_key: str,
    existing: dict[str, tuple[str, str]],
    existing_variants: dict[str, tuple[ReviewedVariant, ...]],
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
            or classify_project(word, origin)["key"] != project_key
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
            if is_safe_family_member(source, word, identity.lemma)
        ]
        if not sources or classify_project(word, candidate.origin)["key"] != project_key:
            continue
        source = min(
            sources,
            key=lambda value: (
                value != identity.lemma,
                len(value),
                value,
            ),
        )
        member_canonical = conversion_branches(word).suggestion(candidate.origin)
        source_canonical = conversion_branches(source).suggestion(candidate.origin)
        source_variants = existing_variants.get(source, ())
        collapsed = _collapsed_family_member_variant(
            source_canonical,
            existing[source][0],
            source_variants,
            member_canonical,
        )
        if collapsed is not None:
            zamanalif_dsl = collapsed.zamanalif
            member_variants = (collapsed,)
        else:
            zamanalif_dsl = _family_member_zamanalif(
                source_canonical,
                existing[source][0],
                member_canonical,
            )
            if not zamanalif_dsl:
                continue
            member_variants = _family_member_variants(
                source_canonical,
                source_variants,
                member_canonical,
            )
        if source_variants and not member_variants:
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
            variants=member_variants,
            existing_variants=existing_variants,
            derived_words=derived_words,
            now=now,
        )
    return inserted


def _family_member_zamanalif(
    source_canonical: str,
    source_reviewed: str,
    member_canonical: str,
) -> str:
    """Transfer only manual edits contained in a family's shared Latin prefix."""
    if not source_canonical or not member_canonical:
        return ""
    if source_reviewed == source_canonical:
        return member_canonical
    if any("{{" in value for value in (source_canonical, source_reviewed, member_canonical)):
        return ""

    common_length = 0
    for source_char, member_char in zip(
        source_canonical,
        member_canonical,
        strict=False,
    ):
        if source_char != member_char:
            break
        common_length += 1
    if common_length == 0:
        return ""

    patches: list[tuple[int, int, str]] = []
    for tag, source_start, source_end, reviewed_start, reviewed_end in SequenceMatcher(
        None,
        source_canonical,
        source_reviewed,
        autojunk=False,
    ).get_opcodes():
        if tag == "equal":
            continue
        if source_start == source_end:
            if source_start >= common_length:
                continue
        elif source_end > common_length:
            continue
        patches.append(
            (source_start, source_end, source_reviewed[reviewed_start:reviewed_end])
        )
    if not patches:
        return ""

    result = member_canonical
    for start, end, replacement in reversed(patches):
        result = result[:start] + replacement + result[end:]
    try:
        parse_dsl(result)
    except DslError:
        return ""
    return result


def _family_member_variants(
    source_canonical_dsl: str,
    source_reviewed: tuple[ReviewedVariant, ...],
    member_canonical_dsl: str,
) -> tuple[ReviewedVariant, ...]:
    """Transfer focused-project edits for every matching policy rendering."""
    if not source_reviewed:
        return ()
    source_canonical = annotation_variants(source_canonical_dsl)
    member_canonical = annotation_variants(member_canonical_dsl)
    source_by_policy: dict[tuple[tuple[str, str], ...], tuple[str, str]] = {}
    for canonical, reviewed in zip(source_canonical, source_reviewed, strict=True):
        for policy in canonical.policies:
            source_by_policy[policy] = (canonical.zamanalif, reviewed.zamanalif)

    transferred: list[ReviewedVariant] = []
    for member in member_canonical:
        values: set[str] = set()
        for policy in member.policies:
            source = source_by_policy.get(policy)
            if source is None:
                return ()
            value = _family_member_zamanalif(
                source[0],
                source[1],
                member.zamanalif,
            )
            if not value:
                return ()
            values.add(value)
        if len(values) != 1:
            return ()
        transferred.append(ReviewedVariant(values.pop(), member.policies))
    return tuple(transferred)


def _collapsed_family_member_variant(
    source_canonical_dsl: str,
    source_reviewed_dsl: str,
    source_variants: tuple[ReviewedVariant, ...],
    member_canonical_dsl: str,
) -> ReviewedVariant | None:
    """Transfer a one-variant lexical override to a safe family member."""
    if (
        len(source_variants) != 1
        or source_reviewed_dsl != source_variants[0].zamanalif
        or not parse_dsl(source_canonical_dsl).has_choices
    ):
        return None
    selected = source_variants[0]
    source_options = annotation_variants(source_canonical_dsl)
    source_option = next(
        (
            option
            for option in source_options
            if set(option.policies) & set(selected.policies)
        ),
        None,
    )
    if source_option is None:
        return None
    member_options = annotation_variants(member_canonical_dsl)
    member_option = next(
        (
            option
            for option in member_options
            if set(option.policies) & set(selected.policies)
        ),
        None,
    )
    if member_option is None:
        return None
    value = _family_member_zamanalif(
        source_option.zamanalif,
        selected.zamanalif,
        member_option.zamanalif,
    )
    return ReviewedVariant(value, selected.policies) if value else None


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
    variants: tuple[ReviewedVariant, ...] = (),
    existing_variants: dict[str, tuple[ReviewedVariant, ...]] | None = None,
) -> int:
    current = (zamanalif_dsl, origin)
    previous = existing.get(word)
    if previous is not None:
        variants_changed = (
            existing_variants is not None
            and existing_variants.get(word, ()) != variants
        )
        if (previous != current or variants_changed) and word in derived_words:
            conn.execute(
                """
                update reviewed_words
                set zamanalif_dsl = ?, origin = ?, updated_at = ?
                where normalized_word = ?
                """,
                (zamanalif_dsl, origin, now, word),
            )
            conn.execute(
                """
                update reviewed_word_derivations
                set source_word = ?, lemma = ?, part_of_speech = ?,
                    analyzer_revision = ?, created_at = ?
                where normalized_word = ?
                """,
                (
                    source_word,
                    identity.lemma,
                    identity.part_of_speech,
                    analyzer_revision,
                    now,
                    word,
                ),
            )
            _replace_stored_variants(conn, word, variants, now)
            existing[word] = current
            if existing_variants is not None:
                existing_variants[word] = variants
            return 1
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
    _replace_stored_variants(conn, word, variants, now)
    existing[word] = current
    if existing_variants is not None:
        existing_variants[word] = variants
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
            reviewed_variants = tuple(
                variant.zamanalif for variant in reviewed.variants
            )
            conversion_changed = (
                reviewed_variants != parsed.suggested_variants
                if parsed.suggested_variants
                else zamanalif_dsl != parsed.suggested_zamanalif
            )
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
                        suggested_zamanalif=(
                            "\n".join(parsed.suggested_variants)
                            if parsed.suggested_variants
                            else parsed.suggested_zamanalif
                        ),
                        reviewed_zamanalif=(
                            "\n".join(reviewed_variants)
                            if reviewed_variants
                            else zamanalif_dsl
                        ),
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
    meta = task.get("meta")
    hidden_suggestion: str | None = None
    exported_variants: tuple[ReviewedVariant, ...] = ()
    is_variant_schema = False
    if project_key == CONTEXTUAL_PROJECT_KEY:
        expected_data_fields = CONTEXTUAL_DATA_FIELDS
        expected_meta_fields = CONTEXTUAL_META_FIELDS
        expected_schema_version = TASK_SCHEMA_VERSION
    elif project_key == "catchall":
        expected_data_fields = DICTIONARY_DATA_FIELDS
        expected_meta_fields = CATCHALL_META_FIELDS
        expected_schema_version = CATCHALL_TASK_SCHEMA_VERSION
    else:
        schema_version = meta.get("schema_version") if isinstance(meta, dict) else None
        if schema_version == TASK_SCHEMA_VERSION:
            expected_data_fields = DICTIONARY_DATA_FIELDS
            expected_meta_fields = frozenset({"schema_version", "project_key"})
            expected_schema_version = TASK_SCHEMA_VERSION
        elif schema_version == LEGACY_FOCUSED_DICTIONARY_TASK_SCHEMA_VERSION:
            expected_data_fields = DICTIONARY_DATA_FIELDS
            expected_meta_fields = frozenset(
                {"schema_version", "project_key", "suggested_zamanalif_dsl"}
            )
            expected_schema_version = LEGACY_FOCUSED_DICTIONARY_TASK_SCHEMA_VERSION
        else:
            expected_data_fields = FOCUSED_DICTIONARY_DATA_FIELDS
            expected_meta_fields = DICTIONARY_META_FIELDS
            expected_schema_version = FOCUSED_DICTIONARY_TASK_SCHEMA_VERSION
            is_variant_schema = True
    if set(data) != expected_data_fields:
        raise LabelStudioImportError(
            f"{context}.data contains unexpected or missing fields"
        )
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
    if is_variant_schema:
        suggested_variants_text = data.get("zamanalif_variants")
        if not isinstance(suggested_variants_text, str):
            raise LabelStudioImportError(
                f"{context} has invalid data.zamanalif_variants"
            )
        hidden_suggestion = meta.get("suggested_zamanalif_dsl")
        if not isinstance(hidden_suggestion, str):
            raise LabelStudioImportError(
                f"{context} has invalid meta.suggested_zamanalif_dsl"
            )
        try:
            canonical_variants = annotation_variants(hidden_suggestion)
        except DslError as exc:
            raise LabelStudioImportError(
                f"{context} has invalid hidden Zamanalif DSL: {exc}"
            ) from exc
        expected_visible = "\n".join(
            variant.zamanalif for variant in canonical_variants
        )
        if suggested_variants_text != expected_visible:
            raise LabelStudioImportError(
                f"{context} visible variants do not match hidden Zamanalif DSL"
            )
        raw_policies = meta.get("variant_policies")
        expected_policies = [
            [dict(policy) for policy in variant.policies]
            for variant in canonical_variants
        ]
        if raw_policies != expected_policies:
            raise LabelStudioImportError(f"{context} has invalid variant policies")
        exported_variants = tuple(
            ReviewedVariant(variant.zamanalif, variant.policies)
            for variant in canonical_variants
        )
        suggested_zamanalif = hidden_suggestion
    else:
        suggested_zamanalif = data.get("auto_zamanalif")
        if not isinstance(suggested_zamanalif, str):
            raise LabelStudioImportError(f"{context} has invalid data.auto_zamanalif")
        if (
            project_key not in {CONTEXTUAL_PROJECT_KEY, "catchall"}
            and expected_schema_version
            == LEGACY_FOCUSED_DICTIONARY_TASK_SCHEMA_VERSION
        ):
            hidden_suggestion = meta.get("suggested_zamanalif_dsl")
            if not isinstance(hidden_suggestion, str):
                raise LabelStudioImportError(
                    f"{context} has invalid meta.suggested_zamanalif_dsl"
                )
            try:
                variants = annotation_display_variants(hidden_suggestion)
            except DslError as exc:
                raise LabelStudioImportError(
                    f"{context} has invalid hidden Zamanalif DSL: {exc}"
                ) from exc
            expected_visible = variants[0] if variants else ""
            if suggested_zamanalif != expected_visible:
                raise LabelStudioImportError(
                    f"{context} visible suggestion does not match hidden Zamanalif DSL"
                )

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
                hidden_suggestion or suggested_zamanalif,
                exported_variants,
                is_variant_schema,
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
        variants=decision.variants,
    )
    return _ParsedTask(
        reviewed=reviewed,
        task_id=str(task.get("id", task_index)),
        word=surface,
        suggested_origin=suggested_origin,
        suggested_zamanalif=hidden_suggestion or suggested_zamanalif,
        suggested_variants=tuple(
            variant.zamanalif for variant in exported_variants
        ),
    )


def _parse_result(
    results: list[Any],
    task_context: str,
    annotation_index: int,
    suggested_zamanalif: str,
    preserved_suggestion_dsl: str,
    exported_variants: tuple[ReviewedVariant, ...],
    variant_schema: bool,
    *,
    allow_homonym: bool,
    require_origin: bool,
    contextual_zamanalif: dict[str, str] | None,
) -> _AnnotationDecision:
    context = f"{task_context} annotation {annotation_index}"
    origins: list[tuple[dict[str, Any], int]] = []
    conversions: list[tuple[dict[str, Any], int]] = []
    variant_conversions: list[tuple[dict[str, Any], int]] = []
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
        elif control == VARIANTS_CONTROL:
            variant_conversions.append((result, result_index))
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
        if len(origins) > 1 or len(conversions) > 1 or len(variant_conversions) > 1:
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
    if variant_schema and (len(variant_conversions) != 1 or conversions):
        raise LabelStudioImportError(
            f"{context} must contain exactly one {VARIANTS_CONTROL!r} result"
        )
    if not require_origin and not variant_schema and len(conversions) != 1:
        raise LabelStudioImportError(
            f"{context} must contain exactly one {CONVERSION_CONTROL!r} result"
        )
    origin = None
    if require_origin:
        origin_result, origin_index = origins[0]
        origin = _parse_origin(origin_result, context, origin_index)
    reviewed_variants: tuple[ReviewedVariant, ...] = ()
    if variant_schema:
        reviewed_variants = _parse_variants_conversion(
            *variant_conversions[0],
            context=context,
            exported_variants=exported_variants,
        )
        zamanalif_dsl = (
            reviewed_variants[0].zamanalif
            if len(reviewed_variants) < len(exported_variants)
            else preserved_suggestion_dsl or reviewed_variants[0].zamanalif
        )
    elif conversions and not _is_empty_conversion(*conversions[0], context=context):
        conversion_result, conversion_index = conversions[0]
        zamanalif_dsl = _parse_conversion(
            conversion_result,
            context,
            conversion_index,
            suggested_zamanalif,
            preserved_suggestion_dsl,
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
        variants=reviewed_variants,
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
    preserved_suggestion_dsl: str,
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
    zamanalif_dsl = (
        preserved_suggestion_dsl
        if texts[-1] == suggested_zamanalif
        else texts[-1]
    )
    try:
        parse_dsl(zamanalif_dsl)
    except DslError as exc:
        raise LabelStudioImportError(
            f"{context} result {result_index} has invalid Zamanalif DSL: {exc}"
        ) from exc
    return zamanalif_dsl


def _parse_variants_conversion(
    result: dict[str, Any],
    result_index: int,
    *,
    context: str,
    exported_variants: tuple[ReviewedVariant, ...],
) -> tuple[ReviewedVariant, ...]:
    if result.get("type") != "textarea":
        raise LabelStudioImportError(
            f"{context} result {result_index} variants type must be 'textarea'"
        )
    value = result.get("value")
    texts = value.get("text") if isinstance(value, dict) else None
    if not isinstance(texts, list) or not texts or any(
        not isinstance(text, str) for text in texts
    ):
        raise LabelStudioImportError(
            f"{context} result {result_index} has invalid variant text"
        )
    original = "\n".join(variant.zamanalif for variant in exported_variants)
    if len(texts) > 1 and texts[:-1] != [original]:
        raise LabelStudioImportError(
            f"{context} result {result_index} has invalid variant history"
        )
    lines = tuple(line.strip() for line in texts[-1].splitlines())
    expected_line_count = len(exported_variants) or 1
    if len(lines) != expected_line_count or any(not line for line in lines):
        raise LabelStudioImportError(
            f"{context} result {result_index} must keep exactly "
            f"{expected_line_count} non-empty variant lines"
        )
    retained_count = sum(line != "-" for line in lines)
    if retained_count == 0:
        raise LabelStudioImportError(
            f"{context} result {result_index} must retain at least one variant"
        )
    if retained_count != len(lines) and retained_count != 1:
        raise LabelStudioImportError(
            f"{context} result {result_index} must retain exactly one variant "
            "when rejecting alternatives"
        )
    reviewed: list[ReviewedVariant] = []
    policy_sources = exported_variants or (ReviewedVariant("", ((),)),)
    for line, exported in zip(lines, policy_sources, strict=True):
        if line == "-":
            continue
        try:
            parsed = parse_dsl(line)
        except DslError as exc:
            raise LabelStudioImportError(
                f"{context} result {result_index} has invalid Zamanalif variant: {exc}"
            ) from exc
        if parsed.has_choices:
            raise LabelStudioImportError(
                f"{context} result {result_index} variant contains hidden DSL"
            )
        reviewed.append(ReviewedVariant(line, exported.policies))
    return tuple(reviewed)


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
