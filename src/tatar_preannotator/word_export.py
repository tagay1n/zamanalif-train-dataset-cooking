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
from typing import Any, Iterable

from tatar_preannotator.conversion import (
    ConversionResult,
    DslError,
    parse_dsl,
    result_with_iya_choices,
)
from zamanalif_selector.features import BACK_VOWELS, CONDITIONAL_LETTERS, FRONT_VOWELS

CYRILLIC_RE = re.compile(r"[А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]")
RL_REVIEW_LETTERS = frozenset("ёыьъщ")
ALLOWED_ZAMANALIF = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "äÄöÖüÜñÑıİğĞşŞçÇ"
    "-—'’"
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
) -> ExportResult:
    """Build Label Studio word-review tasks from annotated SQLite rows."""
    if reviewed_words is None:
        reviewed_words = set(load_reviewed_words(db_path))
    return _export_from_records(
        _sqlite_records(db_path),
        max_items=max_items,
        include_rl=include_rl,
        include_unknown=include_unknown,
        min_frequency=min_frequency,
        sort_by=sort_by,
        already_exported=already_exported,
        reviewed_words=reviewed_words,
    )


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
            if token.get("homonym") is True:
                homonym_words.add(normalized)

            entry = stats.get(normalized)
            if entry is None:
                entry = WordStats(normalized=normalized, display=_display_word(text, normalized))
                stats[normalized] = entry
            entry.frequency += 1
            entry.label_counts[label] += 1
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
    return result_with_iya_choices(word, compact)


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
    branches = conversion_branches(entry.normalized)
    if branches.state != "origin_independent":
        items.append(
            "Native branch: " + _branch_suggestion_html(branches.native_dsl)
        )
        items.append(
            "Loanword branch: " + _branch_suggestion_html(branches.loanword_dsl)
        )
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


def _branch_suggestion_html(value: str) -> str:
    if not value:
        return "<b>unavailable</b>"
    return f"<b>{escape(value)}</b>"


def write_outputs(
    result: ExportResult,
    output_path: str | Path,
    *,
    report_output: str | Path | None = None,
) -> Path:
    """Write Label Studio JSON and report JSON. Return the report path."""
    output = Path(output_path)
    output.write_text(
        json.dumps(result.tasks, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path = Path(report_output) if report_output else Path(str(output) + ".report.json")
    report_path.write_text(
        json.dumps(result.report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report_path


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
    if char in {"'", "’"}:
        return "'"
    if char in CONDITIONAL_LETTERS:
        return _conditional_char_conversion(char, word, index, label)
    if label == "RL" and char == "ы":
        return _loanword_y_conversion(word)
    if label == "RL" and char in {"ь", "ъ"}:
        if index + 1 < len(word) and word[index + 1] == "е":
            return ""
        return "'"
    return _deterministic_char(char)


def _convert_known_label(word: str, label: str) -> str:
    converted: list[str] = []
    index = 0
    while index < len(word):
        char = word[index]
        surname_conversion = _surname_sequence_conversion(word, index)
        if surname_conversion is not None:
            latin, consumed = surname_conversion
            converted.append(latin)
            index += consumed - 1
        elif char == "ц" and index + 1 < len(word) and word[index + 1] == "ц":
            while index + 1 < len(word) and word[index + 1] == "ц":
                index += 1
            converted.append("ts")
        else:
            converted.append(_char_conversion(char, word, index, label))
        index += 1
    return "".join(converted)


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
    if word.startswith(("музы", "посыл", "выш", "сыр")):
        return "ıy"
    return "ı"


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
        return "ğ" if suffix in {"га", "дагы", "ларга"} else "g"
    if char == "г" and index == len(word) - 2 and word.endswith(("дагы", "дәге")):
        stem = word[: -4]
        if len(stem) >= 5:
            return "ğ" if word.endswith("дагы") else "g"
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
        if len(stem) >= 5:
            return "q" if word.endswith(("лык", "лыкка", "лыгын")) else "k"
    return ""


def _native_conditional_char(char: str, word: str, index: int) -> str:
    harmony = vowel_harmony_class(word)
    if char == "в":
        return "w"
    if char == "г":
        context = _local_vowel_context(word, index)
        if context == "front":
            return "g"
        if context == "back":
            return "ğ"
        return "g" if harmony == "front_only" else "ğ" if harmony == "back_only" else ""
    if char == "к":
        context = _local_vowel_context(word, index)
        if context == "front":
            return "k"
        if context == "back":
            return "q"
        return "k" if harmony == "front_only" else "q" if harmony == "back_only" else ""
    if char == "у":
        if index > 0 and word[index - 1] in {"а", "ә"}:
            return "w"
        return "u"
    if char == "ү":
        if index > 0 and word[index - 1] in {"а", "ә"}:
            return "w"
        return "ü"
    if char == "я":
        return _ya_conversion(word, index, "N")
    if char == "ю":
        if index > 0 and word[index - 1] == "и":
            return "yü"
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
        return "yä"
    return ""


def _e_conversion(word: str, index: int, label: str) -> str:
    previous = word[index - 1] if index > 0 else ""
    if previous in {"и", "ү"}:
        return "e"
    if previous in {"ь", "ъ"}:
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
    local_back_vowels = BACK_VOWELS | {"я"}
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
