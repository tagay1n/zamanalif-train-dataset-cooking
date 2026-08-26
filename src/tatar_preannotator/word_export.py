from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from itertools import product
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Iterable

from tatar_preannotator.labelstudio_instructions import render_project_instructions
from tatar_preannotator.morphology import (
    MorphologyAnalyzer,
    default_morphology_analyzer,
)
from tatar_preannotator.conversion import (
    APOSTROPHE_VARIANTS,
    Choice,
    ConversionResult,
    DslError,
    E_GLIDE_RULE,
    FIGYL_STEM_RULE,
    HAMZA_RULE,
    IJTIMAGIY_STEM_RULE,
    IYA_RULE,
    KAGAZ_STEM_RULE,
    Literal,
    RULES,
    MASHGUL_STEM_RULE,
    RL_Y_RULE,
    MONTH_NAME_RULE,
    MOSTAQIL_RULE,
    NATIVE_UW_RULE,
    RL_FINAL_KA_RULE,
    RUS_JOTATION_RULE,
    RUS_SIGN_E_RULE,
    RUS_SOFT_SIGN_O_RULE,
    RUS_SIGN_RULE,
    SHIGYR_STEM_RULE,
    TS_RULE,
    YA_RULE,
    ZAMANALIF_APOSTROPHE,
    normalize_zamanalif_apostrophes,
    parse_dsl,
)
from zamanalif_selector.features import BACK_VOWELS, CONDITIONAL_LETTERS, FRONT_VOWELS

if TYPE_CHECKING:
    from .contextual_review import ContextualExportResult, OccurrenceKey

LABELSTUDIO_SPLIT_BATCH_SIZE = 500
TASK_SCHEMA_VERSION = 1
LEGACY_FOCUSED_DICTIONARY_TASK_SCHEMA_VERSION = 2
FOCUSED_DICTIONARY_TASK_SCHEMA_VERSION = 3
CATCHALL_TASK_SCHEMA_VERSION = 3
DICTIONARY_DATA_FIELDS = frozenset(
    {"cyrl_word", "auto_zamanalif", "gemini_origin", "hints_html"}
)
FOCUSED_DICTIONARY_DATA_FIELDS = frozenset(
    {"cyrl_word", "zamanalif_variants", "gemini_origin", "hints_html"}
)
DICTIONARY_META_FIELDS = frozenset(
    {
        "schema_version",
        "project_key",
        "suggested_zamanalif_dsl",
        "variant_policies",
    }
)
CATCHALL_META_FIELDS = frozenset({"schema_version", "project_key"})
CONTEXTUAL_DATA_FIELDS = frozenset(
    {
        "cyrl_word",
        "sentence",
        "context_html",
        "gemini_origin",
        "auto_zamanalif",
        "native_zamanalif",
        "loanword_zamanalif",
        "hints_html",
    }
)
CONTEXTUAL_META_FIELDS = frozenset(
    {"schema_version", "project_key", "sample_id", "token_index"}
)
MANAGED_BATCH_RE = re.compile(
    r"^project_[a-z0-9_]+(?:_batch_\d{3}_of_\d{3})?\.json$"
)
MANAGED_INSTRUCTIONS_RE = re.compile(r"^project_[a-z0-9_]+_instructions\.html$")
CYRILLIC_RE = re.compile(r"[А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]")
INTERNAL_DOUBLE_QUOTES = frozenset('"“”„‟«»〝〞〟＂')
TATAR_SUFFIXES_AFTER_QUOTE = frozenset(
    {
        "гы",
        "ге",
        "кы",
        "ке",
        "да",
        "дә",
        "та",
        "тә",
        "дагы",
        "дәге",
        "тагы",
        "тәге",
        "дан",
        "дән",
        "тан",
        "тән",
        "га",
        "гә",
        "ка",
        "кә",
        "ны",
        "не",
        "ның",
        "нең",
        "н",
        "на",
        "нә",
        "нда",
        "ндә",
        "ннан",
        "ннән",
        "ы",
        "е",
        "сы",
        "се",
        "ында",
        "ендә",
        "ыннан",
        "еннән",
        "лар",
        "ләр",
        "лары",
        "ләре",
        "ларның",
        "ләрнең",
        "ларында",
        "ләрендә",
        "лыкка",
        "леккә",
        "лыкны",
        "лекне",
        "лыкның",
        "лекнең",
    }
)
TATAR_SPECIFIC_PART_LETTERS = frozenset("әөүҗңһ")
UNKNOWN_LOANWORD_MARKER_LETTERS = frozenset("ёцщьъ")
UNKNOWN_LOANWORD_PREFIXES = (
    "авиа",
    "авто",
    "агро",
    "аудио",
    "видео",
    "гео",
    "гидро",
    "кино",
    "макро",
    "микро",
    "радио",
    "теле",
    "фото",
    "электро",
)
RL_REVIEW_LETTERS = frozenset("ёыьъщ")
FAMILY_DIVERGENCE_RISK_LETTERS = frozenset(CONDITIONAL_LETTERS) | RL_REVIEW_LETTERS
ALLOWED_ZAMANALIF = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "äÄöÖüÜñÑıİğĞşŞçÇ"
    f"-—{ZAMANALIF_APOSTROPHE}()"
)
@dataclass
class WordStats:
    normalized: str
    display: str
    label_counts: Counter[str] = field(default_factory=Counter)
    frequency: int = 0
    conditional_letters: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        if self.label_counts["U"]:
            return "U"
        if self.label_counts["RL"]:
            return "RL"
        return "N"


@dataclass(frozen=True)
class ExportResult:
    tasks: list[dict[str, Any]]
    report: dict[str, Any]
    exported_words: list[str]
    frequencies: tuple[int, ...] = ()


@dataclass(frozen=True)
class SplitExportResult:
    projects: dict[str, ExportResult]
    report: dict[str, Any]
    exported_words: list[str]
    contextual_occurrences: tuple[OccurrenceKey, ...] = ()


@dataclass(frozen=True)
class ReviewedWord:
    normalized_word: str
    zamanalif_dsl: str
    origin: str
    variants: tuple[ReviewedVariant, ...] = ()


@dataclass(frozen=True)
class ReviewedVariant:
    zamanalif: str
    policies: tuple[tuple[tuple[str, str], ...], ...]


@dataclass(frozen=True)
class AnnotationVariant:
    zamanalif: str
    policies: tuple[tuple[tuple[str, str], ...], ...]


@dataclass(frozen=True)
class CatchallWord:
    normalized_word: str
    origin: str
    frequency: int


@dataclass(frozen=True)
class _ExportUnit:
    task: dict[str, Any]
    normalized_word: str
    project_key: str
    frequency: int
    family_members: tuple[str, ...]


@dataclass(frozen=True)
class ConversionBranches:
    """Canonical native and loanword outputs for one normalized word."""

    native_dsl: str
    loanword_dsl: str

    @property
    def state(self) -> str:
        if self.native_dsl and self.loanword_dsl and self.native_dsl == self.loanword_dsl:
            return "origin_independent"
        if not self.native_dsl or not self.loanword_dsl:
            return "unconvertible"
        return "origin_dependent"

    def suggestion(self, label: str) -> str:
        if label == "N":
            return self.native_dsl
        if label == "RL":
            return self.loanword_dsl
        if self.state == "origin_independent":
            return self.native_dsl
        return ""


class AnnotationExportError(ValueError):
    """Raised when generated Label Studio tasks are internally inconsistent."""


def normalize_word(token: str) -> str:
    """Normalize a token for word-form deduplication."""
    matches = list(CYRILLIC_RE.finditer(token or ""))
    if not matches:
        return ""
    surface = token[matches[0].start() : matches[-1].end()]
    cleaned: list[str] = []
    for index, char in enumerate(surface):
        if (
            char in INTERNAL_DOUBLE_QUOTES
            and index > 0
            and index + 1 < len(surface)
            and CYRILLIC_RE.fullmatch(surface[index - 1])
            and CYRILLIC_RE.fullmatch(surface[index + 1])
            and surface[index + 1 :].casefold() in TATAR_SUFFIXES_AFTER_QUOTE
        ):
            continue
        cleaned.append(char)
    normalized = "".join(cleaned)
    if any(
        char in INTERNAL_DOUBLE_QUOTES
        and index > 0
        and index + 1 < len(normalized)
        and CYRILLIC_RE.fullmatch(normalized[index - 1])
        and CYRILLIC_RE.fullmatch(normalized[index + 1])
        for index, char in enumerate(normalized)
    ):
        return ""
    return normalized.casefold()


def contains_conditional_letter(word: str) -> bool:
    """Return true when a normalized word contains a conditional Cyrillic letter."""
    return any(char in CONDITIONAL_LETTERS for char in word)


def contains_rl_review_letter(word: str) -> bool:
    """Return true when a loanword has a non-deterministic review letter."""
    return any(char in CONDITIONAL_LETTERS or char in RL_REVIEW_LETTERS for char in word)


def guess_unknown_tatar_specific_origin(word: str) -> str:
    """Conservatively guess an origin branch for an unresolved Tatar-looking word."""
    folded = word.casefold()
    if any(char in UNKNOWN_LOANWORD_MARKER_LETTERS for char in folded):
        return "RL"
    if folded.startswith(UNKNOWN_LOANWORD_PREFIXES):
        return "RL"
    first_specific = next(
        (
            index
            for index, char in enumerate(folded)
            if char in TATAR_SPECIFIC_PART_LETTERS
        ),
        -1,
    )
    if first_specific >= 3 and vowel_harmony_class(folded) == "mixed_front_back":
        return "RL"
    return "N"


def annotation_suggestion(word: str, label: str) -> str:
    """Return the editable export suggestion, including focused unknown heuristics."""
    branches = conversion_branches(word)
    if label != "U" or _u_project_key(word) != "u_tatar_specific":
        return branches.suggestion(label)
    guessed_origin = guess_unknown_tatar_specific_origin(word)
    preferred = (
        branches.native_dsl if guessed_origin == "N" else branches.loanword_dsl
    )
    fallback = branches.loanword_dsl if guessed_origin == "N" else branches.native_dsl
    return preferred or fallback


def annotation_display_variants(
    zamanalif_dsl: str,
    *,
    limit: int = 3,
) -> tuple[str, ...]:
    """Resolve a DSL suggestion into a preferred plain word and alternatives."""
    if not zamanalif_dsl or limit < 1:
        return ()
    result = parse_dsl(zamanalif_dsl)
    preferred = result.resolve()
    variants = [preferred]
    choices: dict[str, tuple[str, ...]] = {}
    for segment in result.segments:
        if isinstance(segment, Choice) and segment.rule_id not in choices:
            choices[segment.rule_id] = tuple(option for option, _ in segment.options)
    for selected in product(*(choices.values())):
        policy = dict(zip(choices, selected, strict=True))
        try:
            candidate = result.resolve(policy)
        except DslError:
            continue
        if candidate not in variants:
            variants.append(candidate)
        if len(variants) >= limit:
            break
    return tuple(variants)


def annotation_variants(zamanalif_dsl: str) -> tuple[AnnotationVariant, ...]:
    """Return every distinct plain rendering and the policies that select it."""
    if not zamanalif_dsl:
        return ()
    result = parse_dsl(zamanalif_dsl)
    choices: dict[str, tuple[str, ...]] = {}
    for segment in result.segments:
        if isinstance(segment, Choice) and segment.rule_id not in choices:
            choices[segment.rule_id] = tuple(option for option, _ in segment.options)
    if not choices:
        return (AnnotationVariant(result.resolve(), ((),)),)

    grouped: dict[str, list[tuple[tuple[str, str], ...]]] = {}
    order: list[str] = []
    preferred = result.resolve()
    order.append(preferred)
    grouped[preferred] = []
    for selected in product(*(choices.values())):
        policy = tuple(zip(choices, selected, strict=True))
        try:
            rendered = result.resolve(dict(policy))
        except DslError:
            continue
        if rendered not in grouped:
            grouped[rendered] = []
            order.append(rendered)
        grouped[rendered].append(policy)
    return tuple(
        AnnotationVariant(zamanalif=value, policies=tuple(grouped[value]))
        for value in order
    )


def is_safe_family_member(
    representative: str,
    candidate: str,
    lemma: str,
) -> bool:
    """Return whether a non-longer family member is covered by this review."""
    if candidate == representative or len(candidate) > len(representative):
        return False
    if representative.startswith(candidate):
        return True
    if not representative.startswith(lemma) or not candidate.startswith(lemma):
        return False

    common_length = 0
    for source_char, candidate_char in zip(representative, candidate, strict=False):
        if source_char != candidate_char:
            break
        common_length += 1
    if common_length < len(lemma):
        return False

    divergent_suffix = candidate[common_length:]
    return bool(divergent_suffix) and all(
        CYRILLIC_RE.fullmatch(char)
        and char not in FAMILY_DIVERGENCE_RISK_LETTERS
        for char in divergent_suffix
    )


def requires_dictionary_review(word: str, label: str) -> bool:
    """Return whether a word needs Project 1 approval before dataset export."""
    del label
    return conversion_branches(word).state != "origin_independent"


def vowel_harmony_class(word: str) -> str:
    """Classify simple front/back vowel harmony for one word."""
    has_front = any(char in FRONT_VOWELS for char in word)
    has_back = any(char in BACK_VOWELS for char in word)
    if has_front and has_back:
        return "mixed_front_back"
    if has_front:
        return "front_only"
    if has_back:
        return "back_only"
    return "no_vowels"


