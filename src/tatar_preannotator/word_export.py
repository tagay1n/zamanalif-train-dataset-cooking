from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from html import escape
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from typing import Any, Iterable

from tatar_preannotator.labelstudio_instructions import render_project_instructions
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

LABELSTUDIO_SPLIT_BATCH_SIZE = 1000
MANAGED_BATCH_RE = re.compile(
    r"^project_[a-z0-9_]+(?:_batch_\d{3}_of_\d{3})?\.json$"
)
MANAGED_INSTRUCTIONS_RE = re.compile(r"^project_[a-z0-9_]+_instructions\.html$")
CYRILLIC_RE = re.compile(r"[А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]")
TATAR_SPECIFIC_PART_LETTERS = frozenset("әөүҗңһ")
RL_REVIEW_LETTERS = frozenset("ёыьъщ")
ALLOWED_ZAMANALIF = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "äÄöÖüÜñÑıİğĞşŞçÇ"
    f"-—{ZAMANALIF_APOSTROPHE}"
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


@dataclass(frozen=True)
class SplitExportResult:
    projects: dict[str, ExportResult]
    report: dict[str, Any]
    exported_words: list[str]


@dataclass(frozen=True)
class ReviewedWord:
    normalized_word: str
    zamanalif_dsl: str
    origin: str


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
    return token[matches[0].start() : matches[-1].end()].lower()


def contains_conditional_letter(word: str) -> bool:
    """Return true when a normalized word contains a conditional Cyrillic letter."""
    return any(char in CONDITIONAL_LETTERS for char in word)


def contains_rl_review_letter(word: str) -> bool:
    """Return true when a loanword has a non-deterministic review letter."""
    return any(char in CONDITIONAL_LETTERS or char in RL_REVIEW_LETTERS for char in word)


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
    already_exported: set[str] | None = None,
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
        already_exported=already_exported,
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
    already_exported: set[str] | None = None,
    reviewed_words: set[str] | None = None,
    word_resolutions: dict[str, str] | None = None,
) -> SplitExportResult:
    """Build focused Label Studio word-review project tasks from SQLite rows."""
    base = export_labelstudio_tasks_from_db(
        db_path,
        max_items=max_items,
        include_rl=include_rl,
        include_unknown=include_unknown,
        min_frequency=min_frequency,
        sort_by=sort_by,
        already_exported=already_exported,
        reviewed_words=reviewed_words,
        word_resolutions=word_resolutions,
    )
    return split_export_result(base)


def split_export_result(result: ExportResult) -> SplitExportResult:
    """Split one export result into focused project buckets."""
    grouped_tasks: dict[str, list[dict[str, Any]]] = {}
    grouped_words: dict[str, list[str]] = {}
    for task, normalized in zip(result.tasks, result.exported_words, strict=True):
        data = task.get("data", {})
        label = data.get("gemini_origin", "U")
        project = classify_project(normalized, label if isinstance(label, str) else "U")
        grouped_tasks.setdefault(project["key"], []).append(_task_with_project_data(task, project))
        grouped_words.setdefault(project["key"], []).append(normalized)

    projects: dict[str, ExportResult] = {}
    for project_key in _ordered_project_keys(grouped_tasks):
        tasks = grouped_tasks[project_key]
        words = grouped_words[project_key]
        projects[project_key] = ExportResult(
            tasks=tasks,
            report=_project_report(project_key, tasks, words),
            exported_words=words,
        )

    return SplitExportResult(
        projects=projects,
        report=_split_report(result, projects),
        exported_words=result.exported_words,
    )


