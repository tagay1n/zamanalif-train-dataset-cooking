from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from itertools import groupby, zip_longest
import json
from math import inf
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata
from typing import Iterable

from .conversion import (
    CYRILLIC_LETTER_REFERENCES,
    ZAMANALIF_APOSTROPHE,
    DslError,
    RULES,
    parse_dsl,
    resolve_dsl,
)
from .conflict_resolver import load_word_resolutions
from .contextual_review import (
    ContextualReview,
    OccurrenceKey,
    load_contextual_reviews,
)
from .literal_projection import literal_source_view
from .spelling_validation import is_approved_spelling, spelling_matches
from .word_export import (
    INTERNAL_DOUBLE_QUOTES,
    TATAR_SUFFIXES_AFTER_QUOTE,
    ReviewedWord,
    _char_conversion,
    conversion_branches,
    load_reviewed_words,
    normalize_word,
    vowel_harmony_class,
)


CYRILLIC_RE = re.compile(r"[А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]")
DSL_DELIMITER_RE = re.compile(r"{{|}}")
DIGIT_RUN_RE = re.compile(r"\d+")
WORD_INTERNAL_SOFT_HYPHEN_RE = re.compile(r"(?<=[^\W\d_])\u00ad(?=[^\W\d_])")
CASE_SEPARATORS_RE = re.compile(r"([-\u2019'()])")
DOTTED_CASE_SEPARATORS_RE = re.compile(r"([-.\u2019'()])")
DOTTED_INITIAL_NAME_RE = re.compile(
    r"[^\w.]*(?:[А-ЯЁӘӨҮҖҢҺ]\.)+[А-ЯЁӘӨҮҖҢҺ][а-яёәөүҗңһ]"
)
REVIEWED_JOINED_SENTENCE_FORMS = frozenset({"галәмин.Уәссаләтү", "әҗмәгыйн.Бөтен"})
REVIEWED_JOINED_CASE_FORMS = {
    "июнендәгеОАО": "iyünendägeOAO",
    "мөмкинГади": "mömkinĞadi",
    "МӨХӘММӘДИЕВлар": "MÖXÄMMÄDİYEVlar",
    "ФәритЯхинныукыйбыз": "FäritYäxinnıuqıybız",
}
E_GLIDE_RE = re.compile(r"Y[EOI]")
APPROVED_IDENTIFIER_CASE = {"Ц20": "Ts20"}
MAX_FAILURE_EXAMPLES = 10
MIXED_CASE_LETTER_VARIANTS = {
    "е": ("e", "ye", "yı", "yo"),
    "ц": ("ts", "s"),
    "ю": ("yu", "yü"),
    "я": ("ya", "yä", "a", "ä"),
}


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