def export_labelstudio_tasks_from_db(
    db_path: str | Path,
    *,
    max_items: int | None = None,
    include_rl: bool = True,
    include_unknown: bool = True,
    min_frequency: int = 1,
    sort_by: str = "frequency_desc",
    reviewed_words: set[str] | None = None,
    word_resolutions: dict[str, str] | None = None,
) -> ExportResult:
    """Build Label Studio word-review tasks from annotated SQLite rows."""
    if reviewed_words is None:
        reviewed_words = set(load_reviewed_words(db_path))
    if word_resolutions is None:
        from .conflict_resolver import load_word_resolutions

        word_resolutions = {
            word: resolution.decision
            for word, resolution in load_word_resolutions(db_path).items()
        }
    return _export_from_records(
        _sqlite_records(db_path),
        max_items=max_items,
        include_rl=include_rl,
        include_unknown=include_unknown,
        min_frequency=min_frequency,
        sort_by=sort_by,
        reviewed_words=reviewed_words,
        word_resolutions=word_resolutions,
    )


def export_labelstudio_project_tasks_from_db(
    db_path: str | Path,
    *,
    max_items: int | None = None,
    include_rl: bool = True,
    include_unknown: bool = True,
    min_frequency: int = 1,
    sort_by: str = "frequency_desc",
    reviewed_words: set[str] | None = None,
    word_resolutions: dict[str, str] | None = None,
    morphology_analyzer: MorphologyAnalyzer | None = None,
) -> SplitExportResult:
    """Build focused Label Studio word-review project tasks from SQLite rows."""
    base = export_labelstudio_tasks_from_db(
        db_path,
        max_items=None,
        include_rl=include_rl,
        include_unknown=include_unknown,
        min_frequency=min_frequency,
        sort_by=sort_by,
        reviewed_words=reviewed_words,
        word_resolutions=word_resolutions,
    )
    analyzer = morphology_analyzer or default_morphology_analyzer()
    return split_export_result(
        base,
        morphology_analyzer=analyzer,
        max_items=max_items,
        sort_by=sort_by,
    )


def split_export_result(
    result: ExportResult,
    *,
    morphology_analyzer: MorphologyAnalyzer,
    max_items: int | None,
    sort_by: str,
) -> SplitExportResult:
    """Split one export result into focused project buckets."""
    units = _export_units(result, morphology_analyzer)
    if sort_by == "frequency_desc":
        units.sort(key=lambda item: (-item.frequency, item.normalized_word))
    elif sort_by == "word":
        units.sort(key=lambda item: item.normalized_word)
    else:
        raise ValueError("--sort-by must be one of: frequency_desc, word")
    if max_items is not None:
        units = units[:max_items]

    grouped_tasks: dict[str, list[dict[str, Any]]] = {}
    grouped_words: dict[str, list[str]] = {}
    grouped_frequencies: dict[str, list[int]] = {}
    grouped_covered_words: dict[str, set[str]] = {}
    for unit in units:
        grouped_tasks.setdefault(unit.project_key, []).append(
            _task_with_project_meta(unit.task, unit.project_key)
        )
        grouped_words.setdefault(unit.project_key, []).append(unit.normalized_word)
        grouped_frequencies.setdefault(unit.project_key, []).append(unit.frequency)
        grouped_covered_words.setdefault(unit.project_key, set()).update(
            unit.family_members
        )

    projects: dict[str, ExportResult] = {}
    for project_key in _ordered_project_keys(grouped_tasks):
        tasks = grouped_tasks[project_key]
        words = grouped_words[project_key]
        projects[project_key] = ExportResult(
            tasks=tasks,
            report=_project_report(
                project_key,
                tasks,
                words,
                covered_word_count=len(grouped_covered_words[project_key]),
            ),
            exported_words=words,
            frequencies=tuple(grouped_frequencies[project_key]),
        )

    exported_words = [unit.normalized_word for unit in units]
    covered_words = len(
        {
            word
            for unit in units
            for word in unit.family_members
        }
    )
    return SplitExportResult(
        projects=projects,
        report=_split_report(
            result,
            projects,
            covered_word_count=covered_words,
            analyzer_revision=morphology_analyzer.revision,
        ),
        exported_words=exported_words,
    )


def attach_contextual_project(
    result: SplitExportResult,
    contextual: ContextualExportResult,
) -> SplitExportResult:
    """Attach the sentence-context project to a dictionary split export."""
    from .contextual_review import PROJECT_KEY

    if PROJECT_KEY in result.projects:
        raise AnnotationExportError(f"duplicate project key: {PROJECT_KEY}")
    projects = dict(result.projects)
    if contextual.tasks:
        projects[PROJECT_KEY] = ExportResult(
            tasks=contextual.tasks,
            report=contextual.report,
            exported_words=[
                f"{item.sample_id}:{item.token_index}"
                for item in contextual.occurrences
            ],
        )
    report = dict(result.report)
    report["contextual_occurrence_count"] = len(contextual.tasks)
    report["project_count"] = len(projects)
    return SplitExportResult(
        projects=projects,
        report=report,
        exported_words=result.exported_words,
        contextual_occurrences=tuple(contextual.occurrences),
    )


def classify_project(word: str, label: str) -> dict[str, Any]:
    """Return the focused Label Studio project metadata for one normalized word."""
    if label == "U":
        key = _u_project_key(word)
        suggestion = annotation_suggestion(word, label)
        rules = (
            list(dict.fromkeys(parse_dsl(suggestion).rule_ids)) if suggestion else []
        )
        if "ц" in word.casefold():
            key = _project_key_for_rule(TS_RULE.rule_id)
        return {"key": key, "title": project_title_for_key(key), "dsl_rules": rules}
    result = conversion_result_for_annotation(word, label)
    rules = list(dict.fromkeys(result.rule_ids)) if result is not None else []
    if "ц" in word.casefold():
        key = _project_key_for_rule(TS_RULE.rule_id)
        title = project_title_for_key(key)
    elif native_hamza_family(word) is not None and _contains_hamza(result):
        key = _project_key_for_rule(HAMZA_RULE.rule_id)
        title = project_title_for_key(key)
    elif len(rules) > 1:
        key = "complex_multi_rule"
        title = "Complex multi-rule words"
    elif len(rules) == 1:
        key = _project_key_for_rule(rules[0])
        title = project_title_for_key(key)
    else:
        key = "catchall"
        title = "Catchall word review"
    return {"key": key, "title": title, "dsl_rules": rules}


NATIVE_HAMZA_FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("маэмай", "maemay", "maʼmay"),
    ("таэмин", "taemin", "täʼmin"),
    ("тәэмин", "täemin", "täʼmin"),
    ("тәэсир", "täesir", "täʼsir"),
    ("мөэмин", "möemin", "möʼmin"),
    ("мәсьәлә", "mäsälä", "mäsʼälä"),
    ("җөрьәт", "cörät", "cörʼät"),
    ("коръән", "qorän", "qorʼän"),
)

LOANWORD_HAMZA_PREFIXES: dict[str, str] = {
    "маэмай": "maemay",
    "таэмин": "taemin",
    "тәэмин": "täemin",
    "тәэсир": "täesir",
    "мөэмин": "möemin",
    "мәсьәлә": "mäsʼälä",
    "җөрьәт": "cörʼät",
    "коръән": "korʼän",
}


def native_hamza_family(word: str) -> str | None:
    """Return the verified lexical hamza stem for a word."""
    folded = word.casefold()
    family = next(
        (
            cyrillic_prefix
            for cyrillic_prefix, _, _ in NATIVE_HAMZA_FAMILIES
            if folded.startswith(cyrillic_prefix)
        ),
        None,
    )
    return "тәэмин" if family == "таэмин" else family


def _contains_hamza(result: ConversionResult | None) -> bool:
    if result is None:
        return False
    return HAMZA_RULE.rule_id in result.rule_ids or any(
        isinstance(segment, Literal) and ZAMANALIF_APOSTROPHE in segment.text
        for segment in result.segments
    )


def _u_project_key(word: str) -> str:
    if "-" in word:
        return "u_hyphenated"
    if _is_u_abbrev_fragment(word):
        return "u_abbrev_fragment"
    if any(char in TATAR_SPECIFIC_PART_LETTERS for char in word):
        return "u_tatar_specific"
    if contains_conditional_letter(word):
        return "u_conditional_plain"
    return "u_other"


def _is_u_abbrev_fragment(word: str) -> bool:
    cyrillic_count = len(CYRILLIC_RE.findall(word))
    return cyrillic_count <= 3 or "." in word or "/" in word


def _export_from_records(
    records: Iterable[dict[str, Any]],
    *,
    max_items: int | None,
    include_rl: bool,
    include_unknown: bool,
    min_frequency: int,
    sort_by: str,
    reviewed_words: set[str],
    word_resolutions: dict[str, str],
) -> ExportResult:
    stats: dict[str, WordStats] = {}
    total_sentences = 0
    total_tokens = 0
    homonym_words: set[str] = set()
    word_occurrences: Counter[str] = Counter()
    for record in records:
        total_sentences += 1
        if record.get("tatar") is not True:
            continue
        tokens = record.get("tokens")
        if not isinstance(tokens, list):
            continue
        for token in tokens:
            if not isinstance(token, dict):
                continue
            text = token.get("text")
            if not isinstance(text, str):
                continue
            label = token.get("label", "U")
            if label not in {"N", "RL", "U"}:
                label = "U"
            total_tokens += 1
            normalized = normalize_word(text)
            if not normalized:
                continue
            word_occurrences[normalized] += 1
            resolution = word_resolutions.get(normalized)
            if token.get("homonym") is True and resolution not in {"N", "RL", "U"}:
                homonym_words.add(normalized)

            entry = stats.get(normalized)
            if entry is None:
                entry = WordStats(normalized=normalized, display=_display_word(text, normalized))
                stats[normalized] = entry
            entry.frequency += 1
            effective_label = resolution if resolution in {"N", "RL", "U"} else label
            if native_hamza_family(normalized) is not None and effective_label == "U":
                effective_label = "N"
            entry.label_counts[effective_label] += 1
            entry.conditional_letters.update(
                char for char in normalized if char in CONDITIONAL_LETTERS
            )

    decision_counts: Counter[str] = Counter()
    candidates: list[WordStats] = []
    mixed_harmony_n_skipped = 0
    reviewed_words_skipped = 0
    for entry in stats.values():
        branches = conversion_branches(entry.normalized)
        decision_counts[branches.state] += 1
        resolution = word_resolutions.get(entry.normalized)
        if resolution == "contextual_homonym":
            continue
        if entry.normalized in homonym_words:
            continue
        if entry.normalized in reviewed_words:
            reviewed_words_skipped += 1
            continue
        if entry.label == "RL" and not include_rl:
            continue
        if entry.label == "U" and not include_unknown:
            continue
        if (
            entry.label == "N"
            and vowel_harmony_class(entry.normalized) == "mixed_front_back"
            and native_hamza_family(entry.normalized) is None
        ):
            mixed_harmony_n_skipped += 1
            continue
        if (
            branches.state == "origin_independent"
            and native_hamza_family(entry.normalized) is None
        ):
            continue
        if entry.frequency < min_frequency:
            continue
        candidates.append(entry)
    if sort_by == "frequency_desc":
        candidates.sort(key=lambda item: (-item.frequency, item.normalized))
    elif sort_by == "word":
        candidates.sort(key=lambda item: item.normalized)
    else:
        raise ValueError("--sort-by must be one of: frequency_desc, word")
    if max_items is not None:
        candidates = candidates[:max_items]

    tasks = [
        {
            "data": {
                "cyrl_word": entry.display,
                "auto_zamanalif": annotation_suggestion(
                    entry.normalized, entry.label
                ),
                "gemini_origin": entry.label,
                "hints_html": decision_html(entry),
            }
        }
        for entry in candidates
    ]
    return ExportResult(
        tasks=tasks,
        report=_report(
            total_sentences=total_sentences,
            total_tokens=total_tokens,
            unique_word_forms=len(stats),
            exported=candidates,
            mixed_harmony_n_skipped=mixed_harmony_n_skipped,
            reviewed_words_skipped=reviewed_words_skipped,
            homonym_occurrences_skipped=sum(
                word_occurrences[word] for word in homonym_words
            ),
            homonym_words_skipped=len(homonym_words),
            decision_counts=decision_counts,
        ),
        exported_words=[entry.normalized for entry in candidates],
        frequencies=tuple(entry.frequency for entry in candidates),
    )