def classify_project(word: str, label: str) -> dict[str, Any]:
    """Return the focused Label Studio project metadata for one normalized word."""
    if label == "U":
        key = _u_project_key(word)
        return {"key": key, "title": _project_title_for_key(key), "dsl_rules": []}
    result = conversion_result_for_annotation(word, label)
    rules = list(dict.fromkeys(result.rule_ids)) if result is not None else []
    if len(rules) > 1:
        key = "complex_multi_rule"
        title = "Complex multi-rule words"
    elif len(rules) == 1:
        key = _project_key_for_rule(rules[0])
        title = _project_title_for_key(key)
    else:
        key = "catchall"
        title = "Catchall word review"
    return {"key": key, "title": title, "dsl_rules": rules}


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
    already_exported: set[str] | None,
    reviewed_words: set[str],
    word_resolutions: dict[str, str],
) -> ExportResult:
    stats: dict[str, WordStats] = {}
    total_sentences = 0
    total_tokens = 0
    homonym_words: set[str] = set()
    word_occurrences: Counter[str] = Counter()
    already_exported = already_exported or set()

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
            entry.label_counts[effective_label] += 1
            entry.conditional_letters.update(
                char for char in normalized if char in CONDITIONAL_LETTERS
            )

    decision_counts: Counter[str] = Counter()
    candidates: list[WordStats] = []
    mixed_harmony_n_skipped = 0
    already_exported_skipped = 0
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
        if entry.normalized in already_exported:
            already_exported_skipped += 1
            continue
        if entry.label == "RL" and not include_rl:
            continue
        if entry.label == "U" and not include_unknown:
            continue
        if entry.label == "N" and vowel_harmony_class(entry.normalized) == "mixed_front_back":
            mixed_harmony_n_skipped += 1
            continue
        if branches.state == "origin_independent":
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
                "id": f"word_{index:06d}",
                "cyrl_word": entry.display,
                "auto_zamanalif": conversion_branches(entry.normalized).suggestion(entry.label),
                "gemini_origin": entry.label,
                "hints_html": decision_html(entry),
            }
        }
        for index, entry in enumerate(candidates, start=1)
    ]
    return ExportResult(
        tasks=tasks,
        report=_report(
            total_sentences=total_sentences,
            total_tokens=total_tokens,
            unique_word_forms=len(stats),
            exported=candidates,
            mixed_harmony_n_skipped=mixed_harmony_n_skipped,
            already_exported_skipped=already_exported_skipped,
            reviewed_words_skipped=reviewed_words_skipped,
            homonym_occurrences_skipped=sum(
                word_occurrences[word] for word in homonym_words
            ),
            homonym_words_skipped=len(homonym_words),
            decision_counts=decision_counts,
        ),
        exported_words=[entry.normalized for entry in candidates],
    )


