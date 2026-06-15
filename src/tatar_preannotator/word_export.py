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
REVIEWED_GH_WORDS = frozenset(
    {
        "аббревиатурадагы",
        "бгә",
        "елга",
        "законга",
        "закондагы",
        "йолдызлыгы",
        "киловатт-сәгәт",
        "кириллицадагы",
        "куелган",
        "латиницадагы",
        "принципларга",
        "томски-томскига",
        "цифрларга",
        "әсәрләрдәге",
    }
)
REVIEWED_GH_CONVERSIONS = {
    "аергыч": "ayırğıç",
    "җәмигъ": "cämiğ",
    "эшләргә": "eşlärğä",
    "игтибарлы": "iğtibarlı",
    "ишетелгән": "işetelğän",
    "кияргә": "kiärgä",
    "мәгънә": "mäğnä",
    "мәгънәгә": "mäğnägä",
    "мәгънәле": "mäğnäle",
    "мәгънәләре": "mäğnäläre",
    "мәгънәсен": "mäğnäsen",
    "мәгънәсендә": "mäğnäsendä",
    "мәгънәви": "mäğnäwi",
    "мәгәриф": "mäğärif",
    "нигмәтуллин": "niğmätullin",
    "нәгим": "näğim",
    "сарсаз-баграж": "sarsaz-bagraj",
    "сәмигулла": "sämiğulla",
    "сәнгәт": "sänğät",
    "сәгит": "säğit",
    "табиги": "tabiği",
    "гилемханов": "ğilemxanov",
    "гилмиев": "ğilmiev",
    "гәлимов": "ğälimov",
    "гәрәпчә-татарча": "ğäräpçä-tatarça",
    "гөмер": "ğömer",
    "гөмуми": "ğömumi",
    "гөмәр": "ğömär",
    "шигъри": "şiğri",
}
REVIEWED_GH_SEQUENCES = (
    ("агентлыгы", "гы"),
    ("белдергән", "гән"),
    ("белдергәндә", "гән"),
    ("белдергәнгә", "гән"),
    ("килгән", "гән"),
    ("килгәндә", "гән"),
    ("сингармонизмга", "га"),
)
REVIEWED_Q_WORDS = frozenset(
    {
        "берникәдәр",
        "беркатлы",
        "беркая",
        "беркайчан",
        "һичкайда",
        "һәркайсында",
        "икътисади",
        "мәкәләмдә",
        "мәхкүл",
        "кдпуда",
        "кәбәхәтлеге",
        "кәдер",
        "кәдими",
        "кәдәр",
        "көдрәт",
        "күәт",
        "рак",
        "тәкъдим",
        "тәшрик",
        "халикны",
        "өскорма",
    }
)
REVIEWED_Q_SEQUENCES = (
    ("алфавитка", "ка"),
    ("интернетка", "ка"),
    ("объектка", "ка"),
    ("принципка", "ка"),
    ("кубка", "ка"),
    ("салихка", "ка"),
    ("тарихка", "ка"),
    ("ёлку", "ку"),
    ("закончалыклар", "клар"),
    ("закончалыклары", "клары"),
)
REVIEWED_K_WORDS = frozenset(
    {
        "башка",
        "башкисәр",
        "башкисәрләрне",
        "дөньякүләм",
        "ияк",
        "камали",
        "камилләштерүгә",
        "камзул",
        "карават",
        "каз",
        "ком",
    }
)
REVIEWED_SIGN_CONVERSIONS = {
    "автомобиль": "avtomobil",
    "д'артаньян": "d'artanyan",
    "компьютер": "kompyuter",
    "компьютерлар": "kompyuterlar",
    "коньяк": "kon'yak",
    "кремль": "kreml",
    "маэмай": "ma'may",
    "медаль": "medal",
    "мәсьәлә": "mäs'älä",
    "мәсьүл": "mäs'ül",
    "нью-йорк": "nyu-york",
    "коръән": "qor'än",
    "статья": "stat'ya",
    "стиль": "stil",
    "тальян": "tal'yan",
    "таэмин": "tä'min",
    "тәэсир": "tä'sir",
    "тәэсирендә": "tä'sirendä",
}
REVIEWED_YU_CONVERSIONS = {
    "берьюлы": "beryulı",
    "тимерьюл": "timeryul",
    "революция": "revolyutsiä",
    "революциясе": "revolyutsiäse",
    "юк": "yuq",
    "юхиди": "yuxidi",
    "ю": "yü",
}


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
    if word in REVIEWED_GH_CONVERSIONS:
        return REVIEWED_GH_CONVERSIONS[word]
    if word in REVIEWED_SIGN_CONVERSIONS:
        return REVIEWED_SIGN_CONVERSIONS[word]
    if word in REVIEWED_YU_CONVERSIONS:
        return REVIEWED_YU_CONVERSIONS[word]

    month_conversion = _month_name_conversion(word)
    if month_conversion is not None:
        return month_conversion

    converted: list[str] = []
    index = 0
    while index < len(word):
        char = word[index]
        if char in {"ь", "ъ"} and index + 1 < len(word) and word[index + 1] == "я":
            index += 1
            continue
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