def _export_units(
    result: ExportResult,
    morphology_analyzer: MorphologyAnalyzer,
) -> list[_ExportUnit]:
    if len(result.frequencies) != len(result.tasks):
        raise AnnotationExportError("word frequencies do not match exported tasks")

    ordinary: list[_ExportUnit] = []
    catchall: list[tuple[dict[str, Any], str, int, str]] = []
    hamza: dict[tuple[str, str], list[tuple[dict[str, Any], str, int, str]]] = {}
    for task, normalized, frequency in zip(
        result.tasks,
        result.exported_words,
        result.frequencies,
        strict=True,
    ):
        label = task["data"]["gemini_origin"]
        project_key = classify_project(normalized, label)["key"]
        if project_key == "catchall":
            catchall.append((task, normalized, frequency, label))
        elif project_key == "hamza" and (
            family := native_hamza_family(normalized)
        ) is not None:
            hamza.setdefault((family, label), []).append(
                (task, normalized, frequency, label)
            )
        else:
            ordinary.append(
                _ExportUnit(
                    task=task,
                    normalized_word=normalized,
                    project_key=project_key,
                    frequency=frequency,
                    family_members=(normalized,),
                )
            )

    for items in hamza.values():
        representative = min(
            items,
            key=lambda item: (len(item[1]), -item[2], item[1]),
        )
        ordinary.append(
            _ExportUnit(
                task=representative[0],
                normalized_word=representative[1],
                project_key="hamza",
                frequency=sum(item[2] for item in items),
                family_members=tuple(
                    item[1]
                    for item in sorted(
                        items,
                        key=lambda item: (
                            item[1] != representative[1],
                            len(item[1]),
                            item[1],
                        ),
                    )
                ),
            )
        )

    identities = morphology_analyzer.analyze(item[1] for item in catchall)
    grouped: dict[tuple[Any, ...], list[tuple[dict[str, Any], str, int, str]]] = {}
    for item in catchall:
        _, normalized, _, label = item
        identity = identities.get(normalized)
        key = (
            ("family", identity.lemma, identity.part_of_speech, label)
            if identity is not None
            else ("singleton", normalized)
        )
        grouped.setdefault(key, []).append(item)

    for items in grouped.values():
        identity = identities.get(items[0][1])
        if identity is None:
            representatives = items
        else:
            representatives = []
            for item in sorted(
                items,
                key=lambda value: (-len(value[1]), -value[2], value[1]),
            ):
                if any(
                    is_safe_family_member(
                        representative[1],
                        item[1],
                        identity.lemma,
                    )
                    for representative in representatives
                ):
                    continue
                representatives.append(item)
        for task, normalized, _, _ in representatives:
            covered = [
                item
                for item in items
                if item[1] == normalized
                or (
                    identity is not None
                    and is_safe_family_member(
                        normalized,
                        item[1],
                        identity.lemma,
                    )
                )
            ]
            members = tuple(
                item[1]
                for item in sorted(
                    covered,
                    key=lambda item: (
                        item[1] != normalized,
                        -len(item[1]),
                        item[1],
                    ),
                )
            )
            ordinary.append(
                _ExportUnit(
                    task=task,
                    normalized_word=normalized,
                    project_key="catchall",
                    frequency=sum(item[2] for item in covered),
                    family_members=members,
                )
            )
    return ordinary


def _task_with_project_meta(
    task: dict[str, Any],
    project_key: str,
) -> dict[str, Any]:
    data = dict(task["data"])
    if project_key == "catchall":
        meta = {
            "schema_version": CATCHALL_TASK_SCHEMA_VERSION,
            "project_key": project_key,
        }
    else:
        suggestion_dsl = data["auto_zamanalif"]
        variants = annotation_variants(suggestion_dsl)
        del data["auto_zamanalif"]
        data["zamanalif_variants"] = "\n".join(
            variant.zamanalif for variant in variants
        )
        meta = {
            "schema_version": FOCUSED_DICTIONARY_TASK_SCHEMA_VERSION,
            "project_key": project_key,
            "suggested_zamanalif_dsl": suggestion_dsl,
            "variant_policies": [
                [dict(policy) for policy in variant.policies]
                for variant in variants
            ],
        }
    return {
        "data": data,
        "meta": meta,
    }


