from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .schema import ALLOWED_LABELS, Sample, ValidationResult


PUNCT_ONLY_RE = re.compile(r"^\W+$", re.UNICODE)


def validate_response(raw: str, samples: Iterable[Sample]) -> ValidationResult:
    """Parse and validate a Gemini JSON response for one requested batch."""
    originals = {sample.id: sample.text for sample in samples}
    errors: list[str] = []
    warnings: list[str] = []
    normalized: list[dict[str, Any]] = []
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        return ValidationResult(ok=False, errors=[f"invalid JSON: {exc}"])
    if not isinstance(data, list):
        return ValidationResult(ok=False, errors=["response must be a JSON array"])

    seen: set[str] = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(f"item {index} must be an object")
            continue
        extra = sorted(set(item) - {"id", "tatar", "tokens"})
        if extra:
            warnings.append(f"item {item.get('id', index)} has ignored fields: {', '.join(extra)}")
        sample_id = item.get("id")
        if not isinstance(sample_id, str) or not sample_id:
            errors.append(f"item {index} has invalid id")
            continue
        if sample_id in seen:
            errors.append(f"duplicate id: {sample_id}")
            continue
        seen.add(sample_id)
        if sample_id not in originals:
            errors.append(f"unknown id: {sample_id}")
            continue
        tatar = item.get("tatar")
        if not isinstance(tatar, bool):
            errors.append(f"{sample_id}: tatar must be boolean")
            continue
        tokens = item.get("tokens")
        if not isinstance(tokens, list):
            errors.append(f"{sample_id}: tokens must be a list")
            continue
        if not tatar and tokens:
            errors.append(f"{sample_id}: tatar=false requires empty tokens")
            continue
        item_error_count = len(errors)
        norm_tokens = _validate_tokens(sample_id, originals[sample_id], tokens, errors, warnings)
        if len(errors) > item_error_count:
            continue
        normalized.append({"id": sample_id, "tatar": tatar, "tokens": norm_tokens if tatar else []})

    missing = sorted(set(originals) - seen)
    errors.extend(f"missing id: {sample_id}" for sample_id in missing)
    return ValidationResult(ok=not errors, items=normalized if not errors else [], errors=errors, warnings=warnings)


def _validate_tokens(
    sample_id: str,
    sentence: str,
    tokens: list[Any],
    errors: list[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    cursor = 0
    for token_index, token in enumerate(tokens):
        if not isinstance(token, dict):
            errors.append(f"{sample_id}: token {token_index} must be an object")
            continue
        extra = sorted(set(token) - {"text", "label", "homonym"})
        if extra:
            warnings.append(
                f"{sample_id}: token {token_index} has ignored fields: {', '.join(extra)}"
            )
        text = token.get("text")
        label = token.get("label")
        if not isinstance(text, str) or not text:
            errors.append(f"{sample_id}: token {token_index} has invalid text")
            continue
        if PUNCT_ONLY_RE.match(text):
            errors.append(f"{sample_id}: token {token_index} is punctuation-only")
            continue
        if label not in ALLOWED_LABELS:
            errors.append(f"{sample_id}: token {token_index} has invalid label: {label}")
            continue
        homonym = token.get("homonym")
        if homonym is not None:
            if homonym is not True:
                errors.append(f"{sample_id}: token {token_index} homonym must be exactly true")
                continue
            if label != "RL":
                errors.append(f"{sample_id}: token {token_index} homonym is only valid on RL")
                continue
        found = sentence.find(text, cursor)
        if found < 0:
            errors.append(f"{sample_id}: token {token_index} text is missing or out of order: {text}")
            continue
        cursor = found + len(text)
        normalized_token: dict[str, Any] = {"text": text, "label": label}
        if homonym is True:
            normalized_token["homonym"] = True
        normalized.append(normalized_token)
    return normalized