def _task_with_project_data(task: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    data = dict(task["data"])
    data.update(
        {
            "project_key": project["key"],
            "project_title": project["title"],
            "dsl_rules": project["dsl_rules"],
        }
    )
    return {"data": data}


def _sqlite_records(db_path: str | Path) -> Iterable[dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as conn:
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
        ).fetchall()
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
    result = result_with_qoran_hamza_choices(word, result)
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


def result_with_qoran_hamza_choices(source: str, result: ConversionResult) -> ConversionResult:
    """Annotate ``коръән`` as hamza-preserving vs hamza-omitting policy."""
    if not source.casefold().startswith("коръән"):
        return result

    segments: list[Literal | Choice] = []
    changed = False
    for segment in _merge_adjacent_literals(result).segments:
        if isinstance(segment, Choice):
            segments.append(segment)
            continue
        text = segment.text
        if not changed and text.startswith("qorän"):
            _append_literal_segment(segments, "qor")
            segments.append(Choice(HAMZA_RULE.rule_id, HAMZA_RULE.options))
            _append_literal_segment(segments, text[len("qor") :])
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
    """Build compact vertical conversion-decision HTML for Label Studio."""
    items: list[str] = []
    result = conversion_result_for_annotation(entry.normalized, entry.label)
    if result is not None and "IYA" in result.rule_ids:
        items.append("<b>ия</b> -> <b>iä</b> or <b>iyä</b> (<b>IYA</b>)")
    for index, char in enumerate(entry.normalized):
        if not CYRILLIC_RE.fullmatch(char):
            continue
        if char in CONDITIONAL_LETTERS:
            items.append(_conditional_decision(char, entry.normalized, index, entry.label))
        else:
            converted = _char_conversion(char, entry.normalized, index, entry.label)
            if converted:
                items.append(f"<b>{escape(char)}</b> -> <b>{escape(converted)}</b>")
    items.append(f"Gemini's origin prediction: <b>{_origin_prediction(entry.label)}</b>")
    if result is None:
        items.append("Automatic converter produced no clean Latin suggestion")
    items.append(
        f"Frequency for <b><i>{escape(entry.normalized)}</i></b>: <b>{entry.frequency}</b>"
    )
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def write_outputs(result: ExportResult, output_path: str | Path) -> Path:
    """Write a Label Studio JSON import file and return its path."""
    validate_export_result(result)
    output = Path(output_path)
    with TemporaryDirectory(prefix=f".{output.name}.staging-", dir=output.parent) as tmpdir:
        staged = Path(tmpdir) / output.name
        staged.write_text(_json_text(result.tasks), encoding="utf-8")
        _validate_serialized_single_output(staged, result)
        staged.replace(output)
    return output


def write_split_outputs(result: SplitExportResult, output_dir: str | Path) -> list[Path]:
    """Write split Label Studio project JSON import files and return their paths."""
    validate_split_export_result(result)
    root = Path(output_dir)
    root.parent.mkdir(parents=True, exist_ok=True)
    output_names: list[str] = []
    with TemporaryDirectory(prefix=f".{root.name}.staging-", dir=root.parent) as tmpdir:
        staging = Path(tmpdir)
        for project_key, project in result.projects.items():
            batches = list(_task_batches(project.tasks, LABELSTUDIO_SPLIT_BATCH_SIZE))
            batch_total = len(batches)
            for batch_index, tasks in enumerate(batches, start=1):
                output_name = (
                    f"project_{project_key}_batch_{batch_index:03d}_"
                    f"of_{batch_total:03d}.json"
                )
                payload = _tasks_with_batch_data(
                    tasks,
                    project_key=project_key,
                    batch_index=batch_index,
                    batch_total=batch_total,
                )
                (staging / output_name).write_text(
                    _json_text(payload),
                    encoding="utf-8",
                )
                output_names.append(output_name)

            rule_ids = (
                rule_id
                for task in project.tasks
                for rule_id in task["data"].get("dsl_rules", [])
            )
            instructions = render_project_instructions(
                project_key,
                _project_title_for_key(project_key),
                rule_ids,
            )
            (staging / f"project_{project_key}_instructions.html").write_text(
                instructions,
                encoding="utf-8",
            )

        _validate_serialized_split_outputs(staging, result)
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

    seen_ids: set[str] = set()
    seen_words: set[str] = set()
    for index, (task, expected_word) in enumerate(
        zip(result.tasks, result.exported_words, strict=True)
    ):
        _validate_task(
            task,
            expected_word=expected_word,
            context=f"task {index}",
            seen_ids=seen_ids,
            seen_words=seen_words,
        )


def validate_split_export_result(result: SplitExportResult) -> None:
    """Validate project routing and global uniqueness in a split export."""
    seen_ids: set[str] = set()
    seen_words: set[str] = set()
    flattened_words: list[str] = []

    for project_key, project in result.projects.items():
        if not project.tasks:
            raise AnnotationExportError(f"project {project_key!r} is empty")
        if len(project.tasks) != len(project.exported_words):
            raise AnnotationExportError(
                f"project {project_key!r} task count does not match exported words"
            )
        expected_title = _project_title_for_key(project_key)
        if project.report.get("project_key") != project_key:
            raise AnnotationExportError(f"project {project_key!r} has inconsistent report key")
        if project.report.get("project_title") != expected_title:
            raise AnnotationExportError(
                f"project {project_key!r} has inconsistent report title"
            )

        for index, (task, expected_word) in enumerate(
            zip(project.tasks, project.exported_words, strict=True)
        ):
            context = f"project {project_key!r} task {index}"
            data = _validate_task(
                task,
                expected_word=expected_word,
                context=context,
                seen_ids=seen_ids,
                seen_words=seen_words,
            )
            if data.get("project_key") != project_key:
                raise AnnotationExportError(f"{context} has wrong project_key")
            if data.get("project_title") != expected_title:
                raise AnnotationExportError(f"{context} has wrong project_title")
            rules = data.get("dsl_rules")
            if (
                not isinstance(rules, list)
                or any(not isinstance(rule, str) or rule not in RULES for rule in rules)
                or len(rules) != len(set(rules))
            ):
                raise AnnotationExportError(f"{context} has invalid dsl_rules")
            expected_project = classify_project(expected_word, data["gemini_origin"])
            if expected_project["key"] != project_key:
                raise AnnotationExportError(
                    f"{context} belongs to project {expected_project['key']!r}"
                )
            if rules != expected_project["dsl_rules"]:
                raise AnnotationExportError(f"{context} has inconsistent dsl_rules")
        flattened_words.extend(project.exported_words)

    if set(flattened_words) != set(result.exported_words):
        raise AnnotationExportError(
            "split projects do not contain exactly the exported words"
        )
    if len(flattened_words) != len(result.exported_words):
        raise AnnotationExportError(
            "split project word count does not match base exported-word count"
        )


def _validate_task(
    task: Any,
    *,
    expected_word: str,
    context: str,
    seen_ids: set[str],
    seen_words: set[str],
) -> dict[str, Any]:
    if not isinstance(task, dict) or set(task) != {"data"}:
        raise AnnotationExportError(f"{context} must contain only a data object")
    data = task.get("data")
    if not isinstance(data, dict):
        raise AnnotationExportError(f"{context}.data must be an object")

    task_id = data.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise AnnotationExportError(f"{context} has invalid id")
    if task_id in seen_ids:
        raise AnnotationExportError(f"duplicate task id: {task_id!r}")
    seen_ids.add(task_id)

    surface = data.get("cyrl_word")
    if not isinstance(surface, str) or not surface:
        raise AnnotationExportError(f"{context} has invalid cyrl_word")
    normalized = normalize_word(surface)
    if normalized != expected_word:
        raise AnnotationExportError(
            f"{context} normalized word {normalized!r} does not match {expected_word!r}"
        )
    if normalized in seen_words:
        raise AnnotationExportError(f"duplicate normalized word: {normalized!r}")
    seen_words.add(normalized)

    origin = data.get("gemini_origin")
    if origin not in {"N", "RL", "U"}:
        raise AnnotationExportError(f"{context} has invalid gemini_origin")
    suggestion = data.get("auto_zamanalif")
    if not isinstance(suggestion, str):
        raise AnnotationExportError(f"{context} has invalid auto_zamanalif")
    if not suggestion:
        if origin != "U":
            raise AnnotationExportError(
                f"{context} has an empty suggestion for origin {origin}"
            )
    else:
        try:
            parse_dsl(suggestion)
        except DslError as exc:
            raise AnnotationExportError(
                f"{context} has invalid Zamanalif DSL: {exc}"
            ) from exc
    if not isinstance(data.get("hints_html"), str):
        raise AnnotationExportError(f"{context} has invalid hints_html")
    return data


def _validate_serialized_single_output(path: Path, result: ExportResult) -> None:
    payload = _read_json_array(path)
    if payload != result.tasks:
        raise AnnotationExportError("serialized Label Studio output changed task data")


def _validate_serialized_split_outputs(
    staging: Path,
    result: SplitExportResult,
) -> None:
    expected_names: set[str] = set()
    seen_ids: set[str] = set()
    seen_words: set[str] = set()

    for project_key, project in result.projects.items():
        batches = list(_task_batches(project.tasks, LABELSTUDIO_SPLIT_BATCH_SIZE))
        batch_total = len(batches)
        for batch_index, tasks in enumerate(batches, start=1):
            name = (
                f"project_{project_key}_batch_{batch_index:03d}_"
                f"of_{batch_total:03d}.json"
            )
            expected_names.add(name)
            payload = _read_json_array(staging / name)
            expected_payload = _tasks_with_batch_data(
                tasks,
                project_key=project_key,
                batch_index=batch_index,
                batch_total=batch_total,
            )
            if payload != expected_payload:
                raise AnnotationExportError(f"serialized batch {name} changed task data")
            if not 1 <= len(payload) <= LABELSTUDIO_SPLIT_BATCH_SIZE:
                raise AnnotationExportError(f"batch {name} has invalid task count")
            for task in payload:
                data = task["data"]
                task_id = data["id"]
                normalized = normalize_word(data["cyrl_word"])
                if task_id in seen_ids:
                    raise AnnotationExportError(
                        f"duplicate task id across serialized batches: {task_id!r}"
                    )
                if normalized in seen_words:
                    raise AnnotationExportError(
                        "duplicate normalized word across serialized batches: "
                        f"{normalized!r}"
                    )
                seen_ids.add(task_id)
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


def _tasks_with_batch_data(
    tasks: list[dict[str, Any]],
    *,
    project_key: str,
    batch_index: int,
    batch_total: int,
) -> list[dict[str, Any]]:
    batch_id = f"{project_key}_batch_{batch_index:03d}"
    return [
        {
            "data": {
                **task["data"],
                "batch_id": batch_id,
                "batch_index": batch_index,
                "batch_total": batch_total,
            }
        }
        for task in tasks
    ]


def load_exported_words(db_path: str | Path) -> set[str]:
    """Read normalized words already exported to Label Studio."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        ensure_review_state_schema(conn)
        rows = conn.execute("select normalized_word from exported_words").fetchall()
    return {row[0] for row in rows}


def mark_exported_words(db_path: str | Path, words: list[str]) -> None:
    """Persist normalized words exported in a successful batch."""
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(db_path)) as conn, conn:
        ensure_review_state_schema(conn)
        conn.executemany(
            """
            insert or ignore into exported_words(normalized_word, exported_at)
            values (?, ?)
            """,
            [(word, now) for word in words],
        )


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
    return {
        row[0]: ReviewedWord(normalized_word=row[0], zamanalif_dsl=row[1], origin=row[2])
        for row in rows
    }


def _display_word(surface: str, normalized: str) -> str:
    stripped = surface.strip()
    if len(stripped) >= 2 and stripped.isupper():
        return stripped
    return normalized


def _conditional_decision(char: str, word: str, index: int, label: str) -> str:
    converted = _conditional_char_conversion(char, word, index, label)
    if converted:
        return f"<b>{escape(char)}</b> -> <b>{escape(converted)}</b>"
    return f"<b>{escape(char)}</b> -> conditional"


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
    return output


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
    ("маэмай", "maemay", "maʼmay"),
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
    ("таэмин", "taemin", "täʼmin"),
    ("тәкать", "täkat", "täqat"),
    ("тәрәккый", "täräkqıy", "täräqqıy"),
    ("тәэмин", "täemin", "täʼmin"),
    ("тәэсир", "täesir", "täʼsir"),
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
    conn.execute(
        """
        create table if not exists exported_words (
            normalized_word text primary key,
            exported_at text not null
        )
        """
    )
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


def _report(
    *,
    total_sentences: int,
    total_tokens: int,
    unique_word_forms: int,
    exported: list[WordStats],
    mixed_harmony_n_skipped: int,
    already_exported_skipped: int,
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
        "already_exported_skipped_count": already_exported_skipped,
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


def _project_title_for_key(project_key: str) -> str:
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


def _project_report(project_key: str, tasks: list[dict[str, Any]], words: list[str]) -> dict[str, Any]:
    rule_counts: Counter[str] = Counter()
    for task in tasks:
        rule_counts.update(task["data"].get("dsl_rules", []))
    return {
        "project_key": project_key,
        "project_title": _project_title_for_key(project_key),
        "exported_word_count": len(tasks),
        "dsl_rule_counts": dict(sorted(rule_counts.items())),
        "exported_words": words,
    }


def _split_report(result: ExportResult, projects: dict[str, ExportResult]) -> dict[str, Any]:
    return {
        "exported_word_count": len(result.tasks),
        "project_count": len(projects),
        "projects": [
            {
                "project_key": key,
                "project_title": project.report["project_title"],
                "exported_word_count": len(project.tasks),
            }
            for key, project in projects.items()
        ],
        "base_report": result.report,
    }
