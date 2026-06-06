from __future__ import annotations

import math
import json
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
    error: str | None = None


def run_annotation(
    *,
    db_path: str,
    config: PreannotationConfig,
    client: GeminiClientProtocol,
    sleep: Callable[[int], None],
    log: Callable[[str], None] | None = None,
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
            _log(
                log,
                "batch start: "
                f"size={len(samples)} batch_size={batch_size} "
                f"annotated={current_annotated}/{config.target_annotated_count} "
                f"first_id={samples[0].id} last_id={samples[-1].id}",
            )
            prompt = build_prompt(samples)
            try:
                raw = client.generate(
                    api_key=key,
                    model=config.model,
                    prompt=prompt,
                    timeout_seconds=config.request_timeout_seconds,
                )
            except GeminiOverloadedError as exc:
                _log(
                    log,
                    f"batch overloaded: sleep={config.overload_sleep_seconds}s error={_safe_error(exc)}",
                )
                sleep(config.overload_sleep_seconds)
                db.mark_pending(conn, samples, f"Gemini overloaded: {_safe_error(exc)}")
                continue
            except GeminiQuotaError as exc:
                _log(log, f"Gemini key exhausted: {_safe_error(exc)}")
                keys.mark_exhausted()
                db.mark_pending(conn, samples, f"Gemini key exhausted: {_safe_error(exc)}")
                if keys.current() is None:
                    return _summary(conn, "all_keys_exhausted")
                continue
            except GeminiTimeoutError as exc:
                _log(log, f"batch timeout: {_safe_error(exc)}")
                batch_size, consecutive_successes = _handle_batch_failure(
                    conn, samples, batch_size, "timeout", exc
                )
                _log(log, f"batch size reduced to {batch_size}")
                continue
            except GeminiFatalError as exc:
                error = f"fatal Gemini error: {_safe_error(exc)}"
                _log(log, error)
                db.mark_pending(conn, samples, error)
                return _summary(conn, "fatal_error", error=error)

            validation = validate_response(raw, samples)
            if not validation.ok:
                _log(log, "invalid Gemini response: " + "; ".join(validation.errors))
                batch_size, consecutive_successes = _handle_batch_failure(
                    conn,
                    samples,
                    batch_size,
                    "invalid response: " + "; ".join(validation.errors),
                    None,
                )
                _log(log, f"batch size reduced to {batch_size}")
                continue
            db.save_annotations(conn, validation.items)
            _log(log, f"batch annotated: count={len(validation.items)}")
            _log(log, json.dumps(validation.items, ensure_ascii=False, indent=2))
            if validation.warnings:
                _log(log, "validation warnings: " + "; ".join(validation.warnings))
            consecutive_successes += 1
            if consecutive_successes >= 3:
                batch_size = max(1, math.ceil(batch_size * 1.5))
                _log(log, f"batch size increased to {batch_size}")
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


def _summary(conn, reason: str, *, error: str | None = None) -> AnnotateSummary:
    return AnnotateSummary(
        annotated_count=db.annotated_count(conn),
        pending_count=db.pending_count(conn),
        stopped_reason=reason,
        error=error,
    )


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:500]


def _log(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)
