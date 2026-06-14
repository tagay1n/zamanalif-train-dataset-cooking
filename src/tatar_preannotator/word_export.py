from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

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
) -> ExportResult:
    """Build Label Studio word-review tasks from annotated SQLite rows."""
    return _export_from_records(
        _sqlite_records(db_path),
        max_items=max_items,
        include_rl=include_rl,
        include_unknown=include_unknown,
        min_frequency=min_frequency,
        sort_by=sort_by,
        already_exported=already_exported,
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
) -> ExportResult:
    stats: dict[str, WordStats] = {}
    total_sentences = 0
    total_tokens = 0
    mixed_harmony_n_skipped = 0
    already_exported_skipped = 0
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

            has_conditional = contains_conditional_letter(normalized)
            has_rl_review = contains_rl_review_letter(normalized)
            if label == "RL" and (not include_rl or not has_rl_review):
                continue
            if label == "U" and not include_unknown:
                continue
            if label == "N" and not has_conditional:
                continue
            if label == "N" and vowel_harmony_class(normalized) == "mixed_front_back":
                mixed_harmony_n_skipped += 1
                continue
            if normalized in already_exported:
                already_exported_skipped += 1
                continue

            entry = stats.get(normalized)
            if entry is None:
                entry = WordStats(normalized=normalized, display=_display_word(text, normalized))
                stats[normalized] = entry
            entry.frequency += 1
            entry.label_counts[label] += 1
            entry.conditional_letters.update(
                char for char in normalized if char in CONDITIONAL_LETTERS
            )

    candidates = [
        entry
        for entry in stats.values()
        if entry.frequency >= min_frequency
        and (
            contains_conditional_letter(entry.normalized)
            or entry.label == "U"
            or (entry.label == "RL" and contains_rl_review_letter(entry.normalized))
        )
    ]
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
                "auto_zamanalif": convert_for_annotation(entry.normalized, entry.label),
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
        ),
        exported_words=[entry.normalized for entry in candidates],
    )


def _sqlite_records(db_path: str | Path) -> Iterable[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
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
    if label not in {"N", "RL"}:
        converted = _best_effort_unknown(word)
    else:
        converted = _convert_known_label(word, label)
    return converted if _is_clean_zamanalif(converted) else ""


def decision_html(entry: WordStats) -> str:
    """Build compact vertical conversion-decision HTML for Label Studio."""
    items: list[str] = []
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
    if not convert_for_annotation(entry.normalized, entry.label):
        items.append("Automatic converter produced no clean Latin suggestion")
    items.append(
        f"Frequency for <b><i>{escape(entry.normalized)}</i></b>: <b>{entry.frequency}</b>"
    )
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


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
    with sqlite3.connect(db_path) as conn:
        _ensure_state_schema(conn)
        rows = conn.execute("select normalized_word from exported_words").fetchall()
    return {row[0] for row in rows}


def mark_exported_words(db_path: str | Path, words: list[str]) -> None:
    """Persist normalized words exported in a successful batch."""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        _ensure_state_schema(conn)
        conn.executemany(
            """
            insert or ignore into exported_words(normalized_word, exported_at)
            values (?, ?)
            """,
            [(word, now) for word in words],
        )


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
    if char in CONDITIONAL_LETTERS:
        return _conditional_char_conversion(char, word, index, label)
    if label == "RL" and char == "ы":
        return "ıy"
    if label == "RL" and char in {"ь", "ъ"}:
        return "'"
    return _deterministic_char(char)


def _convert_known_label(word: str, label: str) -> str:
    converted: list[str] = []
    index = 0
    while index < len(word):
        char = word[index]
        if char == "ц" and index + 1 < len(word) and word[index + 1] == "ц":
            while index + 1 < len(word) and word[index + 1] == "ц":
                index += 1
            converted.append("ts")
        else:
            converted.append(_char_conversion(char, word, index, label))
        index += 1
    return "".join(converted)


def _conditional_char_conversion(char: str, word: str, index: int, label: str) -> str:
    if label == "RL":
        return _loanword_conditional_char(char, word, index)
    if label == "N":
        return _native_conditional_char(char, word, index)
    loan = _loanword_conditional_char(char, word, index)
    native = _native_conditional_char(char, word, index)
    return loan if loan == native else ""


def _loanword_conditional_char(char: str, word: str, index: int) -> str:
    return {
        "в": "v",
        "г": "g",
        "к": "k",
        "я": "ya",
        "ю": "yu",
        "е": "ye",
        "у": "u",
        "ү": "ü",
        "ц": _ts_conversion(word, index),
    }.get(char, "")


def _native_conditional_char(char: str, word: str, index: int) -> str:
    harmony = vowel_harmony_class(word)
    if char == "в":
        return "w"
    if char == "г":
        return "g" if harmony == "front_only" else "ğ" if harmony == "back_only" else ""
    if char == "к":
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
        if index > 0 and word[index - 1] == "и":
            return "ä" if harmony == "front_only" else "a" if harmony == "back_only" else ""
        if harmony == "no_vowels":
            return "ya"
        return "yä" if harmony == "front_only" else "ya" if harmony == "back_only" else ""
    if char == "ю":
        if index > 0 and word[index - 1] == "и":
            return "iü"
        if harmony == "no_vowels":
            return "yu"
        return "yü" if harmony == "front_only" else "yu" if harmony == "back_only" else ""
    if char == "е":
        if index > 0:
            return "e"
        return "ye" if harmony == "front_only" else "yı" if harmony == "back_only" else ""
    if char == "ц":
        return _ts_conversion(word, index)
    return ""


def _ts_conversion(word: str, index: int) -> str:
    if index > 0 and word[index - 1] in FRONT_VOWELS | BACK_VOWELS:
        return "ts"
    return "s"


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


def _ensure_state_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists exported_words (
            normalized_word text primary key,
            exported_at text not null
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
) -> dict[str, Any]:
    conditional_counts = Counter()
    for entry in exported:
        conditional_counts.update(entry.conditional_letters)
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
        "top_50_exported_words_by_frequency": [
            {"word": entry.normalized, "frequency": entry.frequency}
            for entry in sorted(exported, key=lambda item: (-item.frequency, item.normalized))[:50]
        ],
    }
