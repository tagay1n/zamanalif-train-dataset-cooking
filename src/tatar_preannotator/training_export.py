from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Iterable

from .conversion import DslError, RULES, parse_dsl, resolve_dsl
from .word_export import (
    ReviewedWord,
    conversion_branches,
    load_reviewed_words,
    normalize_word,
    vowel_harmony_class,
)


CYRILLIC_RE = re.compile(r"[А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]")
DSL_DELIMITER_RE = re.compile(r"{{|}}")
CASE_SEPARATORS_RE = re.compile(r"([-\u2019'])")


class TrainingExportError(ValueError):
    """Raised when a training dataset cannot be exported safely."""


@dataclass(frozen=True)
class TrainingExportSummary:
    """Paths and counts produced by a successful training export."""

    output_path: Path
    manifest_path: Path
    exported_count: int
    skipped_count: int


@dataclass(frozen=True)
class _SentenceRecord:
    sample_id: str
    text: str
    tatar: bool
    tokens: list[dict]


@dataclass(frozen=True)
class _NotReady:
    reason: str


def parse_policy_overrides(values: Iterable[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Build an effective DSL policy from registered defaults and CLI overrides."""
    effective = {
        rule_id: definition.default_option for rule_id, definition in RULES.items()
    }
    overrides: dict[str, str] = {}
    for raw in values:
        if raw.count("=") != 1:
            raise TrainingExportError(
                f"invalid --choice {raw!r}; expected RULE=OPTION"
            )
        rule_id, option_id = (part.strip() for part in raw.split("=", 1))
        if rule_id not in RULES:
            raise TrainingExportError(f"unknown DSL rule in --choice: {rule_id!r}")
        if rule_id in overrides:
            raise TrainingExportError(f"duplicate --choice for DSL rule: {rule_id}")
        allowed = {name for name, _ in RULES[rule_id].options}
        if option_id not in allowed:
            raise TrainingExportError(
                f"unknown option {option_id!r} for {rule_id}; "
                f"expected one of: {', '.join(sorted(allowed))}"
            )
        effective[rule_id] = option_id
        overrides[rule_id] = option_id
    return effective, overrides


def export_training_dataset(
    db_path: str | Path,
    output_path: str | Path,
    *,
    choice_overrides: Iterable[str] = (),
) -> TrainingExportSummary:
    """Export fully resolved Cyrillic/Zamanalif sentence pairs as JSONL."""
    database = Path(db_path)
    if not database.exists():
        raise TrainingExportError(f"database file does not exist: {database}")

    effective_policy, overrides = parse_policy_overrides(choice_overrides)
    reviewed = load_reviewed_words(database)
    _validate_reviewed_dictionary(reviewed)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = Path(str(output) + ".manifest.json")
    skipped: Counter[str] = Counter()
    total_annotated = 0
    tatar_sentences = 0
    non_tatar_sentences = 0
    exported_count = 0

    output_temp = _temporary_path(output)
    manifest_temp = _temporary_path(manifest)
    try:
        with output_temp.open("w", encoding="utf-8") as handle:
            for record in _read_annotated_records(database):
                total_annotated += 1
                if not record.tatar:
                    non_tatar_sentences += 1
                    continue
                tatar_sentences += 1
                converted = _convert_sentence(record, reviewed, effective_policy)
                if isinstance(converted, _NotReady):
                    skipped[converted.reason] += 1
                    continue
                _validate_resolved_sentence(record.sample_id, converted)
                handle.write(
                    json.dumps(
                        {
                            "id": record.sample_id,
                            "cyrillic": record.text,
                            "zamanalif": converted,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                exported_count += 1

        manifest_data = {
            "effective_policy": dict(sorted(effective_policy.items())),
            "overrides": dict(sorted(overrides.items())),
            "counts": {
                "annotated_sentences": total_annotated,
                "tatar_sentences": tatar_sentences,
                "non_tatar_sentences_ignored": non_tatar_sentences,
                "exported_sentences": exported_count,
                "skipped_not_ready_sentences": sum(skipped.values()),
            },
            "skipped_by_reason": dict(sorted(skipped.items())),
        }
        manifest_temp.write_text(
            json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(output_temp, output)
        os.replace(manifest_temp, manifest)
    except Exception:
        output_temp.unlink(missing_ok=True)
        manifest_temp.unlink(missing_ok=True)
        raise

    return TrainingExportSummary(
        output_path=output,
        manifest_path=manifest,
        exported_count=exported_count,
        skipped_count=sum(skipped.values()),
    )


def _read_annotated_records(db_path: Path) -> Iterable[_SentenceRecord]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                select s.id, s.text, p.tatar, p.tokens_json
                from preannotation_state p
                join samples s on s.id = p.sample_id
                where p.status = 'annotated'
                  and p.tatar is not null
                  and p.tokens_json is not null
                order by s.id
                """
            ).fetchall()
        except sqlite3.Error as exc:
            raise TrainingExportError(f"cannot read annotation database: {exc}") from exc

    for row in rows:
        try:
            tokens = json.loads(row["tokens_json"])
        except json.JSONDecodeError as exc:
            raise TrainingExportError(
                f"{row['id']}: invalid tokens_json: {exc}"
            ) from exc
        if not isinstance(tokens, list):
            raise TrainingExportError(f"{row['id']}: tokens_json must contain a list")
        yield _SentenceRecord(
            sample_id=str(row["id"]),
            text=str(row["text"]),
            tatar=bool(row["tatar"]),
            tokens=tokens,
        )


def _convert_sentence(
    record: _SentenceRecord,
    reviewed: dict[str, ReviewedWord],
    policy: dict[str, str],
) -> str | _NotReady:
    pieces: list[str] = []
    cursor = 0
    for token_index, token in enumerate(record.tokens):
        if not isinstance(token, dict):
            raise TrainingExportError(
                f"{record.sample_id}: token {token_index} must be an object"
            )
        text = token.get("text")
        label = token.get("label")
        if not isinstance(text, str) or not text:
            raise TrainingExportError(
                f"{record.sample_id}: token {token_index} has invalid text"
            )
        if label not in {"N", "RL", "U"}:
            raise TrainingExportError(
                f"{record.sample_id}: token {token_index} has invalid label: {label!r}"
            )
        found = record.text.find(text, cursor)
        if found < 0:
            raise TrainingExportError(
                f"{record.sample_id}: token {token_index} is missing or out of order: {text!r}"
            )
        pieces.append(record.text[cursor:found])

        normalized = normalize_word(text)
        if not normalized:
            pieces.append(text)
            cursor = found + len(text)
            continue
        if token.get("homonym") is True:
            return _NotReady("contextual_homonym")

        approved = reviewed.get(normalized)
        if approved is not None:
            dsl = approved.zamanalif_dsl
        else:
            if label == "N" and vowel_harmony_class(normalized) == "mixed_front_back":
                return _NotReady("mixed_harmony_word")
            branches = conversion_branches(normalized)
            if branches.state != "origin_independent":
                return _NotReady("unreviewed_word")
            dsl = branches.suggestion(label)
            if not dsl:
                raise TrainingExportError(
                    f"{record.sample_id}: converter failed for token {text!r}"
                )

        try:
            resolved = resolve_dsl(dsl, policy)
        except DslError as exc:
            raise TrainingExportError(
                f"{record.sample_id}: invalid DSL for {normalized!r}: {exc}"
            ) from exc
        pieces.append(_apply_source_case(text, resolved))
        cursor = found + len(text)

    pieces.append(record.text[cursor:])
    return "".join(pieces)


def _apply_source_case(source: str, target: str) -> str:
    source_parts = CASE_SEPARATORS_RE.split(source)
    target_parts = CASE_SEPARATORS_RE.split(target)
    if len(source_parts) == len(target_parts) and source_parts[1::2] == target_parts[1::2]:
        return "".join(
            target_part if index % 2 else _apply_part_case(source_parts[index], target_part)
            for index, target_part in enumerate(target_parts)
        )
    return _apply_part_case(source, target)


def _apply_part_case(source: str, target: str) -> str:
    letters = "".join(char for char in source if char.isalpha())
    if not letters or letters.islower():
        return target
    if letters.isupper():
        return _zamanalif_upper(target)
    if letters[0].isupper() and letters[1:].islower():
        return _uppercase_first(target)
    return target


def _uppercase_first(value: str) -> str:
    for index, char in enumerate(value):
        if char.isalpha():
            return value[:index] + _zamanalif_upper(char) + value[index + 1 :]
    return value


def _zamanalif_upper(value: str) -> str:
    return "".join({"i": "İ", "ı": "I"}.get(char, char.upper()) for char in value)


def _validate_reviewed_dictionary(reviewed: dict[str, ReviewedWord]) -> None:
    for word, annotation in reviewed.items():
        try:
            parse_dsl(annotation.zamanalif_dsl)
        except DslError as exc:
            raise TrainingExportError(
                f"invalid reviewed DSL for {word!r}: {exc}"
            ) from exc


def _validate_resolved_sentence(sample_id: str, value: str) -> None:
    if DSL_DELIMITER_RE.search(value):
        raise TrainingExportError(f"{sample_id}: unresolved DSL remained in output")
    match = CYRILLIC_RE.search(value)
    if match:
        raise TrainingExportError(
            f"{sample_id}: unresolved Cyrillic character remained in output: {match.group()!r}"
        )


def _temporary_path(destination: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(raw_path)