@dataclass(frozen=True)
class _ConvertedSentence:
    text: str
    word_alignment_error: str | None = None
    punctuation_error: str | None = None
    character_error: str | None = None
    capitalization_error: str | None = None
    digit_error: str | None = None
    usable_output_error: str | None = None


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
    allow_empty: bool = False,
) -> TrainingExportSummary:
    """Export fully resolved Cyrillic/Zamanalif sentence pairs as JSONL."""
    database = Path(db_path)
    if not database.exists():
        raise TrainingExportError(f"database file does not exist: {database}")

    effective_policy, overrides = parse_policy_overrides(choice_overrides)
    reviewed = load_reviewed_words(database)
    contextual_reviews = load_contextual_reviews(database)
    word_resolutions = {
        word: resolution.decision
        for word, resolution in load_word_resolutions(database).items()
    }
    _validate_reviewed_dictionary(reviewed)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = Path(str(output) + ".manifest.json")
    skipped: Counter[str] = Counter()
    total_annotated = 0
    tatar_sentences = 0
    non_tatar_sentences = 0
    exported_count = 0
    duplicate_pairs = 0
    targets_by_source: dict[str, dict[str, str]] = {}
    word_alignment_errors: list[str] = []
    punctuation_errors: list[str] = []
    character_errors: list[str] = []
    capitalization_errors: list[str] = []
    digit_errors: list[str] = []
    usable_output_errors: list[str] = []
    target_errors: list[str] = []
    conversion_errors: list[str] = []

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
                try:
                    converted = _convert_sentence(
                        record,
                        reviewed,
                        contextual_reviews,
                        effective_policy,
                        word_resolutions,
                    )
                except TrainingExportError as exc:
                    message = str(exc)
                    prefix = f"{record.sample_id}: "
                    conversion_errors.append(prefix + message.removeprefix(prefix))
                    continue
                if isinstance(converted, _NotReady):
                    skipped[converted.reason] += 1
                    continue
                if converted.word_alignment_error:
                    word_alignment_errors.append(converted.word_alignment_error)
                if converted.punctuation_error:
                    punctuation_errors.append(converted.punctuation_error)
                if converted.character_error:
                    character_errors.append(converted.character_error)
                if converted.capitalization_error:
                    capitalization_errors.append(converted.capitalization_error)
                if converted.digit_error:
                    digit_errors.append(converted.digit_error)
                if converted.usable_output_error:
                    usable_output_errors.append(converted.usable_output_error)
                else:
                    targets = targets_by_source.setdefault(record.text, {})
                    if converted.text in targets:
                        duplicate_pairs += 1
                    else:
                        targets[converted.text] = record.sample_id
                try:
                    _validate_resolved_sentence(record.sample_id, converted.text)
                except TrainingExportError as exc:
                    target_errors.append(str(exc))
                    continue
                if (
                    converted.word_alignment_error
                    or converted.punctuation_error
                    or converted.character_error
                    or converted.capitalization_error
                    or converted.digit_error
                    or converted.usable_output_error
                ):
                    continue
                handle.write(
                    json.dumps(
                        {
                            "id": record.sample_id,
                            "cyrillic": record.text,
                            "zamanalif": converted.text,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                exported_count += 1

        duplicate_errors = [
            ", ".join(f"{sample_id} -> {target!r}" for target, sample_id in targets.items())
            for targets in targets_by_source.values()
            if len(targets) > 1
        ]
        error_groups = (
            ("Word alignment", "sentence(s)", word_alignment_errors),
            ("Punctuation and whitespace", "sentence(s)", punctuation_errors),
            ("Character validation", "sentence(s)", character_errors),
            ("Capitalization", "sentence(s)", capitalization_errors),
            ("Digit preservation", "sentence(s)", digit_errors),
            ("Conflicting duplicates", "source group(s)", duplicate_errors),
            ("Usable output", "sentence(s)", usable_output_errors),
            ("Target validation", "sentence(s)", target_errors),
            ("Conversion", "sentence(s)", conversion_errors),
        )
        failures: list[str] = []
        for check, unit, errors in error_groups:
            if errors:
                examples = errors[:MAX_FAILURE_EXAMPLES]
                failures.append(f"{check} failed for {len(errors)} {unit}:")
                failures.extend(f"  {error}" for error in examples)
                if len(errors) > len(examples):
                    failures.append(f"  … {len(errors) - len(examples)} more")
        if not exported_count and not allow_empty:
            failures.append(
                f"Usable output failed: zero usable pairs ({sum(skipped.values())} awaiting review). "
                "Use --allow-empty for an intentional zero-row export."
            )
        if failures:
            raise TrainingExportError("\n".join(failures))

        manifest_data = {
            "effective_policy": dict(sorted(effective_policy.items())),
            "overrides": dict(sorted(overrides.items())),
            "counts": {
                "annotated_sentences": total_annotated,
                "tatar_sentences": tatar_sentences,
                "non_tatar_sentences_ignored": non_tatar_sentences,
                "exported_sentences": exported_count,
                "duplicate_pairs": duplicate_pairs,
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
    contextual_reviews: dict[OccurrenceKey, ContextualReview],
    policy: dict[str, str],
    word_resolutions: dict[str, str],
) -> _ConvertedSentence | _NotReady:
    pieces: list[str] = []
    token_count_changes: list[str] = []
    punctuation_error = None
    character_error = None
    capitalization_error = None
    digit_error = None
    cursor = 0
    literal_view = literal_source_view(record.text)
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
        pieces.append(literal_view[cursor:found])
        end = found + len(text)
        projected_text = literal_view[found:end]

        conversion = resolve_training_token(
            record.sample_id, token_index, token, reviewed, contextual_reviews,
            policy, word_resolutions,
            projected_text=projected_text,
        )
        if conversion.text is None:
            return _NotReady(conversion.source)
        source_count = _count_words(text)
        target_count = _count_words(conversion.text)
        if source_count != target_count:
            token_count_changes.append(
                f"token {token_index} {text!r} -> {conversion.text!r} "
                f"(Cyrillic={source_count}, Zamanalif={target_count})"
            )
        if punctuation_error is None and not _token_punctuation_matches(text, conversion, policy):
            punctuation_error = (
                f"{record.sample_id}: punctuation or whitespace mismatch at token {token_index} "
                f"{text!r} -> {conversion.text!r}"
            )
        if character_error is None and not is_approved_spelling(
            record.sample_id, token_index, text, conversion.text,
        ):
            dotted = record.text.startswith(".", end)
            expected_counts = _expected_character_counts(
                projected_text, conversion, policy, dotted=dotted,
            )
            target_length = _spelling_length(conversion.text)
            if target_length not in expected_counts:
                expected = ", ".join(str(count) for count in sorted(expected_counts))
                character_error = (
                    f"{record.sample_id}: character count mismatch at token {token_index} "
                    f"{text!r} -> {conversion.text!r} (expected {expected}, got {target_length})"
                )
            elif not spelling_matches(
                projected_text, conversion.text, policy,
                legacy_e_glides=conversion.legacy_e_glides,
                reviewed_initial=(
                    conversion.source in {"dictionary", "contextual"}
                    and text.isupper() and (dotted or text.endswith("."))
                ),
            ):
                character_error = (
                    f"{record.sample_id}: letter correspondence mismatch at token {token_index} "
                    f"{text!r} -> {conversion.text!r}"
                )
        if capitalization_error is None and not _capitalization_matches(
            text, conversion.text, conversion.origin,
        ):
            capitalization_error = (
                f"{record.sample_id}: capitalization mismatch at token {token_index} "
                f"{text!r} -> {conversion.text!r}"
            )
        if digit_error is None:
            digit_error = _digit_mismatch(
                f"{record.sample_id}: token {token_index}", text, conversion.text,
            )
        pieces.append(conversion.text)
        cursor = end

    pieces.append(literal_view[cursor:])
    converted = "".join(pieces)
    if digit_error is None:
        digit_error = _digit_mismatch(f"{record.sample_id}: sentence", record.text, converted)
    source_count = _count_words(record.text)
    target_count = _count_words(converted)
    usable_output_error = None
    if not source_count or not target_count:
        usable_output_error = (
            f"{record.sample_id}: each side needs a word or number "
            f"(Cyrillic={source_count}, Zamanalif={target_count})"
        )
    alignment_error = None
    if source_count != target_count or token_count_changes:
        alignment_error = (
            f"{record.sample_id}: word alignment mismatch "
            f"(Cyrillic={source_count}, Zamanalif={target_count})"
        )
        if token_count_changes:
            alignment_error += ". " + "; ".join(token_count_changes)
    if punctuation_error is None:
        boundaries = zip_longest(
            _punctuation_layout(record.text), _punctuation_layout(converted),
        )
        for boundary, (source_marks, target_marks) in enumerate(boundaries):
            if source_marks != target_marks:
                punctuation_error = (
                    f"{record.sample_id}: punctuation or whitespace mismatch after "
                    f"{boundary} text run(s) (Cyrillic={source_marks!r}, Zamanalif={target_marks!r})"
                )
                break
    return _ConvertedSentence(
        text=converted,
        word_alignment_error=alignment_error,
        punctuation_error=punctuation_error,
        character_error=character_error,
        capitalization_error=capitalization_error,
        digit_error=digit_error,
        usable_output_error=usable_output_error,
    )


def _digit_mismatch(location: str, source: str, target: str) -> str | None:
    source_digits = DIGIT_RUN_RE.findall(source)
    target_digits = DIGIT_RUN_RE.findall(target)
    if source_digits != target_digits:
        return f"{location}: digit runs differ (Cyrillic={source_digits!r}, Zamanalif={target_digits!r})"
    return None


def _count_words(value: str) -> int:
    # Keep compounds and dotted abbreviations together and ignore punctuation-only fields.
    return sum(
        any(char.isalnum() and char != ZAMANALIF_APOSTROPHE for char in field)
        for field in value.split()
    )


@dataclass(frozen=True)
class TokenConversion:
    normalized_word: str
    text: str | None
    origin: str
    source: str
    legacy_e_glides: int = 0


def _is_text_character(char: str) -> bool:
    return char.isalnum() or unicodedata.category(char).startswith("M")


def _spelling_length(value: str) -> int:
    value = unicodedata.normalize("NFC", value)
    return len(value) if value.isalnum() else sum(map(_is_text_character, value))


def _cased_letters(value: str) -> str:
    return "".join(char for char in value if char.lower() != char.upper())


def _capitalization_matches(source: str, target: str, origin: str) -> bool:
    if source == target:
        return True
    if source.isalpha() and target.isalpha():
        return _part_capitalization_matches(source, target, origin)
    source_parts = ["".join(run) for is_text, run in groupby(source, _is_text_character) if is_text]
    target_parts = ["".join(run) for is_text, run in groupby(target, _is_text_character) if is_text]
    if len(source_parts) != len(target_parts):
        return _part_capitalization_matches(source, target, origin)
    return all(
        _part_capitalization_matches(left, right, origin)
        for left, right in zip(source_parts, target_parts)
    )


def _part_capitalization_matches(source: str, target: str, origin: str) -> bool:
    identifier = _approved_identifier_case(source, target)
    if identifier is not None:
        return target == identifier
    source_letters, target_letters = _cased_letters(source), _cased_letters(target)
    if not source_letters or source_letters.islower():
        return not target_letters or target_letters.islower()
    if source_letters.isupper():
        return target_letters.isupper()
    if source_letters.istitle():
        return target_letters.istitle()

    origins = ("N", "RL") if origin == "U" else (origin,)
    return any(_mixed_capitalization_matches(source, target_letters, label) for label in origins)


def _mixed_capitalization_matches(source: str, target: str, label: str) -> bool:
    """Align letters using transliteration fragments before checking their case."""
    word = source.replace("İ", "i").lower()
    source_letters = [(index, char) for index, char in enumerate(source) if _cased_letters(char)]
    # Compare the best spelling alignment with the best alignment that also
    # respects case, so a poorer spelling alignment cannot hide a moved capital.
    scores: dict[int, tuple[float, float]] = {0: (0, 0)}
    for position, (index, char) in enumerate(source_letters):
        lower = word[index]
        canonical = char
        if CYRILLIC_RE.fullmatch(char):
            canonical = _char_conversion(lower, word, index, label)
        variants = MIXED_CASE_LETTER_VARIANTS.get(lower, ()) if canonical else ()
        spellings = {_zamanalif_upper(_cased_letters(form)) for form in (canonical, *variants)}
        widths = {len(spelling) for spelling in spellings}
        upper = not char.islower()
        upper_run = upper and any(
            not source_letters[neighbor][1].islower()
            for neighbor in (position - 1, position + 1)
            if 0 <= neighbor < len(source_letters)
        )

        next_scores: dict[int, tuple[float, float]] = {}
        for start, (spelling_score, case_score) in scores.items():
            for end in range(start, min(start + 2, len(target)) + 1):
                fragment = target[start:end]
                cost = 0
                if _zamanalif_upper(fragment) not in spellings:
                    cost = 1 + min(abs(len(fragment) - width) for width in widths)
                best_spelling, best_case = next_scores.get(end, (inf, inf))
                case_matches = (
                    not fragment
                    or (not upper and fragment.islower())
                    or (upper and fragment.istitle())
                    or (upper_run and fragment.isupper())
                )
                if case_matches:
                    best_case = min(best_case, case_score + cost)
                next_scores[end] = (min(best_spelling, spelling_score + cost), best_case)
        scores = next_scores

    spelling_score, case_score = scores.get(len(target), (inf, inf))
    return spelling_score < inf and spelling_score == case_score


def _expected_character_counts(
    source: str, conversion: TokenConversion, policy: dict[str, str], *, dotted: bool,
) -> set[int]:
    source_length = _spelling_length(source)
    word = normalize_word(source)
    if conversion.source in {"literal", "unchanged"} or word in CYRILLIC_LETTER_REFERENCES:
        return {source_length}

    branches = conversion_branches(word)
    references = {branches.native_dsl, branches.loanword_dsl}
    references.discard("")
    ya_count = word.count("я")
    ts_runs = len(re.findall("ц+", word))
    reviewed = conversion.source in {"dictionary", "contextual"}
    e_count = word.count("е")
    sign_count = word.count("ь") + word.count("ъ")

    if not references:
        # A review may supply a spelling where context leaves the automatic
        # branch unresolved. Use bounds from the letters present in the source.
        fixed = source_length - sum(word.count(char) for char in "ьъц")
        minimum = fixed + sum(word.count(char) for char in "ёющ") + ts_runs
        optional = ya_count + e_count + ts_runs + sign_count
        maximum = minimum + optional
        return set(range(minimum, maximum + 1))

    # The converter can drop characters that have no mapping inside its
    # Cyrillic span. Count those characters from the source independently.
    edge_length = _spelling_length(_restore_edge_punctuation(source, ""))
    internal_literals = CYRILLIC_RE.sub("", word).replace(ZAMANALIF_APOSTROPHE, "")
    literal_length = edge_length + _spelling_length(internal_literals)
    expected: set[int] = set()
    for dsl in references:
        # Start with the longest form of я, then allow one fewer character per я.
        reference = dsl
        if DSL_DELIMITER_RE.search(dsl):
            reference = resolve_dsl(dsl, {**policy, "YA": "ya"})
        baseline = literal_length + _spelling_length(reference)
        shorter = ya_count
        longer = 0
        if reviewed:
            target = conversion.text
            assert target is not None
            # Preserve documented compact е forms and reviewed ц → s spellings.
            compact_ie = min(word.count("ие"), reference.count("iye"))
            legacy_e = min(conversion.legacy_e_glides, e_count, reference.count("ye"))
            shorter += max(compact_ie, legacy_e)
            shorter += min(ts_runs, reference.count("ts"))
            if sign_count:
                sign_change = target.count(ZAMANALIF_APOSTROPHE) - reference.count(ZAMANALIF_APOSTROPHE)
                shorter += min(sign_count, max(0, -sign_change))
                longer += min(sign_count, max(0, sign_change))
            if e_count:
                plain_e = reference.count("e") - reference.count("ye")
                review_glides = len(E_GLIDE_RE.findall(_zamanalif_upper(target)))
                reference_glides = len(E_GLIDE_RE.findall(_zamanalif_upper(reference)))
                longer += min(e_count, plain_e, max(0, review_glides - reference_glides))
        expected.update(range(baseline - shorter, baseline + longer + 1))

    if reviewed and source_length == 1 and source.isupper() and (dotted or source.endswith(".")):
        expected.add(1)
    return expected


def _punctuation_layout(value: str) -> list[str]:
    """Collect separators while ignoring discretionary breaks inside words."""
    value = WORD_INTERNAL_SOFT_HYPHEN_RE.sub("", value)
    layout = [""]
    for is_text, chars in groupby(value, _is_text_character):
        if is_text:
            layout.append("")
        else:
            layout[-1] = "".join(chars)
    return layout


def _token_punctuation_matches(
    source: str, conversion: TokenConversion, policy: dict[str, str],
) -> bool:
    target = conversion.text
    assert target is not None
    if source == target or (source.isalnum() and target.isalnum()):
        return True
    source_layout = _punctuation_layout(source)
    if source_layout != _punctuation_layout(target):
        return False
    if len(source_layout) < 3 or conversion.source not in {"dictionary", "contextual"}:
        return True

    # Anchor internal marks to the converter's letter positions when a review
    # uses the same spelling. This catches shifts within a single token.
    suggestion = conversion_branches(conversion.normalized_word).suggestion(conversion.origin)
    if not suggestion:
        return True
    reference = _restore_edge_punctuation(source, resolve_dsl(suggestion, policy))
    if _punctuation_layout(reference) != source_layout:
        return True
    reference_letters = _zamanalif_upper("".join(filter(_is_text_character, reference)))
    target_letters = _zamanalif_upper("".join(filter(_is_text_character, target)))
    return (
        reference_letters != target_letters
        or _zamanalif_upper(reference) == _zamanalif_upper(target)
    )


def resolve_training_token(
    sample_id: str,
    token_index: int,
    token: dict,
    reviewed: dict[str, ReviewedWord],
    contextual_reviews: dict[OccurrenceKey, ContextualReview],
    policy: dict[str, str],
    word_resolutions: dict[str, str],
    *,
    projected_text: str | None = None,
) -> TokenConversion:
    """Resolve one aligned token under the same policy used for training export."""
    original_text, label = token["text"], token["label"]
    text = literal_source_view(original_text) if projected_text is None else projected_text
    if len(text) != len(original_text):
        raise TrainingExportError(f"{sample_id}: literal projection changed token length")
    source_word = normalize_word(original_text)
    normalized = normalize_word(text)
    if not normalized:
        if text != original_text:
            letters = [index for index, char in enumerate(original_text) if char.isalpha()]
            literal_word = original_text[letters[0] : letters[-1] + 1].casefold()
            return TokenConversion(literal_word, text, label, "literal")
        return TokenConversion(source_word, text, label, "unchanged")
    branches = conversion_branches(normalized)
    resolution = word_resolutions.get(normalized)
    is_contextual_homonym = resolution == "contextual_homonym" or (
        token.get("homonym") is True
        and resolution not in {"N", "RL", "U"}
    )
    contextual = contextual_reviews.get(
        OccurrenceKey(sample_id, token_index)
    )
    reviewed_resolution: str | None = None
    origin = resolution if resolution in {"N", "RL", "U"} else label
    source = "automatic"
    if is_contextual_homonym and branches.state == "origin_independent":
        dsl = branches.native_dsl
    elif contextual is not None:
        if contextual.normalized_word != normalized:
            raise TrainingExportError(
                f"{sample_id}: contextual review is stale at token "
                f"{token_index}"
            )
        dsl = contextual.zamanalif_dsl
        origin, source = contextual.origin, "contextual"
    else:
        if is_contextual_homonym:
            return TokenConversion(source_word, None, origin, "contextual_homonym")

        approved = reviewed.get(normalized)
        if approved is not None:
            dsl = approved.zamanalif_dsl
            origin, source = approved.origin, "dictionary"
            reviewed_resolution = _reviewed_variant_for_policy(
                approved,
                policy,
            )
        else:
            if branches.state != "origin_independent":
                if (
                    origin == "N"
                    and vowel_harmony_class(normalized) == "mixed_front_back"
                ):
                    return TokenConversion(source_word, None, origin, "mixed_harmony_word")
                return TokenConversion(source_word, None, origin, "unreviewed_word")
            dsl = branches.suggestion(origin)
            if not dsl:
                raise TrainingExportError(
                    f"{sample_id}: converter failed for token {text!r}"
                )

    if reviewed_resolution is not None:
        resolved = reviewed_resolution
    else:
        try:
            resolved = resolve_dsl(dsl, policy)
        except DslError as exc:
            raise TrainingExportError(
                f"{sample_id}: invalid DSL for {normalized!r}: {exc}"
            ) from exc
    with_internal_punctuation = _restore_internal_word_punctuation(
        text,
        resolved,
        policy,
    )
    rendered = _apply_source_case(
        text, _restore_edge_punctuation(text, with_internal_punctuation)
    )
    return TokenConversion(source_word, rendered, origin, source, dsl.count("{{E_GLIDE|"))


def _restore_edge_punctuation(source: str, target: str) -> str:
    matches = list(CYRILLIC_RE.finditer(source))
    if not matches:
        return target
    return source[: matches[0].start()] + target + source[matches[-1].end() :]


def _restore_internal_word_punctuation(
    source: str,
    target: str,
    policy: dict[str, str],
) -> str:
    """Reinsert normalized structural quotes at their converted stem boundary."""
    matches = list(CYRILLIC_RE.finditer(source))
    if not matches:
        return target
    surface = source[matches[0].start() : matches[-1].end()]
    clean_prefix: list[str] = []
    boundaries: list[tuple[str, str]] = []
    for index, char in enumerate(surface):
        if (
            char in INTERNAL_DOUBLE_QUOTES
            and index > 0
            and index + 1 < len(surface)
            and CYRILLIC_RE.fullmatch(surface[index - 1])
            and CYRILLIC_RE.fullmatch(surface[index + 1])
            and surface[index + 1 :].casefold() in TATAR_SUFFIXES_AFTER_QUOTE
        ):
            boundaries.append(("".join(clean_prefix).casefold(), char))
            continue
        clean_prefix.append(char)
    if not boundaries:
        return target

    insertions: list[tuple[int, str]] = []
    for prefix, quote in boundaries:
        branches = conversion_branches(prefix)
        converted_prefixes = {
            resolve_dsl(dsl, policy)
            for dsl in (branches.native_dsl, branches.loanword_dsl)
            if dsl
        }
        positions = {
            len(converted)
            for converted in converted_prefixes
            if target.startswith(converted)
        }
        if len(positions) != 1:
            raise TrainingExportError(
                f"cannot preserve internal quote in token {source!r}"
            )
        insertions.append((positions.pop(), quote))

    restored = target
    for position, quote in sorted(insertions, reverse=True):
        restored = restored[:position] + quote + restored[position:]
    return restored


def _apply_source_case(source: str, target: str) -> str:
    reviewed_case = REVIEWED_JOINED_CASE_FORMS.get(source)
    if reviewed_case and _zamanalif_upper(target) == _zamanalif_upper(reviewed_case):
        return reviewed_case
    split_on_dots = source in REVIEWED_JOINED_SENTENCE_FORMS or DOTTED_INITIAL_NAME_RE.match(source)
    separators = DOTTED_CASE_SEPARATORS_RE if split_on_dots else CASE_SEPARATORS_RE
    source_parts = separators.split(source)
    target_parts = separators.split(target)
    if len(source_parts) == len(target_parts) and source_parts[1::2] == target_parts[1::2]:
        return "".join(
            target_part if index % 2 else _apply_part_case(source_parts[index], target_part)
            for index, target_part in enumerate(target_parts)
        )
    return _apply_part_case(source, target)


def _approved_identifier_case(source: str, target: str) -> str | None:
    """Preserve approved model casing without changing letters or separators."""
    for original, rendered in APPROVED_IDENTIFIER_CASE.items():
        if source.count(original) != 1:
            continue
        prefix, _, suffix = source.partition(original)
        if any(_is_text_character(char) for char in prefix + suffix):
            continue
        expected = prefix + rendered + suffix
        if target.casefold() == expected.casefold():
            return expected
    return None


def _apply_part_case(source: str, target: str) -> str:
    identifier = _approved_identifier_case(source, target)
    if identifier is not None:
        return identifier
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
        for variant in annotation.variants:
            try:
                parsed = parse_dsl(variant.zamanalif)
            except DslError as exc:
                raise TrainingExportError(
                    f"invalid reviewed variant for {word!r}: {exc}"
                ) from exc
            if parsed.has_choices:
                raise TrainingExportError(
                    f"reviewed variant for {word!r} contains unresolved DSL"
                )


def _reviewed_variant_for_policy(
    annotation: ReviewedWord,
    policy: dict[str, str],
) -> str | None:
    if len(annotation.variants) == 1:
        return annotation.variants[0].zamanalif
    for variant in annotation.variants:
        if any(
            all(policy.get(rule_id) == option_id for rule_id, option_id in candidate)
            for candidate in variant.policies
        ):
            return variant.zamanalif
    return None


def _validate_resolved_sentence(sample_id: str, value: str) -> None:
    if DSL_DELIMITER_RE.search(value):
        raise TrainingExportError(f"{sample_id}: unresolved DSL remained in output")
    for index, char in enumerate(value):
        if (
            char.isascii()
            or "CYRILLIC" not in unicodedata.name(char, "")
            or unicodedata.category(char)[0] not in "LM"
        ):
            continue
        if (
            char in CYRILLIC_LETTER_REFERENCES
            and (index == 0 or not re.match(r"\w", value[index - 1]))
            and (index + 1 == len(value) or not re.match(r"\w", value[index + 1]))
        ):
            continue
        raise TrainingExportError(
            f"{sample_id}: unresolved Cyrillic character remained in output: {char!r}"
        )


def _temporary_path(destination: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(raw_path)
