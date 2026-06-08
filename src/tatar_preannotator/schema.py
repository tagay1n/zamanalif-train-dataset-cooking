from __future__ import annotations

from dataclasses import dataclass, field


ALLOWED_LABELS = frozenset({"RL", "N", "U"})


@dataclass(frozen=True)
class PreannotationConfig:
    model: str
    api_keys: tuple[str, ...]
    exhausted_keys_path: str
    requests_per_minute: int
    graceful_shutdown_timeout_seconds: int
    initial_batch_size: int
    request_timeout_seconds: int
    overload_sleep_seconds: int
    target_annotated_count: int


@dataclass(frozen=True)
class Sample:
    id: str
    text: str


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    items: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