def _month_name_conversion(word: str) -> str | None:
    for cyrillic, latin in (
        ("гыйнвар", "ğinwar"),
        ("февраль", "fevral"),
        ("июн", "iyün"),
        ("июнь", "iyün"),
        ("июль", "iyül"),
        ("сентябрь", "sentäbr"),
        ("сентябр", "sentäbr"),
        ("октябрь", "oktäbr"),
        ("октябр", "oktäbr"),
        ("ноябрь", "noyäbr"),
        ("ноябр", "noyäbr"),
        ("декабрь", "dekäbr"),
        ("декабр", "dekäbr"),
    ):
        if word.startswith(cyrillic):
            return latin + _month_suffix_conversion(word[len(cyrillic) :])
    return None


def _month_suffix_conversion(suffix: str) -> str:
    if not suffix:
        return ""
    converted: list[str] = []
    for index, char in enumerate(suffix):
        if index == 0 and char == "е":
            converted.append("e")
        else:
            converted.append(_char_conversion(char, suffix, index, "N"))
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
    if char == "в" and index == 0 and word.startswith("вәлиев"):
        return "w"
    if char == "г":
        return _reviewed_gh_conversion(word, index) or "g"
    if char == "к":
        return _reviewed_k_conversion(word, index) or "k"
    if char == "я":
        return _ya_conversion(word, index, "RL")
    return {
        "в": "v",
        "ю": "yu",
        "у": "u",
        "ү": "ü",
        "ц": _ts_conversion(word, index),
    }.get(char, "")


def _native_conditional_char(char: str, word: str, index: int) -> str:
    harmony = vowel_harmony_class(word)
    if char == "в":
        return "w"
    if char == "г":
        suffix = _reviewed_gh_conversion(word, index)
        if suffix:
            return suffix
        context = _local_vowel_context(word, index)
        if context == "front":
            return "g"
        if context == "back":
            return "ğ"
        return "g" if harmony == "front_only" else "ğ" if harmony == "back_only" else ""
    if char == "к":
        reviewed = _reviewed_k_conversion(word, index)
        if reviewed:
            return reviewed
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
    if index > 0 and word[index - 1] in FRONT_VOWELS | BACK_VOWELS:
        return "ts"
    return "s"


def _reviewed_gh_conversion(word: str, index: int) -> str:
    if word in REVIEWED_GH_WORDS:
        return "ğ"
    for cyrillic, sequence in REVIEWED_GH_SEQUENCES:
        if word == cyrillic and word.rfind(sequence) == index:
            return "ğ"
    return ""


def _reviewed_k_conversion(word: str, index: int) -> str:
    if word in REVIEWED_Q_WORDS:
        return "q"
    for cyrillic, sequence in REVIEWED_Q_SEQUENCES:
        if word == cyrillic and word.rfind(sequence) == index:
            return "q"
    if word in REVIEWED_K_WORDS:
        return "k"
    return ""


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