def _sqlite_records(db_path: str | Path) -> Iterable[dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as conn:
        yield from _sqlite_records_from_connection(conn)


def _sqlite_records_from_connection(
    conn: sqlite3.Connection,
) -> Iterable[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        select s.id, s.text, p.tatar, p.tokens_json
        from preannotation_state p
        join samples s on s.id = p.sample_id
        where p.status = 'annotated'
          and p.tokens_json is not null
        order by s.id
        """
    )
    for row in rows:
        try:
            tokens = json.loads(row["tokens_json"])
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid tokens_json for {row['id']}: {exc}") from exc
        yield {
            "id": row["id"],
            "text": row["text"],
            "tatar": bool(row["tatar"]),
            "tokens": tokens,
        }


def eligible_catchall_words(
    conn: sqlite3.Connection,
) -> dict[str, CatchallWord]:
    """Return every observed word currently eligible for catchall review."""
    return eligible_project_words(conn, "catchall")


def eligible_project_words(
    conn: sqlite3.Connection,
    project_key: str,
) -> dict[str, CatchallWord]:
    """Return observed unreviewed candidates for one dictionary project."""
    resolutions = {
        str(row[0]): str(row[1])
        for row in conn.execute(
            "select normalized_word, decision from word_resolutions"
        ).fetchall()
    }
    result = _export_from_records(
        _sqlite_records_from_connection(conn),
        max_items=None,
        include_rl=True,
        include_unknown=True,
        min_frequency=1,
        sort_by="word",
        reviewed_words=set(),
        word_resolutions=resolutions,
    )
    eligible: dict[str, CatchallWord] = {}
    for task, word, frequency in zip(
        result.tasks,
        result.exported_words,
        result.frequencies,
        strict=True,
    ):
        origin = task["data"]["gemini_origin"]
        if classify_project(word, origin)["key"] == project_key:
            eligible[word] = CatchallWord(word, origin, frequency)
    return eligible


def convert_for_annotation(word: str, label: str) -> str:
    """Convert one normalized word using the branch implied by Gemini label."""
    label = label.strip()
    if label not in {"N", "RL"}:
        converted = _best_effort_unknown(word)
    else:
        converted = _convert_known_label(word, label)
    return converted if _is_clean_zamanalif(converted) else ""


def conversion_result_for_annotation(word: str, label: str) -> ConversionResult | None:
    """Return structured annotation output with accepted convention choices."""
    compact = convert_for_annotation(word, label)
    if not compact:
        return None
    if native_hamza_family(word) is not None:
        return result_with_native_hamza_choices(
            word,
            ConversionResult((Literal(compact),)),
        )
    result = result_with_russian_sign_glide_choices(word, compact, label)
    if result.has_choices:
        return result_with_rl_y_choices(word, result, label)
    result = result_with_russian_soft_sign_choices(word, compact, label)
    if result.has_choices:
        result = result_with_month_name_choices(word, result)
        result = result_with_russian_jotated_softening_result(word, result, label)
        result = result_with_loanword_final_ka_choices(word, result, label)
        return result_with_rl_y_choices(word, result, label)
    result = result_with_cilquar_native_uw_choices(word, compact, label)
    if result.has_choices:
        return result
    result = result_with_native_uw_choices(word, compact, label)
    if result.has_choices:
        return result_with_ie_glide_choices(word, result_with_iya_choices(word, result))
    result = result_with_month_name_choices(word, ConversionResult((Literal(compact),)))
    result = result_with_russian_shch_yo_choices(word, result, label)
    result = result_with_russian_jotated_softening_result(word, result, label)
    result = result_with_loanword_final_ka_choices(word, result, label)
    result = result_with_kts_after_k_choices(word, result, label)
    result = result_with_final_ts_suffix_choices(word, result, label)
    result = result_with_project_e_choices(word, result, label)
    result = result_with_rl_y_choices(word, result, label)
    result = result_with_figyl_stem_choices(word, result)
    result = result_with_shigyr_stem_choices(word, result)
    result = result_with_ijtimagiy_stem_choices(word, result)
    result = result_with_erzya_ya_choices(word, result)
    result = result_with_kagaz_stem_choices(word, result)
    result = result_with_mashgul_stem_choices(word, result)
    result = result_with_iya_choices(word, result)
    result = result_with_ie_glide_choices(word, result)
    result = result_with_jamgiyat_iya_choices(word, result, label)
    return result_with_mostaqil_choices(word, result, label)


def result_with_iya_choices(source: str, result: ConversionResult) -> ConversionResult:
    """Annotate aligned Cyrillic ``ия`` / compact ``iä`` spans in a result."""
    source_count = source.casefold().count("ия")
    output_count = sum(
        segment.text.casefold().count("iä")
        for segment in result.segments
        if isinstance(segment, Literal)
    )
    if source_count == 0 or source_count != output_count:
        return result

    options = _iya_options_for_source(source)
    segments: list[Literal | Choice] = []
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        start = 0
        for match in re.finditer("iä", segment.text, flags=re.IGNORECASE):
            _append_literal_segment(segments, segment.text[start : match.start() + 1])
            segments.append(Choice(IYA_RULE.rule_id, options))
            start = match.end()
        _append_literal_segment(segments, segment.text[start:])
    return ConversionResult(tuple(segments))


def _iya_options_for_source(source: str) -> tuple[tuple[str, str], ...]:
    folded = source.casefold()
    if folded.startswith(("әдәбия", "әүлия", "риялы")):
        return (("compact", "a"), ("explicit", "ya"))
    return IYA_RULE.options


def result_with_jamgiyat_iya_choices(
    source: str, result: ConversionResult, label: str
) -> ConversionResult:
    """Annotate ``җәмгыят*`` compact PDF ``iä`` vs explicit ``iyä`` convention."""
    if label != "N" or not source.casefold().startswith("җәмгыят"):
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        start = 0
        for match in re.finditer("iyä", text, flags=re.IGNORECASE):
            _append_literal_segment(segments, text[start : match.start() + 1])
            segments.append(Choice(IYA_RULE.rule_id, IYA_RULE.options))
            start = match.end()
            changed = True
        _append_literal_segment(segments, text[start:])
    return ConversionResult(tuple(segments)) if changed else result


def result_with_ie_glide_choices(source: str, result: ConversionResult) -> ConversionResult:
    """Annotate Cyrillic ``ие`` as a plain ``ie`` vs glide ``iye`` convention."""
    if source.casefold().endswith(("иев", "иева", "әев", "әева")):
        return result
    source_count = source.casefold().count("ие")
    output_count = sum(
        segment.text.casefold().count("ie")
        for segment in result.segments
        if isinstance(segment, Literal)
    )
    if source_count == 0 or source_count != output_count:
        return result

    segments: list[Literal | Choice] = []
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        start = 0
        for match in re.finditer("ie", segment.text, flags=re.IGNORECASE):
            _append_literal_segment(segments, segment.text[start : match.start() + 1])
            segments.append(Choice(E_GLIDE_RULE.rule_id, E_GLIDE_RULE.options))
            start = match.end()
        _append_literal_segment(segments, segment.text[start:])
    return ConversionResult(tuple(segments))


def result_with_kts_after_k_choices(
    source: str, result: ConversionResult, label: str
) -> ConversionResult:
    """Annotate loanword Cyrillic ``кц`` as an attested ``ks`` vs ``kts`` policy."""
    if label != "RL":
        return result
    source_count = source.casefold().count("кц")
    output_count = sum(
        segment.text.casefold().count("ks")
        for segment in result.segments
        if isinstance(segment, Literal)
    )
    if source_count == 0 or source_count != output_count:
        return result

    segments: list[Literal | Choice] = []
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        start = 0
        for match in re.finditer("ks", segment.text, flags=re.IGNORECASE):
            _append_literal_segment(segments, segment.text[start : match.start() + 1])
            segments.append(Choice(TS_RULE.rule_id, TS_RULE.options))
            start = match.end()
        _append_literal_segment(segments, segment.text[start:])
    return ConversionResult(tuple(segments))


TATAR_SUFFIXES_AFTER_FINAL_TS: tuple[str, ...] = (
    "лары",
    "ләре",
    "ларга",
    "ләргә",
    "ларда",
    "ләрдә",
    "лардан",
    "ләрдән",
    "ларын",
    "ләрен",
    "ларының",
    "ләренең",
    "ларны",
    "ләрне",
    "лар",
    "ләр",
    "ының",
    "енең",
    "ында",
    "ендә",
    "ыннан",
    "еннән",
    "ына",
    "енә",
    "ны",
    "не",
    "ның",
    "нең",
    "дан",
    "дән",
    "тан",
    "тән",
    "да",
    "дә",
    "та",
    "тә",
    "га",
    "гә",
    "ка",
    "кә",
)


def result_with_final_ts_suffix_choices(
    source: str, result: ConversionResult, label: str
) -> ConversionResult:
    """Annotate loanword stem-final ``ц`` before Tatar suffix as ``s`` vs ``ts``."""
    if label != "RL":
        return result
    folded = source.casefold()
    source_count = sum(
        1
        for index, char in enumerate(folded)
        if char == "ц" and folded[index + 1 :].startswith(TATAR_SUFFIXES_AFTER_FINAL_TS)
    )
    output_count = sum(
        segment.text.casefold().count("ts")
        for segment in result.segments
        if isinstance(segment, Literal)
    )
    if source_count == 0 or source_count > output_count:
        return result

    segments: list[Literal | Choice] = []
    remaining = source_count
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        start = 0
        for match in re.finditer("ts", text, flags=re.IGNORECASE):
            if remaining <= 0:
                break
            _append_literal_segment(segments, text[start : match.start()])
            segments.append(Choice(TS_RULE.rule_id, TS_RULE.options))
            start = match.end()
            remaining -= 1
        _append_literal_segment(segments, text[start:])
    return ConversionResult(tuple(segments))


def result_with_project_e_choices(
    source: str, result: ConversionResult, label: str
) -> ConversionResult:
    """Annotate the attested ``проект`` / ``proekt`` vs ``proyekt`` convention."""
    if label != "RL" or not source.casefold().startswith("проект"):
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in result.segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and text.startswith("proyekt"):
            _append_literal_segment(segments, "pro")
            segments.append(Choice(E_GLIDE_RULE.rule_id, E_GLIDE_RULE.options))
            _append_literal_segment(segments, text[len("proye") :])
            changed = True
            continue
        _append_literal_segment(segments, text)
    return ConversionResult(tuple(segments)) if changed else result


def result_with_rl_y_choices(
    source: str, result: ConversionResult, label: str
) -> ConversionResult:
    """Annotate long vs short ``ы`` in known Russian/Russian-through-Russian stems."""
    if label != "RL" or not _uses_long_loanword_y(source.casefold()):
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in result.segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        start = 0
        for match in re.finditer("ıy", text):
            _append_literal_segment(segments, text[start : match.start()])
            segments.append(Choice(RL_Y_RULE.rule_id, RL_Y_RULE.options))
            start = match.end()
            changed = True
        _append_literal_segment(segments, text[start:])
    return ConversionResult(tuple(segments)) if changed else result


MONTH_NAME_CHOICES: tuple[tuple[str, str, str], ...] = (
    ("гыйнвар", "ğıynwar", "ğinwar"),
    ("февраль", "fevral", "fevral"),
    ("март", "mart", "mart"),
    ("апрель", "aprel", "aprel"),
    ("май", "may", "may"),
    ("июнь", "iyun", "iyün"),
    ("июль", "iyul", "iyül"),
    ("август", "avgust", "avgust"),
    ("сентябрь", "sentyabr", "sentäbr"),
    ("сентябр", "sentyabr", "sentäbr"),
    ("октябрь", "oktyabr", "oktäbr"),
    ("октябр", "oktyabr", "oktäbr"),
    ("ноябрь", "noyabr", "noyäbr"),
    ("декабрь", "dekabr", "dekäbr"),
)


def result_with_month_name_choices(source: str, result: ConversionResult) -> ConversionResult:
    """Annotate PDF month-name spellings vs ordinary loanword spellings."""
    folded = source.casefold()
    matched = next(
        (
            (cyrillic_stem, ordinary_stem, pdf_stem)
            for cyrillic_stem, ordinary_stem, pdf_stem in MONTH_NAME_CHOICES
            if folded.startswith(cyrillic_stem)
        ),
        None,
    )
    if matched is None:
        return result
    _, ordinary_stem, pdf_stem = matched
    if ordinary_stem == pdf_stem:
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and text.startswith(ordinary_stem):
            segments.append(
                Choice(
                    MONTH_NAME_RULE.rule_id,
                    (("ordinary", ordinary_stem), ("pdf", pdf_stem)),
                )
            )
            _append_literal_segment(segments, text[len(ordinary_stem) :])
            changed = True
            continue
        _append_literal_segment(segments, text)
    return ConversionResult(tuple(segments)) if changed else result


def result_with_figyl_stem_choices(source: str, result: ConversionResult) -> ConversionResult:
    """Annotate the attested ``фигыль`` stem as ANTAT vs PDF policy."""
    if not source.casefold().startswith("фигыль"):
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and text.startswith("fiğıl"):
            segments.append(Choice(FIGYL_STEM_RULE.rule_id, FIGYL_STEM_RULE.options))
            _append_literal_segment(segments, text[len("fiğıl") :])
            changed = True
            continue
        _append_literal_segment(segments, text)
    return ConversionResult(tuple(segments)) if changed else result


def result_with_shigyr_stem_choices(source: str, result: ConversionResult) -> ConversionResult:
    """Annotate the attested ``шигырь`` stem as ANTAT vs PDF policy."""
    if "шигыр" not in source.casefold():
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        start = 0
        for match in re.finditer("şiğır", text):
            _append_literal_segment(segments, text[start : match.start()])
            segments.append(Choice(SHIGYR_STEM_RULE.rule_id, SHIGYR_STEM_RULE.options))
            start = match.end()
            changed = True
        _append_literal_segment(segments, text[start:])
    return ConversionResult(tuple(segments)) if changed else result


def result_with_ijtimagiy_stem_choices(source: str, result: ConversionResult) -> ConversionResult:
    """Annotate attested ``иҗтимагый`` spelling as ANTAT vs PDF policy."""
    if not source.casefold().startswith("иҗтимагый"):
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and text.startswith("ictimaği"):
            segments.append(Choice(IJTIMAGIY_STEM_RULE.rule_id, IJTIMAGIY_STEM_RULE.options))
            _append_literal_segment(segments, text[len("ictimaği") :])
            changed = True
            continue
        _append_literal_segment(segments, text)
    return ConversionResult(tuple(segments)) if changed else result


def result_with_erzya_ya_choices(source: str, result: ConversionResult) -> ConversionResult:
    """Annotate ``ерзя`` with the generic Cyrillic ``я`` policy."""
    if "ерзя" not in source.casefold():
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and "erzya" in text:
            prefix, suffix = text.split("erzya", 1)
            _append_literal_segment(segments, prefix + "erz")
            segments.append(Choice(YA_RULE.rule_id, YA_RULE.options))
            _append_literal_segment(segments, suffix)
            changed = True
            continue
        _append_literal_segment(segments, text)
    return ConversionResult(tuple(segments)) if changed else result


def result_with_kagaz_stem_choices(source: str, result: ConversionResult) -> ConversionResult:
    """Annotate attested ``кәгаз`` stem spelling as ANTAT vs PDF policy."""
    if not source.casefold().startswith(("кәгазъ", "кәгазь", "кәгаз")):
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and text.startswith("käğaz"):
            segments.append(Choice(KAGAZ_STEM_RULE.rule_id, KAGAZ_STEM_RULE.options))
            _append_literal_segment(segments, text[len("käğaz") :])
            changed = True
            continue
        _append_literal_segment(segments, text)
    return ConversionResult(tuple(segments)) if changed else result


def result_with_mashgul_stem_choices(source: str, result: ConversionResult) -> ConversionResult:
    """Annotate attested ``мәшгуль`` stem spelling as ANTAT vs PDF policy."""
    if not source.casefold().startswith("мәшгуль"):
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and text.startswith("mäşğul"):
            segments.append(Choice(MASHGUL_STEM_RULE.rule_id, MASHGUL_STEM_RULE.options))
            _append_literal_segment(segments, text[len("mäşğul") :])
            changed = True
            continue
        _append_literal_segment(segments, text)
    return ConversionResult(tuple(segments)) if changed else result


def result_with_native_hamza_choices(
    source: str,
    result: ConversionResult,
) -> ConversionResult:
    """Represent verified native lexical hamza as one global policy choice."""
    family = native_hamza_family(source)
    if family is None:
        return result
    preserved_prefix = next(
        preserved
        for cyrillic, _, preserved in NATIVE_HAMZA_FAMILIES
        if cyrillic == family
    )
    apostrophe_index = preserved_prefix.index(ZAMANALIF_APOSTROPHE)

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and text.startswith(preserved_prefix):
            _append_literal_segment(segments, preserved_prefix[:apostrophe_index])
            segments.append(Choice(HAMZA_RULE.rule_id, HAMZA_RULE.options))
            _append_literal_segment(
                segments,
                preserved_prefix[apostrophe_index + 1 :]
                + text[len(preserved_prefix) :],
            )
            changed = True
            continue
        _append_literal_segment(segments, text)
    return ConversionResult(tuple(segments)) if changed else result


def result_with_mostaqil_choices(
    source: str, result: ConversionResult, label: str
) -> ConversionResult:
    """Annotate the attested ``мөстәкыйль`` convention as PDF vs ANTAT policy."""
    if label != "N" or not source.casefold().startswith("мөстәкыйль"):
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in result.segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and text.startswith(("möstäkıyl", "möstäqıyl")):
            stem = "möstäkıyl" if text.startswith("möstäkıyl") else "möstäqıyl"
            suffix = text[len(stem) :]
            _append_literal_segment(segments, "möstä")
            antat_text = "qıyl" + ZAMANALIF_APOSTROPHE if suffix else "qıyl"
            segments.append(
                Choice(
                    MOSTAQIL_RULE.rule_id,
                    (("pdf", "qil"), ("antat", antat_text)),
                )
            )
            _append_literal_segment(segments, suffix)
            changed = True
            continue
        _append_literal_segment(segments, text)
    return ConversionResult(tuple(segments)) if changed else result


def _append_literal_segment(segments: list[Literal | Choice], text: str) -> None:
    if not text:
        return
    if segments and isinstance(segments[-1], Literal):
        segments[-1] = Literal(segments[-1].text + text)
        return
    segments.append(Literal(text))


def result_with_russian_sign_glide_choices(
    source: str,
    converted: str,
    label: str,
) -> ConversionResult:
    """Annotate Russian soft/hard signs before glide letters as a policy choice."""
    if label != "RL" or not any(sign in source for sign in "ьъ"):
        return ConversionResult((Literal(converted),))

    segments: list[Literal | Choice] = []
    source_index = 0
    converted_index = 0
    previous_sign_before_e = False
    while source_index < len(source):
        char = source[source_index]
        if (
            char in {"ь", "ъ"}
            and source_index + 1 < len(source)
            and source[source_index + 1] in {"я", "ю", "е", "о"}
        ):
            next_char = source[source_index + 1]
            if next_char == "е":
                if converted.startswith("y", converted_index):
                    converted_index += 1
                elif converted.startswith(ZAMANALIF_APOSTROPHE, converted_index):
                    converted_index += 1
                else:
                    return ConversionResult((Literal(converted),))
                segments.append(Choice(RUS_SIGN_E_RULE.rule_id, RUS_SIGN_E_RULE.options))
                previous_sign_before_e = True
                source_index += 1
                continue
            if next_char == "о":
                if converted.startswith(ZAMANALIF_APOSTROPHE + "y", converted_index):
                    converted_index += 2
                elif converted.startswith(ZAMANALIF_APOSTROPHE, converted_index):
                    converted_index += 1
                segments.append(
                    Choice(RUS_SOFT_SIGN_O_RULE.rule_id, RUS_SOFT_SIGN_O_RULE.options)
                )
                source_index += 1
                continue
            if converted.startswith(ZAMANALIF_APOSTROPHE, converted_index):
                converted_index += 1
            segments.append(Choice(RUS_SIGN_RULE.rule_id, RUS_SIGN_RULE.options))
            source_index += 1
            continue

        if char == "е" and previous_sign_before_e:
            latin = "e"
            previous_sign_before_e = False
        else:
            latin = _char_conversion(char, source, source_index, label)
        if latin and converted.startswith(latin, converted_index):
            segments.append(Literal(latin))
            converted_index += len(latin)
        else:
            return ConversionResult((Literal(converted),))
        source_index += 1

    if converted_index != len(converted):
        return ConversionResult((Literal(converted),))
    return ConversionResult(tuple(segments))


def result_with_russian_soft_sign_choices(
    source: str,
    converted: str,
    label: str,
) -> ConversionResult:
    """Annotate ordinary Russian soft/hard signs as a preserve-vs-omit choice."""
    if (
        label != "RL"
        or not any(sign in source for sign in "ьъ")
        or ZAMANALIF_APOSTROPHE not in converted
    ):
        return ConversionResult((Literal(converted),))
    if "ия" in source and "iä" in converted:
        return ConversionResult((Literal(converted),))

    segments: list[Literal | Choice] = []
    source_index = 0
    converted_index = 0
    while source_index < len(source):
        char = source[source_index]
        if char in {"ь", "ъ"}:
            if (
                source_index + 1 < len(source)
                and source[source_index + 1] in {"я", "ю", "е"}
            ):
                return ConversionResult((Literal(converted),))
            if converted.startswith(ZAMANALIF_APOSTROPHE, converted_index):
                segments.append(Choice(RUS_SIGN_RULE.rule_id, RUS_SIGN_RULE.options))
                converted_index += 1
                source_index += 1
                continue

        latin = _char_conversion(char, source, source_index, label)
        if latin and converted.startswith(latin, converted_index):
            segments.append(Literal(latin))
            converted_index += len(latin)
        elif not latin:
            pass
        else:
            return ConversionResult((Literal(converted),))
        source_index += 1

    if converted_index != len(converted):
        return ConversionResult((Literal(converted),))
    return ConversionResult(tuple(segments))


def result_with_russian_shch_yo_choices(
    source: str, result: ConversionResult, label: str
) -> ConversionResult:
    """Annotate Russian loanword ``щё`` after ``щ`` as y/apostrophe/plain policy."""
    if label != "RL" or "щё" not in source.casefold():
        return result

    segments: list[Literal | Choice] = []
    changed = False
    pending_count = source.casefold().count("щё")
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        start = 0
        while pending_count:
            match_index = text.find("şçy", start)
            if match_index < 0:
                break
            choice_index = match_index + len("şç")
            _append_literal_segment(segments, text[start:choice_index])
            segments.append(Choice(RUS_JOTATION_RULE.rule_id, RUS_JOTATION_RULE.options))
            start = choice_index + 1
            pending_count -= 1
            changed = True
        _append_literal_segment(segments, text[start:])
    return ConversionResult(tuple(segments)) if changed else result


def result_with_russian_jotated_softening_result(
    source: str, result: ConversionResult, label: str
) -> ConversionResult:
    """Compose consonant + RL ``я/ю/ё`` softening with existing sign choices."""
    if label != "RL" or not any(char in source for char in "яюё"):
        return result
    if "ерзя" in source or source.casefold().startswith("вестибюль"):
        return result

    replacements: list[tuple[str, str]] = []
    for index, char in enumerate(source):
        if char not in {"я", "ю", "ё"} or not _is_russian_jotated_softening_position(source, index):
            continue
        if char == "ё" and source[index - 1].casefold() == "щ":
            continue
        previous = source[index - 1]
        previous_latin = _char_conversion(previous, source, index - 1, label)
        latin = _char_conversion(char, source, index, label)
        if not previous_latin or not latin.startswith("y"):
            continue
        replacements.append((previous_latin + latin, previous_latin))

    if not replacements:
        return result

    segments: list[Literal | Choice] = []
    changed = False
    pending = replacements.copy()
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        start = 0
        while pending:
            pattern, prefix = pending[0]
            match_index = text.find(pattern, start)
            if match_index < 0:
                break
            choice_index = match_index + len(prefix)
            _append_literal_segment(segments, text[start:choice_index])
            segments.append(
                Choice(
                    RUS_JOTATION_RULE.rule_id,
                    RUS_JOTATION_RULE.options,
                )
            )
            start = choice_index + 1
            pending.pop(0)
            changed = True
        _append_literal_segment(segments, text[start:])
    return ConversionResult(tuple(segments)) if changed else result


def _merge_adjacent_literals(result: ConversionResult) -> ConversionResult:
    segments: list[Literal | Choice] = []
    for segment in result.segments:
        if isinstance(segment, Literal):
            _append_literal_segment(segments, segment.text)
        else:
            segments.append(segment)
    return ConversionResult(tuple(segments))


def result_with_russian_jotated_softening_choices(
    source: str,
    converted: str,
    label: str,
) -> ConversionResult:
    """Annotate RL consonant + ``я/ю/ё`` as y-glide vs apostrophe convention."""
    if label != "RL" or not any(char in source for char in "яюё"):
        return ConversionResult((Literal(converted),))
    if "ерзя" in source or source.casefold().startswith("вестибюль"):
        return ConversionResult((Literal(converted),))

    segments: list[Literal | Choice] = []
    source_index = 0
    converted_index = 0
    while source_index < len(source):
        char = source[source_index]
        latin = _char_conversion(char, source, source_index, label)
        if not latin:
            source_index += 1
            continue

        if (
            char in {"я", "ю", "ё"}
            and _is_russian_jotated_softening_position(source, source_index)
            and not (char == "ё" and source[source_index - 1].casefold() == "щ")
            and latin.startswith("y")
            and converted.startswith(latin, converted_index)
        ):
            segments.append(
                Choice(
                    RUS_JOTATION_RULE.rule_id,
                    RUS_JOTATION_RULE.options,
                )
            )
            _append_literal_segment(segments, latin[1:])
            converted_index += len(latin)
            source_index += 1
            continue

        if converted.startswith(latin, converted_index):
            _append_literal_segment(segments, latin)
            converted_index += len(latin)
        else:
            return ConversionResult((Literal(converted),))
        source_index += 1

    if converted_index != len(converted):
        return ConversionResult((Literal(converted),))
    return ConversionResult(tuple(segments))


def _is_russian_jotated_softening_position(source: str, index: int) -> bool:
    if index == 0:
        return False
    previous = source[index - 1]
    if previous in FRONT_VOWELS | BACK_VOWELS | {
        "е",
        "ё",
        "ю",
        "я",
        "ь",
        "ъ",
        "-",
        ZAMANALIF_APOSTROPHE,
    }:
        return False
    return bool(CYRILLIC_RE.fullmatch(previous))


def result_with_loanword_final_ka_choices(
    source: str,
    result: ConversionResult,
    label: str,
) -> ConversionResult:
    """Annotate RL final ``-ка`` as Tatar suffix ``q`` vs loanword stem ``k``."""
    if label != "RL" or not source.endswith("ка"):
        return result
    short_result = result_with_short_loanword_final_ka_choices(source, result)
    if short_result != result:
        return short_result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and text.endswith("qa"):
            _append_literal_segment(segments, text[:-2])
            segments.append(Choice(RL_FINAL_KA_RULE.rule_id, RL_FINAL_KA_RULE.options))
            _append_literal_segment(segments, "a")
            changed = True
            continue
        _append_literal_segment(segments, text)
    return ConversionResult(tuple(segments)) if changed else result


SHORT_LOANWORD_FINAL_KA_CHOICES = frozenset({"кубка"})


def result_with_short_loanword_final_ka_choices(
    source: str, result: ConversionResult
) -> ConversionResult:
    """Annotate known short RL stems where final ``-ка`` is a Tatar suffix."""
    if source.casefold() not in SHORT_LOANWORD_FINAL_KA_CHOICES:
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and text.endswith("ka"):
            _append_literal_segment(segments, text[:-2])
            segments.append(Choice(RL_FINAL_KA_RULE.rule_id, RL_FINAL_KA_RULE.options))
            _append_literal_segment(segments, "a")
            changed = True
            continue
        _append_literal_segment(segments, text)
    return ConversionResult(tuple(segments)) if changed else result


def result_with_cilquar_native_uw_choices(
    source: str,
    converted: str,
    label: str,
) -> ConversionResult:
    """Annotate ``җилкуар`` after deterministic ``k -> q`` stem normalization."""
    if label != "N" or not source.casefold().startswith("җилкуар"):
        return ConversionResult((Literal(converted),))
    if not converted.startswith("cilquar"):
        return ConversionResult((Literal(converted),))
    return ConversionResult(
        (
            Literal("cilqu"),
            Choice(NATIVE_UW_RULE.rule_id, NATIVE_UW_RULE.options),
            Literal(converted[len("cilqu") :]),
        )
    )


def result_with_native_uw_choices(
    source: str,
    converted: str,
    label: str,
) -> ConversionResult:
    """Annotate native ``у/ү/ю`` glide conventions as plain-vs-glide choices."""
    if label != "N" or not any(char in source for char in "уүю"):
        return ConversionResult((Literal(converted),))

    segments: list[Literal | Choice] = []
    source_index = 0
    converted_index = 0
    while source_index < len(source):
        char = source[source_index]
        latin = _char_conversion(char, source, source_index, label)
        if latin and converted.startswith(latin, converted_index):
            _append_literal_segment(segments, latin)
            converted_index += len(latin)
        elif not latin:
            pass
        else:
            return ConversionResult((Literal(converted),))

        if _native_uw_insertion_needs_choice(source, source_index):
            segments.append(Choice(NATIVE_UW_RULE.rule_id, NATIVE_UW_RULE.options))
        source_index += 1

    if converted_index != len(converted):
        return ConversionResult((Literal(converted),))
    return ConversionResult(tuple(segments))


def convert_for_annotation_dsl(word: str, label: str) -> str:
    """Return canonical DSL for Label Studio, or an empty string on conversion failure."""
    result = conversion_result_for_annotation(word, label)
    return result.to_dsl() if result is not None else ""


@lru_cache(maxsize=200_000)
def conversion_branches(word: str) -> ConversionBranches:
    """Return canonical outputs for both possible origin classifications."""
    return ConversionBranches(
        native_dsl=convert_for_annotation_dsl(word, "N"),
        loanword_dsl=convert_for_annotation_dsl(word, "RL"),
    )


def decision_html(entry: WordStats) -> str:
    """Build compact review context for Label Studio."""
    items: list[str] = []
    effective_label = entry.label
    if entry.label == "U" and _u_project_key(entry.normalized) == "u_tatar_specific":
        effective_label = guess_unknown_tatar_specific_origin(entry.normalized)
    result = conversion_result_for_annotation(entry.normalized, effective_label)
    if result is not None and "IYA" in result.rule_ids:
        items.append("<b>ия</b> -> <b>iä</b> or <b>iyä</b> (<b>IYA</b>)")
    items.append(f"Gemini's origin prediction: <b>{_origin_prediction(entry.label)}</b>")
    if effective_label != entry.label:
        items.append(
            "Simple origin heuristic: "
            f"<b>{_origin_prediction(effective_label)}</b> (editable suggestion)"
        )
    if result is None:
        items.append("Automatic converter produced no clean Latin suggestion")
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def write_split_outputs(
    result: SplitExportResult,
    output_dir: str | Path,
    *,
    batch_size: int = LABELSTUDIO_SPLIT_BATCH_SIZE,
) -> list[Path]:
    """Write split Label Studio project JSON import files and return their paths."""
    if batch_size < 1:
        raise AnnotationExportError("batch_size must be positive")
    validate_split_export_result(result)
    root = Path(output_dir)
    root.parent.mkdir(parents=True, exist_ok=True)
    output_names: list[str] = []
    with TemporaryDirectory(prefix=f".{root.name}.staging-", dir=root.parent) as tmpdir:
        staging = Path(tmpdir)
        for project_key, project in result.projects.items():
            batches = list(_task_batches(project.tasks, batch_size))
            batch_total = len(batches)
            for batch_index, tasks in enumerate(batches, start=1):
                output_name = (
                    f"project_{project_key}_batch_{batch_index:03d}_"
                    f"of_{batch_total:03d}.json"
                )
                (staging / output_name).write_text(
                    _json_text(tasks),
                    encoding="utf-8",
                )
                output_names.append(output_name)

            rule_ids = (
                rule_id
                for task, word in zip(
                    project.tasks,
                    project.exported_words,
                    strict=True,
                )
                for rule_id in classify_project(
                    word,
                    task["data"]["gemini_origin"],
                )["dsl_rules"]
            )
            if project_key == "contextual_homonym":
                from .contextual_review import contextual_project_instructions

                instructions = contextual_project_instructions()
            else:
                instructions = render_project_instructions(
                    project_key,
                    project_title_for_key(project_key),
                    rule_ids,
                )
            (staging / f"project_{project_key}_instructions.html").write_text(
                instructions,
                encoding="utf-8",
            )

        _validate_serialized_split_outputs(staging, result, batch_size=batch_size)
        root.mkdir(parents=True, exist_ok=True)
        for existing in root.iterdir():
            if existing.is_file() and _is_managed_export_name(existing.name):
                existing.unlink()
        for staged in staging.iterdir():
            staged.replace(root / staged.name)

    return [root / name for name in output_names]


def validate_export_result(result: ExportResult) -> None:
    """Validate one unsplit Label Studio export before it is persisted."""
    if len(result.tasks) != len(result.exported_words):
        raise AnnotationExportError(
            "task count does not match exported-word count: "
            f"{len(result.tasks)} != {len(result.exported_words)}"
        )

    seen_words: set[str] = set()
    for index, (task, expected_word) in enumerate(
        zip(result.tasks, result.exported_words, strict=True)
    ):
        _validate_task(
            task,
            expected_word=expected_word,
            context=f"task {index}",
            seen_words=seen_words,
        )


def validate_split_export_result(result: SplitExportResult) -> None:
    """Validate project routing and global uniqueness in a split export."""
    seen_words: set[str] = set()
    contextual_words: set[str] = set()
    flattened_words: list[str] = []
    contextual_index = 0

    for project_key, project in result.projects.items():
        if not project.tasks:
            raise AnnotationExportError(f"project {project_key!r} is empty")
        if len(project.tasks) != len(project.exported_words):
            raise AnnotationExportError(
                f"project {project_key!r} task count does not match exported words"
            )
        expected_title = project_title_for_key(project_key)
        if project.report.get("project_key") != project_key:
            raise AnnotationExportError(f"project {project_key!r} has inconsistent report key")
        if project.report.get("project_title") != expected_title:
            raise AnnotationExportError(
                f"project {project_key!r} has inconsistent report title"
            )

        if project_key == "contextual_homonym":
            contextual_index = _validate_contextual_project(
                project,
                result.contextual_occurrences,
                contextual_index,
                contextual_words,
            )
            continue

        for index, (task, expected_word) in enumerate(
            zip(project.tasks, project.exported_words, strict=True)
        ):
            context = f"project {project_key!r} task {index}"
            data = _validate_task(
                task,
                expected_word=expected_word,
                context=context,
                seen_words=seen_words,
                project_key=project_key,
            )
            suggestion = (
                data["auto_zamanalif"]
                if project_key == "catchall"
                else task["meta"]["suggested_zamanalif_dsl"]
            )
            suggestion_rules = (
                list(dict.fromkeys(parse_dsl(suggestion).rule_ids))
                if suggestion
                else []
            )
            expected_project = classify_project(expected_word, data["gemini_origin"])
            if expected_project["key"] != project_key:
                raise AnnotationExportError(
                    f"{context} belongs to project {expected_project['key']!r}"
                )
            if suggestion_rules != expected_project["dsl_rules"]:
                raise AnnotationExportError(f"{context} has inconsistent DSL rules")
        flattened_words.extend(project.exported_words)

    if contextual_index != len(result.contextual_occurrences):
        raise AnnotationExportError(
            "contextual project does not contain exactly the exported occurrences"
        )
    overlap = sorted(seen_words & contextual_words)
    if overlap:
        raise AnnotationExportError(
            "contextual homonyms also appear in dictionary projects: "
            + ", ".join(overlap[:20])
        )
    if set(flattened_words) != set(result.exported_words):
        raise AnnotationExportError(
            "split projects do not contain exactly the exported words"
        )
    if len(flattened_words) != len(result.exported_words):
        raise AnnotationExportError(
            "split project word count does not match base exported-word count"
        )


def _validate_contextual_project(
    project: ExportResult,
    occurrences: tuple[OccurrenceKey, ...],
    start_index: int,
    contextual_words: set[str],
) -> int:
    from .contextual_review import PROJECT_KEY

    end_index = start_index + len(project.tasks)
    expected_occurrences = occurrences[start_index:end_index]
    if len(expected_occurrences) != len(project.tasks):
        raise AnnotationExportError("contextual task count does not match occurrence count")
    seen_occurrences: set[tuple[str, int]] = set()
    for index, (task, occurrence) in enumerate(
        zip(project.tasks, expected_occurrences, strict=True)
    ):
        context = f"project {PROJECT_KEY!r} task {index}"
        if not isinstance(task, dict) or set(task) != {"data", "meta"}:
            raise AnnotationExportError(
                f"{context} must contain exactly data and meta objects"
            )
        data = task.get("data")
        if not isinstance(data, dict) or set(data) != CONTEXTUAL_DATA_FIELDS:
            raise AnnotationExportError(
                f"{context}.data must contain exactly the contextual display fields"
            )
        meta = task.get("meta")
        if not isinstance(meta, dict) or set(meta) != CONTEXTUAL_META_FIELDS:
            raise AnnotationExportError(
                f"{context}.meta must contain exactly the contextual metadata fields"
            )
        if meta.get("schema_version") != TASK_SCHEMA_VERSION:
            raise AnnotationExportError(f"{context} has unsupported schema_version")
        if meta.get("project_key") != PROJECT_KEY:
            raise AnnotationExportError(f"{context} has wrong project_key")
        key = (meta.get("sample_id"), meta.get("token_index"))
        expected_key = (occurrence.sample_id, occurrence.token_index)
        if key != expected_key:
            raise AnnotationExportError(f"{context} has inconsistent occurrence identity")
        if key in seen_occurrences:
            raise AnnotationExportError(f"duplicate contextual occurrence: {key!r}")
        seen_occurrences.add(key)
        if not isinstance(data.get("sentence"), str) or not data["sentence"]:
            raise AnnotationExportError(f"{context} has invalid sentence")
        if not isinstance(data.get("context_html"), str) or "<mark>" not in data["context_html"]:
            raise AnnotationExportError(f"{context} has invalid context_html")
        surface = data.get("cyrl_word")
        if not isinstance(surface, str) or not normalize_word(surface):
            raise AnnotationExportError(f"{context} has invalid cyrl_word")
        contextual_words.add(normalize_word(surface))
        if data.get("gemini_origin") not in {"N", "RL", "U"}:
            raise AnnotationExportError(f"{context} has invalid gemini_origin")
        for field_name in (
            "auto_zamanalif",
            "native_zamanalif",
            "loanword_zamanalif",
        ):
            value = data.get(field_name)
            if not isinstance(value, str):
                raise AnnotationExportError(f"{context} has invalid {field_name}")
            if value:
                try:
                    parse_dsl(value)
                except DslError as exc:
                    raise AnnotationExportError(
                        f"{context} has invalid {field}: {exc}"
                    ) from exc
        if not isinstance(data.get("hints_html"), str):
            raise AnnotationExportError(f"{context} has invalid hints_html")
    return end_index


def _validate_task(
    task: Any,
    *,
    expected_word: str,
    context: str,
    seen_words: set[str],
    project_key: str | None = None,
) -> dict[str, Any]:
    expected_task_fields = {"data"} if project_key is None else {"data", "meta"}
    if not isinstance(task, dict) or set(task) != expected_task_fields:
        raise AnnotationExportError(
            f"{context} must contain exactly {sorted(expected_task_fields)}"
        )
    data = task.get("data")
    expected_data_fields = (
        FOCUSED_DICTIONARY_DATA_FIELDS
        if project_key is not None and project_key != "catchall"
        else DICTIONARY_DATA_FIELDS
    )
    if not isinstance(data, dict) or set(data) != expected_data_fields:
        raise AnnotationExportError(
            f"{context}.data must contain exactly the dictionary display fields"
        )
    if project_key is not None:
        meta = task.get("meta")
        expected_meta_fields = (
            CATCHALL_META_FIELDS
            if project_key == "catchall"
            else DICTIONARY_META_FIELDS
        )
        if not isinstance(meta, dict) or set(meta) != expected_meta_fields:
            raise AnnotationExportError(
                f"{context}.meta must contain exactly the dictionary metadata fields"
            )
        expected_schema_version = (
            CATCHALL_TASK_SCHEMA_VERSION
            if project_key == "catchall"
            else FOCUSED_DICTIONARY_TASK_SCHEMA_VERSION
        )
        if meta.get("schema_version") != expected_schema_version:
            raise AnnotationExportError(f"{context} has unsupported schema_version")
        if meta.get("project_key") != project_key:
            raise AnnotationExportError(f"{context} has wrong project_key")

    surface = data.get("cyrl_word")
    if not isinstance(surface, str) or not surface:
        raise AnnotationExportError(f"{context} has invalid cyrl_word")
    normalized = normalize_word(surface)
    if normalized != expected_word:
        raise AnnotationExportError(
            f"{context} normalized word {normalized!r} does not match {expected_word!r}"
        )

    origin = data.get("gemini_origin")
    if origin not in {"N", "RL", "U"}:
        raise AnnotationExportError(f"{context} has invalid gemini_origin")
    if normalized in seen_words:
        raise AnnotationExportError(f"duplicate normalized word: {normalized!r}")
    seen_words.add(normalized)

    expected_suggestion = annotation_suggestion(normalized, origin)
    if project_key is not None and project_key != "catchall":
        display_variants = data.get("zamanalif_variants")
        if not isinstance(display_variants, str):
            raise AnnotationExportError(f"{context} has invalid zamanalif_variants")
        hidden_suggestion = task["meta"].get("suggested_zamanalif_dsl")
        if hidden_suggestion != expected_suggestion:
            raise AnnotationExportError(
                f"{context} hidden suggestion does not match canonical conversion"
            )
        variants = annotation_variants(hidden_suggestion)
        expected_display = "\n".join(variant.zamanalif for variant in variants)
        if display_variants != expected_display:
            raise AnnotationExportError(
                f"{context} visible variants do not match resolved conversion"
            )
        expected_policies = [
            [dict(policy) for policy in variant.policies]
            for variant in variants
        ]
        if task["meta"].get("variant_policies") != expected_policies:
            raise AnnotationExportError(f"{context} has inconsistent variant policies")
    else:
        display_suggestion = data.get("auto_zamanalif")
        if not isinstance(display_suggestion, str):
            raise AnnotationExportError(f"{context} has invalid auto_zamanalif")
        if not display_suggestion:
            if origin != "U":
                raise AnnotationExportError(
                    f"{context} has an empty suggestion for origin {origin}"
                )
        else:
            try:
                parse_dsl(display_suggestion)
            except DslError as exc:
                raise AnnotationExportError(
                    f"{context} has invalid visible Zamanalif suggestion: {exc}"
                ) from exc
        if display_suggestion != expected_suggestion:
            raise AnnotationExportError(
                f"{context} suggestion does not match canonical conversion"
            )
    if not isinstance(data.get("hints_html"), str):
        raise AnnotationExportError(f"{context} has invalid hints_html")
    return data


def _validate_serialized_split_outputs(
    staging: Path,
    result: SplitExportResult,
    *,
    batch_size: int = LABELSTUDIO_SPLIT_BATCH_SIZE,
) -> None:
    expected_names: set[str] = set()
    seen_words: set[str] = set()
    seen_occurrences: set[tuple[str, int]] = set()

    for project_key, project in result.projects.items():
        batches = list(_task_batches(project.tasks, batch_size))
        batch_total = len(batches)
        for batch_index, tasks in enumerate(batches, start=1):
            name = (
                f"project_{project_key}_batch_{batch_index:03d}_"
                f"of_{batch_total:03d}.json"
            )
            expected_names.add(name)
            payload = _read_json_array(staging / name)
            if payload != tasks:
                raise AnnotationExportError(f"serialized batch {name} changed task data")
            if not 1 <= len(payload) <= batch_size:
                raise AnnotationExportError(f"batch {name} has invalid task count")
            for task in payload:
                data = task["data"]
                if project_key == "contextual_homonym":
                    meta = task["meta"]
                    occurrence = (meta["sample_id"], meta["token_index"])
                    if occurrence in seen_occurrences:
                        raise AnnotationExportError(
                            "duplicate contextual occurrence across serialized batches: "
                            f"{occurrence!r}"
                        )
                    seen_occurrences.add(occurrence)
                else:
                    normalized = normalize_word(data["cyrl_word"])
                    if normalized in seen_words:
                        raise AnnotationExportError(
                            "duplicate normalized word across serialized batches: "
                            f"{normalized!r}"
                        )
                    seen_words.add(normalized)

        instruction_name = f"project_{project_key}_instructions.html"
        expected_names.add(instruction_name)
        instruction_path = staging / instruction_name
        if not instruction_path.exists() or not instruction_path.read_text(
            encoding="utf-8"
        ).strip():
            raise AnnotationExportError(
                f"project {project_key!r} has empty instructions"
            )

    staged_names = {path.name for path in staging.iterdir() if path.is_file()}
    if staged_names != expected_names:
        raise AnnotationExportError(
            "staged export files differ from expected managed files"
        )


def _read_json_array(path: Path) -> list[Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnnotationExportError(f"cannot validate {path.name}: {exc}") from exc
    if not isinstance(payload, list):
        raise AnnotationExportError(f"{path.name} must contain a JSON array")
    return payload


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _is_managed_export_name(name: str) -> bool:
    return bool(MANAGED_BATCH_RE.fullmatch(name) or MANAGED_INSTRUCTIONS_RE.fullmatch(name))


def _task_batches(tasks: list[dict[str, Any]], batch_size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(tasks), batch_size):
        yield tasks[start : start + batch_size]


def save_reviewed_word(
    db_path: str | Path,
    normalized_word: str,
    zamanalif_dsl: str,
    origin: str,
) -> None:
    """Store one human-approved word conversion in the shared SQLite dictionary."""
    normalized = normalize_word(normalized_word)
    if not normalized or normalized != normalized_word:
        raise ValueError("normalized_word must be a lowercase normalized Cyrillic word")
    if origin not in {"N", "RL", "U"}:
        raise ValueError("origin must be one of: N, RL, U")
    try:
        parse_dsl(zamanalif_dsl)
    except DslError as exc:
        raise ValueError(f"invalid reviewed Zamanalif DSL: {exc}") from exc

    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(db_path)) as conn, conn:
        ensure_review_state_schema(conn)
        conn.execute(
            """
            insert into reviewed_words(normalized_word, zamanalif_dsl, origin, updated_at)
            values (?, ?, ?, ?)
            on conflict(normalized_word) do update set
                zamanalif_dsl=excluded.zamanalif_dsl,
                origin=excluded.origin,
                updated_at=excluded.updated_at
            """,
            (normalized, zamanalif_dsl, origin, now),
        )
        conn.execute(
            "delete from reviewed_word_variants where normalized_word = ?",
            (normalized,),
        )


def load_reviewed_words(db_path: str | Path) -> dict[str, ReviewedWord]:
    """Load the human-approved word dictionary keyed by normalized Cyrillic form."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        ensure_review_state_schema(conn)
        rows = conn.execute(
            """
            select normalized_word, zamanalif_dsl, origin
            from reviewed_words
            order by normalized_word
            """
        ).fetchall()
        variant_rows = conn.execute(
            """
            select normalized_word, position, zamanalif, policies_json
            from reviewed_word_variants
            order by normalized_word, position
            """
        ).fetchall()
    variants_by_word: dict[str, list[ReviewedVariant]] = {}
    for word, _, zamanalif, policies_json in variant_rows:
        raw_policies = json.loads(policies_json)
        policies = tuple(
            tuple((str(rule), str(option)) for rule, option in policy.items())
            for policy in raw_policies
        )
        variants_by_word.setdefault(str(word), []).append(
            ReviewedVariant(str(zamanalif), policies)
        )
    return {
        row[0]: ReviewedWord(
            normalized_word=row[0],
            zamanalif_dsl=row[1],
            origin=row[2],
            variants=tuple(variants_by_word.get(row[0], ())),
        )
        for row in rows
    }


def _display_word(surface: str, normalized: str) -> str:
    stripped = surface.strip()
    if len(stripped) >= 2 and stripped.isupper():
        return stripped
    return normalized


def _origin_prediction(label: str) -> str:
    return {
        "N": "native",
        "RL": "loanword",
        "U": "unknown",
    }.get(label, "unknown")


def _char_conversion(char: str, word: str, index: int, label: str) -> str:
    if char == "-":
        return "-"
    if char in APOSTROPHE_VARIANTS:
        return ZAMANALIF_APOSTROPHE
    if char in CONDITIONAL_LETTERS:
        return _conditional_char_conversion(char, word, index, label)
    if label == "RL" and char == "ы":
        return _loanword_y_conversion(word)
    if label == "RL" and char in {"ь", "ъ"}:
        if index + 1 < len(word) and word[index + 1] == "е":
            return ""
        return ZAMANALIF_APOSTROPHE
    return _deterministic_char(char)


def _convert_known_label(word: str, label: str) -> str:
    if "-" in word:
        parts = word.split("-")
        part_labels = [_guess_hyphen_part_label(part, label) for part in parts]
        if all(part_label == label for part_label in part_labels):
            return _convert_known_label_without_hyphen(word, label)
        return "-".join(
            _convert_known_label_without_hyphen(part, part_label)
            if part
            else ""
            for part, part_label in zip(parts, part_labels, strict=True)
        )
    return _convert_known_label_without_hyphen(word, label)


def _guess_hyphen_part_label(part: str, parent_label: str) -> str:
    if any(char in TATAR_SPECIFIC_PART_LETTERS for char in part.casefold()):
        return "N"
    return parent_label


def _convert_known_label_without_hyphen(word: str, label: str) -> str:
    converted: list[str] = []
    index = 0
    while index < len(word):
        char = word[index]
        loanword_ets_conversion = _loanword_final_ets_sequence_conversion(word, index, label)
        surname_conversion = _surname_sequence_conversion(word, index)
        if loanword_ets_conversion is not None:
            latin, consumed = loanword_ets_conversion
            converted.append(latin)
            index += consumed - 1
        elif surname_conversion is not None:
            latin, consumed = surname_conversion
            converted.append(latin)
            index += consumed - 1
        elif label == "N" and char in {"г", "к"} and _next_char(word, index) == "ъ":
            converted.append("ğ" if char == "г" else "q")
            index += 1
        elif char == "ц" and index + 1 < len(word) and word[index + 1] == "ц":
            while index + 1 < len(word) and word[index + 1] == "ц":
                index += 1
            converted.append("ts")
        else:
            converted.append(_char_conversion(char, word, index, label))
        index += 1
    output = "".join(converted)
    if word.casefold().startswith("металл") and output.startswith("metall"):
        output = "metal" + output[len("metall") :]
    if label == "N":
        output = _apply_native_lexical_conventions(word, output)
    if label == "RL":
        output = _apply_loanword_lexical_conventions(word, output)
    return _apply_verified_hamza_lexical_convention(word, output, label)


def _apply_verified_hamza_lexical_convention(
    word: str,
    converted: str,
    label: str,
) -> str:
    folded = word.casefold()
    for cyrillic, native_plain, preserved in NATIVE_HAMZA_FAMILIES:
        if not folded.startswith(cyrillic):
            continue
        branch_prefix = native_plain if label == "N" else LOANWORD_HAMZA_PREFIXES[cyrillic]
        if converted.startswith(preserved):
            return converted
        if converted.startswith(branch_prefix):
            return preserved + converted[len(branch_prefix) :]
        return converted
    return converted


def _apply_loanword_lexical_conventions(word: str, converted: str) -> str:
    folded = word.casefold()
    for cyrillic_prefix, plain_text, expected_text in LOANWORD_MIXED_SUFFIX_REPLACEMENTS:
        if not folded.startswith(cyrillic_prefix) or not converted.startswith(plain_text):
            continue
        return expected_text + converted[len(plain_text) :]
    if folded.startswith("интриг") and converted.startswith("intriğ"):
        return "intrig" + converted[len("intriğ") :]
    if folded.startswith("боулинг") and converted.startswith("bouling"):
        return "bowling" + converted[len("bouling") :]
    if folded.endswith("лау") and converted.endswith("lau"):
        return converted[:-3] + "law"
    if folded.endswith("ләү") and converted.endswith("läü"):
        return converted[:-3] + "läw"
    return converted


LOANWORD_MIXED_SUFFIX_REPLACEMENTS: tuple[tuple[str, str, str], ...] = (
    ("закончалык", "zakonçalık", "zakonçalıq"),
)


def _loanword_final_ets_sequence_conversion(
    word: str, index: int, label: str
) -> tuple[str, int] | None:
    if label != "RL":
        return None
    suffix = word[index:]
    if suffix == "еец":
        return "eyets", 3
    if suffix == "ец":
        return "ets", 2
    return None


NATIVE_PREFIX_REPLACEMENTS: tuple[tuple[str, str, str], ...] = (
    *NATIVE_HAMZA_FAMILIES,
    ("аек", "ayık", "ayıq"),
    ("беркай", "berkay", "berqay"),
    ("беркая", "berkaya", "berqaya"),
    ("беркат", "berkat", "berqat"),
    ("берникадәр", "bernikadär", "berniqadär"),
    ("боек", "boyık", "boyıq"),
    ("вазифа", "wazifa", "wazıyfa"),
    ("вәкаләт", "wäkalät", "wäqalät"),
    ("гадел", "ğadel", "ğädel"),
    ("гадәт", "ğadät", "ğädät"),
    ("гаеп", "ğayıp", "ğäyep"),
    ("гая", "ğaya", "ğäyä"),
    ("гаск", "ğasq", "ğäsk"),
    ("гаҗ", "ğac", "ğäc"),
    ("гамә", "ğamä", "ğämä"),
    ("гарә", "ğarä", "ğärä"),
    ("гарип", "ğarip", "ğärip"),
    ("гарь", "ğar", "ğär"),
    ("гаск", "ğask", "ğäsk"),
    ("галәм", "ğaläm", "ğäläm"),
    ("гамь", "ğam", "ğäm"),
    ("ганимәт", "ğanimät", "ğänimät"),
    ("газиз", "ğaziz", "ğäziz"),
    ("гаилә", "ğailä", "ğäilä"),
    ("гай", "ğay", "ğäy"),
    ("гәлим", "gälim", "ğälim"),
    ("гәрәп", "gäräp", "ğäräp"),
    ("гилем", "gilem", "ğilem"),
    ("гилм", "gilm", "ğilm"),
    ("дикъкать", "diqkat", "diqqat"),
    ("инкыйлаб", "inkıylab", "inqıylab"),
    ("инкар", "inkar", "inqar"),
    ("иҗтимагый", "ictimağıy", "ictimaği"),
    ("игътибар", "iğtibar", "iğtibar"),
    ("каек", "qayık", "qayıq"),
    ("каракүл", "qaraqül", "qarakül"),
    ("киная", "kinaya", "kinayä"),
    ("кыек", "qıyık", "qıyıq"),
    ("кыяфәт", "qıyafät", "qıyäfät"),
    ("лаек", "layık", "layıq"),
    ("мыек", "mıyık", "mıyıq"),
    ("мөкатдәс", "mökatdäs", "möqatdäs"),
    ("мәкал", "mäkal", "mäqal"),
    ("мәкәлә", "mäkälä", "mäqälä"),
    ("мәхкүл", "mäxkül", "mäxqül"),
    ("мәгъсум", "mäğsum", "mäğsüm"),
    ("мәгариф", "mäğarif", "mäğärif"),
    ("мәшәкать", "mäşäkat", "mäşäqat"),
    ("мөгаллим", "möğallim", "möğällim"),
    ("мөгамәлә", "möğamälä", "möğämälä"),
    ("нәкыш", "näkış", "näqış"),
    ("оек", "oyık", "oyıq"),
    ("оеш", "oyışk", "oyışq"),
    ("пәйгамбәр", "päyğambär", "päyğämbär"),
    ("сыек", "sıyık", "sıyıq"),
    ("сөякк", "söyäqq", "söyäkk"),
    ("сөяк", "söyäq", "söyäk"),
    ("сәркатип", "särkatip", "särqatip"),
    ("тәкать", "täkat", "täqat"),
    ("тәрәккый", "täräkqıy", "täräqqıy"),
    ("тәнкыйть", "tänkıyt", "tänqıyt"),
    ("тәшрик", "täşrik", "täşriq"),
    ("вакиф", "wakif", "waqif"),
    ("фәкать", "fäkat", "fäqat"),
    ("фәкыйрь", "fäkıyr", "fäqıyr"),
    ("хыянәт", "xıyanät", "xıyänät"),
    ("әхлак", "äxlak", "äxlaq"),
    ("югыйсә", "yuğıysä", "yuğisä"),
    ("шәфкать", "şäfkat", "şäfqat"),
    ("нигмәт", "nigmät", "niğmät"),
    ("нәгим", "nägim", "näğim"),
    ("кәбәхәт", "käbäxät", "qäbäxät"),
    ("кәдер", "käder", "qäder"),
    ("кәдими", "kädimi", "qädimi"),
    ("кәдәр", "kädär", "qädär"),
    ("көдрәт", "ködrät", "qödrät"),
    ("һичкая", "hiçkaya", "hiçqaya"),
    ("һичкай", "hiçkay", "hiçqay"),
    ("һәркай", "härkay", "härqay"),
    ("юкә", "yüqä", "yükä"),
)

NATIVE_FRAGMENT_REPLACEMENTS: tuple[tuple[str, str, str], ...] = (
    ("сөякк", "söyäqq", "söyäkk"),
    ("сөяк", "söyäq", "söyäk"),
    ("төяк", "töyäq", "töyäk"),
    ("гүяки", "güyäqi", "güyäki"),
    ("өянке", "öyänqe", "öyänke"),
    ("мияу", "miäw", "miyaw"),
    ("җилкуар", "cilkuar", "cilquar"),
    ("гыйбад", "ğıybad", "ğibäd"),
    ("гыйбар", "ğıybar", "ğibär"),
    ("гыйльм", "ğıylm", "ğilm"),
    ("зәгыйф", "zäğıyf", "zäğif"),
    ("шагыйр", "şağıyr", "şağir"),
    ("шигри", "şigri", "şiğri"),
    ("кагыйдә", "qağıydä", "qağidä"),
    ("табигый", "tabiğıy", "tabiği"),
    ("васыять", "wasıyat", "wasıyät"),
    ("елан", "elan", "yılan"),
    ("итагать", "itağat", "itağät"),
    ("канәгать", "qanäğat", "qanäğät"),
    ("риваять", "riwayat", "riwayät"),
    ("сәгать", "säğat", "säğät"),
    ("сәгәт", "sägät", "säğät"),
    ("сәнгат", "sänğat", "sänğät"),
    ("табигать", "tabiğat", "tabiğät"),
    ("җәмгыят", "cämğıyat", "cämğiyät"),
    ("җинаять", "cinayat", "cinayät"),
    ("җәмәгать", "cämäğat", "cämäğät"),
    ("мөрәҗәгать", "möräcäğat", "möräcäğät"),
)


def _apply_native_lexical_conventions(word: str, converted: str) -> str:
    folded = word.casefold()
    for cyrillic_prefix, plain_text, expected_text in NATIVE_PREFIX_REPLACEMENTS:
        if not folded.startswith(cyrillic_prefix) or not converted.startswith(plain_text):
            continue
        return _apply_native_surname_suffix_conventions(
            folded,
            expected_text + converted[len(plain_text) :],
        )
    for cyrillic_fragment, plain_text, expected_text in NATIVE_FRAGMENT_REPLACEMENTS:
        if cyrillic_fragment not in folded or plain_text not in converted:
            continue
        return _apply_native_surname_suffix_conventions(
            folded,
            converted.replace(plain_text, expected_text, 1),
        )
    return _apply_native_surname_suffix_conventions(folded, converted)


def _apply_native_surname_suffix_conventions(folded: str, converted: str) -> str:
    if folded.endswith("ова") and converted.endswith("owa"):
        return converted[:-3] + "ova"
    if folded.endswith("ов") and converted.endswith("ow"):
        return converted[:-2] + "ov"
    if folded.endswith("ева") and converted.endswith("ewa"):
        return converted[:-3] + "eva"
    if folded.endswith("ев") and converted.endswith("ew"):
        return converted[:-2] + "ev"
    return converted


def _next_char(word: str, index: int) -> str:
    return word[index + 1] if index + 1 < len(word) else ""


def _surname_sequence_conversion(word: str, index: int) -> tuple[str, int] | None:
    for cyrillic, latin in (
        ("иева", "ieva"),
        ("әева", "äyeva"),
        ("иев", "iev"),
        ("әев", "äyev"),
    ):
        if word.startswith(cyrillic, index):
            return latin, len(cyrillic)
    return None


def _loanword_y_conversion(word: str) -> str:
    if _uses_long_loanword_y(word):
        return "ıy"
    return "ı"


def _uses_long_loanword_y(word: str) -> bool:
    return word.startswith(("музы", "посыл", "выш", "сыр"))


def _conditional_char_conversion(char: str, word: str, index: int, label: str) -> str:
    if label == "RL":
        return _loanword_conditional_char(char, word, index)
    if label == "N":
        return _native_conditional_char(char, word, index)
    loan = _loanword_conditional_char(char, word, index)
    native = _native_conditional_char(char, word, index)
    return loan if loan == native else ""


def _loanword_conditional_char(char: str, word: str, index: int) -> str:
    if char == "е":
        return _e_conversion(word, index, "RL")
    if char == "г":
        return _loanword_suffix_gk_conversion(char, word, index) or "g"
    if char == "к":
        return _loanword_suffix_gk_conversion(char, word, index) or "k"
    if char == "я":
        return _ya_conversion(word, index, "RL")
    return {
        "в": "v",
        "ю": "yu",
        "у": "u",
        "ү": "ü",
        "ц": _ts_conversion(word, index),
    }.get(char, "")


def _loanword_suffix_gk_conversion(char: str, word: str, index: int) -> str:
    suffix = word[index:]
    prefix = word[:index]
    if char == "г" and suffix in {
        "га",
        "гә",
        "ларга",
        "ләргә",
    } and len(prefix) >= 5:
        return "ğ" if suffix in {"га", "ларга"} else "g"
    if char == "г" and suffix in {"ган", "гән", "гын", "ген"} and len(prefix) >= 5:
        return "ğ" if suffix in {"ган", "гын"} else "g"
    if char == "г" and suffix in {"гы", "ге"} and word.endswith(("лыгы", "леге")):
        stem = word[:-4]
        if len(stem) >= 4:
            return "ğ" if suffix == "гы" else "g"
    if char == "г" and index == len(word) - 2 and word.endswith(
        ("дагы", "дәге", "тагы", "тәге")
    ):
        stem = word[: -4]
        if len(stem) >= 5:
            return "ğ" if word.endswith(("дагы", "тагы")) else "g"
    if char == "к" and suffix in {
        "ка",
        "кә",
    } and len(prefix) >= 5:
        if prefix.endswith("л"):
            return ""
        return "q" if suffix.startswith(("ка", "лык")) else "k"
    if char == "к" and index == len(word) - 1 and word.endswith(
        ("лык", "лек", "лыкка", "леккә", "лыгын", "леген")
    ):
        stem = word[: -3]
        if len(stem) >= 4:
            return "q" if word.endswith(("лык", "лыкка", "лыгын")) else "k"
    return ""


def _native_conditional_char(char: str, word: str, index: int) -> str:
    harmony = vowel_harmony_class(word)
    if char == "в":
        return "w"
    if char == "г":
        right_context = _right_vowel_context(word, index)
        if right_context == "front":
            return "g"
        if right_context == "back":
            return "ğ"
        context = _local_vowel_context(word, index)
        if context == "front":
            return "g"
        if context == "back":
            return "ğ"
        return "g" if harmony == "front_only" else "ğ" if harmony == "back_only" else ""
    if char == "к":
        suffix = word[index:]
        if suffix.startswith("кыч"):
            return "q"
        if suffix.startswith("кеч"):
            return "k"
        right_context = _right_vowel_context(word, index)
        if right_context == "front":
            return "k"
        if right_context == "back":
            return "q"
        context = _local_vowel_context(word, index)
        if context == "front":
            return "k"
        if context == "back":
            return "q"
        return "k" if harmony == "front_only" else "q" if harmony == "back_only" else ""
    if char == "у":
        if index > 0 and word[index - 1] in {"а", "ә", "я"}:
            return "w"
        return "u"
    if char == "ү":
        if index > 0 and word[index - 1] in {"а", "ә", "я"}:
            return "w"
        return "ü"
    if char == "я":
        return _ya_conversion(word, index, "N")
    if char == "ю":
        if word == "ю":
            return "yü"
        if index > 0 and word[index - 1] == "и":
            return "yü"
        context = _local_vowel_context(word, index)
        if context == "front":
            return "yü"
        if context == "back":
            return "yu"
        if harmony == "no_vowels":
            return "yu"
        return "yü" if harmony == "front_only" else "yu" if harmony == "back_only" else ""
    if char == "е":
        return _e_conversion(word, index, "N")
    if char == "ц":
        return _ts_conversion(word, index)
    return ""


def _ts_conversion(word: str, index: int) -> str:
    if index == len(word) - 1:
        return "s"
    if index > 0 and word[index - 1] in FRONT_VOWELS | BACK_VOWELS | {"е", "ё", "ю", "я"}:
        return "ts"
    return "s"


def _ya_conversion(word: str, index: int, label: str) -> str:
    previous = word[index - 1] if index > 0 else ""
    if previous == "и":
        return "ä"
    if previous in {"ь", "ъ"}:
        return "ya"
    if label == "RL" and index > 0:
        return "ya"
    if index == 0:
        if word == "я" or word.startswith("яшь"):
            return "yä"
        if word == "ящик":
            return "ya"

    context = _local_vowel_context(word, index)
    if context == "front":
        return "yä"
    if context == "back":
        return "ya"

    harmony = _vowel_harmony_without_index(word, index)
    if harmony == "front_only":
        return "yä"
    if harmony == "back_only":
        return "ya"
    if harmony == "no_vowels":
        return "ya"
    return ""


def _e_conversion(word: str, index: int, label: str) -> str:
    previous = word[index - 1] if index > 0 else ""
    if previous in {"и", "ү"}:
        return "e"
    if previous in {"ь", "ъ"}:
        if label == "N" and word[index:].startswith("ел"):
            return "yı"
        return "ye"
    if label == "RL":
        if index == 0:
            return _initial_e_conversion(word, index)
        if previous == "ә" and index + 1 < len(word) and word[index + 1] == "в":
            return "ye"
        if previous == "у":
            return "e"
        if previous in BACK_VOWELS and index == len(word) - 1:
            return "yı"
        if previous in FRONT_VOWELS | BACK_VOWELS:
            return "ye"
        return "e"
    if index == 0:
        return _initial_e_conversion(word, index)
    if previous in FRONT_VOWELS | BACK_VOWELS:
        return _native_vowel_e_conversion(previous)
    return "e"


def _native_vowel_e_conversion(previous: str) -> str:
    return {
        "а": "yı",
        "о": "yı",
        "у": "yı",
        "ы": "yı",
        "ә": "ye",
        "ө": "ye",
    }.get(previous, "e")


def _initial_e_conversion(word: str, index: int) -> str:
    if word.startswith(("европ", "евраз", "епископ", "ефәк")):
        return "ye"
    harmony = _vowel_harmony_without_index(word, index)
    if harmony == "front_only":
        return "ye"
    if harmony in {"back_only", "no_vowels"}:
        return "yı"
    return ""


def _vowel_harmony_without_index(word: str, index: int) -> str:
    front_vowels = FRONT_VOWELS | {"э"}
    back_vowels = BACK_VOWELS
    has_front = any(i != index and char in front_vowels for i, char in enumerate(word))
    has_back = any(i != index and char in back_vowels for i, char in enumerate(word))
    if has_front and has_back:
        return "mixed_front_back"
    if has_front:
        return "front_only"
    if has_back:
        return "back_only"
    return "no_vowels"


def _local_vowel_context(word: str, index: int) -> str:
    local_front_vowels = FRONT_VOWELS | {"э"}
    local_back_vowels = BACK_VOWELS | {"ю", "я"}
    for char in reversed(word[:index]):
        if char == "-":
            break
        if char in local_front_vowels:
            return "front"
        if char in local_back_vowels:
            return "back"
    for char in word[index + 1 :]:
        if char == "-":
            break
        if char in local_front_vowels:
            return "front"
        if char in local_back_vowels:
            return "back"
    return ""


def _right_vowel_context(word: str, index: int) -> str:
    next_char = word[index + 1] if index + 1 < len(word) else ""
    if next_char in FRONT_VOWELS | {"э"}:
        return "front"
    if next_char in BACK_VOWELS | {"я"}:
        return "back"
    return ""


def _native_uw_insertion_needs_choice(word: str, index: int) -> bool:
    char = word[index]
    next_char = _next_char(word, index)
    if char == "у":
        return next_char in (FRONT_VOWELS | BACK_VOWELS) - {"е"}
    if char == "ү":
        return next_char == "е" or next_char in (FRONT_VOWELS | BACK_VOWELS) - {"е"}
    if char == "ю":
        return next_char in FRONT_VOWELS | BACK_VOWELS
    return False


def _deterministic_char(char: str) -> str:
    return {
        "а": "a",
        "ә": "ä",
        "о": "o",
        "ө": "ö",
        "ы": "ı",
        "э": "e",
        "и": "i",
        "б": "b",
        "җ": "c",
        "ч": "ç",
        "д": "d",
        "ф": "f",
        "һ": "h",
        "ж": "j",
        "л": "l",
        "м": "m",
        "н": "n",
        "ң": "ñ",
        "п": "p",
        "р": "r",
        "с": "s",
        "ш": "ş",
        "т": "t",
        "х": "x",
        "й": "y",
        "з": "z",
        "ё": "yo",
        "щ": "şç",
        "ь": "",
        "ъ": "",
    }.get(char, "")


def _best_effort_unknown(word: str) -> str:
    return "".join(_char_conversion(char, word, index, "U") for index, char in enumerate(word))


def _is_clean_zamanalif(value: str | None) -> bool:
    if not value:
        return False
    value = normalize_zamanalif_apostrophes(value)
    return all(char in ALLOWED_ZAMANALIF for char in value)


def ensure_review_state_schema(conn: sqlite3.Connection) -> None:
    """Create SQLite tables used by word-review export and import."""
    conn.execute("drop table if exists exported_words")
    conn.execute(
        """
        create table if not exists reviewed_words (
            normalized_word text primary key,
            zamanalif_dsl text not null,
            origin text not null check(origin in ('N', 'RL', 'U')),
            updated_at text not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists reviewed_word_derivations (
            normalized_word text primary key,
            source_word text not null,
            lemma text not null,
            part_of_speech text not null,
            analyzer_revision text not null,
            created_at text not null,
            foreign key(normalized_word) references reviewed_words(normalized_word),
            foreign key(source_word) references reviewed_words(normalized_word)
        )
        """
    )
    conn.execute(
        """
        create table if not exists reviewed_word_variants (
            normalized_word text not null,
            position integer not null,
            zamanalif text not null,
            policies_json text not null,
            updated_at text not null,
            primary key(normalized_word, position),
            foreign key(normalized_word) references reviewed_words(normalized_word)
        )
        """
    )


def _report(
    *,
    total_sentences: int,
    total_tokens: int,
    unique_word_forms: int,
    exported: list[WordStats],
    mixed_harmony_n_skipped: int,
    reviewed_words_skipped: int,
    homonym_occurrences_skipped: int,
    homonym_words_skipped: int,
    decision_counts: Counter[str],
) -> dict[str, Any]:
    conditional_counts = Counter()
    dsl_rule_occurrence_count = 0
    for entry in exported:
        conditional_counts.update(entry.conditional_letters)
        result = conversion_result_for_annotation(entry.normalized, entry.label)
        if result is not None:
            dsl_rule_occurrence_count += len(result.rule_ids)
    return {
        "total_input_sentences": total_sentences,
        "total_tokens": total_tokens,
        "unique_word_forms": unique_word_forms,
        "exported_word_count": len(exported),
        "count_by_conditional_letter": dict(sorted(conditional_counts.items())),
        "rl_exported_word_count": sum(entry.label == "RL" for entry in exported),
        "u_exported_word_count": sum(entry.label == "U" for entry in exported),
        "mixed_harmony_n_word_skipped_count": mixed_harmony_n_skipped,
        "reviewed_words_skipped_count": reviewed_words_skipped,
        "homonym_occurrences_skipped_count": homonym_occurrences_skipped,
        "homonym_words_deferred_count": homonym_words_skipped,
        "origin_independent_word_count": decision_counts["origin_independent"],
        "origin_dependent_word_count": decision_counts["origin_dependent"],
        "unconvertible_word_count": decision_counts["unconvertible"],
        "dsl_rule_occurrence_count": dsl_rule_occurrence_count,
        "top_50_exported_words_by_frequency": [
            {"word": entry.normalized, "frequency": entry.frequency}
            for entry in sorted(exported, key=lambda item: (-item.frequency, item.normalized))[:50]
        ],
    }


def _ordered_project_keys(projects: dict[str, list[dict[str, Any]]]) -> list[str]:
    priority = [
        "contextual_homonym",
        "complex_multi_rule",
        *(_project_key_for_rule(rule_id) for rule_id in RULES),
        "u_hyphenated",
        "u_abbrev_fragment",
        "u_tatar_specific",
        "u_conditional_plain",
        "u_other",
        "catchall",
    ]
    known = [key for key in priority if key in projects]
    unknown = sorted(key for key in projects if key not in set(priority))
    return known + unknown


def _project_key_for_rule(rule_id: str) -> str:
    return rule_id.lower()


def project_title_for_key(project_key: str) -> str:
    if project_key == "contextual_homonym":
        return "Contextual homonyms"
    if project_key == "hamza":
        return "Hamza review"
    if project_key == "complex_multi_rule":
        return "Complex multi-rule words"
    if project_key == "u_hyphenated":
        return "Unknown hyphenated compounds"
    if project_key == "u_abbrev_fragment":
        return "Unknown abbreviations and fragments"
    if project_key == "u_tatar_specific":
        return "Unknown Tatar-specific words"
    if project_key == "u_conditional_plain":
        return "Unknown conditional-letter words"
    if project_key == "u_other":
        return "Other unknown-origin words"
    if project_key == "catchall":
        return "Catchall word review"
    return project_key.upper().replace("_", " ")


def dictionary_project_keys() -> set[str]:
    """Return every strict project key produced by dictionary split export."""
    return {
        "complex_multi_rule",
        *(_project_key_for_rule(rule_id) for rule_id in RULES),
        "u_hyphenated",
        "u_abbrev_fragment",
        "u_tatar_specific",
        "u_conditional_plain",
        "u_other",
        "catchall",
    }


def _project_report(
    project_key: str,
    tasks: list[dict[str, Any]],
    words: list[str],
    *,
    covered_word_count: int,
) -> dict[str, Any]:
    rule_counts: Counter[str] = Counter()
    for task, word in zip(tasks, words, strict=True):
        rule_counts.update(
            classify_project(word, task["data"]["gemini_origin"])["dsl_rules"]
        )
    return {
        "project_key": project_key,
        "project_title": project_title_for_key(project_key),
        "exported_word_count": len(tasks),
        "covered_word_count": covered_word_count,
        "dsl_rule_counts": dict(sorted(rule_counts.items())),
        "exported_words": words,
    }


def _split_report(
    result: ExportResult,
    projects: dict[str, ExportResult],
    *,
    covered_word_count: int,
    analyzer_revision: str,
) -> dict[str, Any]:
    return {
        "exported_word_count": sum(len(project.tasks) for project in projects.values()),
        "covered_word_count": covered_word_count,
        "morphology_analyzer_revision": analyzer_revision,
        "project_count": len(projects),
        "projects": [
            {
                "project_key": key,
                "project_title": project.report["project_title"],
                "exported_word_count": len(project.tasks),
                "covered_word_count": project.report["covered_word_count"],
            }
            for key, project in projects.items()
        ],
        "base_report": result.report,
    }
