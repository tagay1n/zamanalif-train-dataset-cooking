from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from . import db
from .gemini_client import (
    GeminiClientProtocol,
    GeminiFatalError,
    GeminiOverloadedError,
    GeminiQuotaError,
    GeminiTimeoutError,
    KeyRing,
)
from .prompt import build_prompt
from .schema import PreannotationConfig, Sample
from .validate import validate_response


@dataclass(frozen=True)
class AnnotateSummary:
    annotated_count: int
    pending_count: int
    stopped_reason: str


def run_annotation(
    *,
    db_path: str,
    config: PreannotationConfig,
    client: GeminiClientProtocol,
    sleep: Callable[[int], None],
) -> AnnotateSummary:
    """Run adaptive Gemini pre-annotation until the configured stop condition is reached."""
    batch_size = config.initial_batch_size
    consecutive_successes = 0
    keys = KeyRing(config.api_keys)
    with db.connect(db_path) as conn:
        db.reset_processing(conn)
        while True:
            current_annotated = db.annotated_count(conn)
            if current_annotated >= config.target_annotated_count:
                return _summary(conn, "target_reached")
            remaining = config.target_annotated_count - current_annotated
            request_size = min(batch_size, remaining)
            samples = db.next_pending(conn, request_size)
            if not samples:
                return _summary(conn, "no_pending")
            db.mark_processing(conn, samples)
            key = keys.current()
            if key is None:
                db.mark_pending(conn, samples, "all Gemini keys exhausted")
                return _summary(conn, "all_keys_exhausted")
            prompt = build_prompt(samples)
            try:
                raw = client.generate(
                    api_key=key,
                    model=config.model,
                    prompt=prompt,
                    timeout_seconds=config.request_timeout_seconds,
                )
            except GeminiOverloadedError as exc:
                sleep(config.overload_sleep_seconds)
                db.mark_pending(conn, samples, f"Gemini overloaded: {_safe_error(exc)}")
                continue
            except GeminiQuotaError as exc:
                keys.mark_exhausted()
                db.mark_pending(conn, samples, f"Gemini key exhausted: {_safe_error(exc)}")
                if keys.current() is None:
                    return _summary(conn, "all_keys_exhausted")
                continue
            except GeminiTimeoutError as exc:
                batch_size, consecutive_successes = _handle_batch_failure(
                    conn, samples, batch_size, "timeout", exc
                )
                continue
            except GeminiFatalError as exc:
                db.mark_pending(conn, samples, f"fatal Gemini error: {_safe_error(exc)}")
                return _summary(conn, "fatal_error")

            validation = validate_response(raw, samples)
            if not validation.ok:
                batch_size, consecutive_successes = _handle_batch_failure(
                    conn,
                    samples,
                    batch_size,
                    "invalid response: " + "; ".join(validation.errors),
                    None,
                )
                continue
            db.save_annotations(conn, validation.items)
            consecutive_successes += 1
            if consecutive_successes >= 3:
                batch_size = max(1, math.ceil(batch_size * 1.5))
                consecutive_successes = 0


def _handle_batch_failure(
    conn,
    samples: list[Sample],
    batch_size: int,
    message: str,
    exc: Exception | None,
) -> tuple[int, int]:
    error = message if exc is None else f"{message}: {_safe_error(exc)}"
    if len(samples) == 1 or batch_size == 1:
        db.mark_unprocessable(conn, samples[0], error)
        return 1, 0
    db.mark_pending(conn, samples, error)
    return max(1, math.ceil(batch_size / 2)), 0


def _summary(conn, reason: str) -> AnnotateSummary:
    return AnnotateSummary(
        annotated_count=db.annotated_count(conn),
        pending_count=db.pending_count(conn),
        stopped_reason=reason,
    )


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:500]

