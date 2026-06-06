from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .schema import PreannotationConfig


def load_config(path: str | Path) -> PreannotationConfig:
    """Load required Gemini/preannotation settings from config.yaml."""
    path = Path(path)
    if not path.exists():
        raise ValueError(f"config file does not exist: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("config must be a YAML mapping")

    gemini = _mapping(data.get("gemini"), "gemini")
    preannotation = _mapping(data.get("preannotation"), "preannotation")
    model = _required_str(gemini, "model", "gemini.model")
    api_keys_value = gemini.get("api_keys")
    if not isinstance(api_keys_value, list) or not api_keys_value:
        raise ValueError("gemini.api_keys must be a non-empty list")
    api_keys = tuple(str(key).strip() for key in api_keys_value if str(key).strip())
    if not api_keys:
        raise ValueError("gemini.api_keys must contain at least one non-empty key")

    return PreannotationConfig(
        model=model,
        api_keys=api_keys,
        initial_batch_size=_required_positive_int(
            preannotation, "initial_batch_size", "preannotation.initial_batch_size"
        ),
        request_timeout_seconds=_required_positive_int(
            preannotation, "request_timeout_seconds", "preannotation.request_timeout_seconds"
        ),
        overload_sleep_seconds=_required_positive_int(
            preannotation, "overload_sleep_seconds", "preannotation.overload_sleep_seconds"
        ),
        target_annotated_count=_required_positive_int(
            preannotation, "target_annotated_count", "preannotation.target_annotated_count"
        ),
    )


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def _required_str(data: dict[str, Any], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value.strip()


def _required_positive_int(data: dict[str, Any], key: str, path: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{path} must be a positive integer")
    return value

