from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import db
from .gemini_client import (
    GeminiClientProtocol,
    GeminiEmptyResponseError,
    GeminiFatalError,
    GeminiOverloadedError,
    GeminiQuotaError,
    GeminiRateLimitError,
    GeminiShutdownError,
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


RATE_LIMIT_RETRY_SLEEP_SECONDS = 60


def run_annotation(
    *,
    db_path: str,
    config: PreannotationConfig,
    client: GeminiClientProtocol,
    sleep: Callable[[float], None],
    log: Callable[[str], None] | None = None,
    now: Callable[[], float] | None = None,
    shutdown_requested: Callable[[], bool] | None = None,
    force_shutdown: Callable[[], bool] | None = None,
    shutdown_deadline: Callable[[], float | None] | None = None,
    key_shuffle: Callable[[list[str]], None] | None = None,
) -> AnnotateSummary:
    """Run adaptive Gemini pre-annotation until the configured stop condition is reached."""
    if now is None:
        from time import monotonic

        now = monotonic
    shutdown_requested = shutdown_requested or (lambda: False)
    force_shutdown = force_shutdown or (lambda: False)
    shutdown_deadline = shutdown_deadline or (lambda: None)
    batch_size = config.initial_batch_size
    consecutive_successes = 0
    last_request_started_at: float | None = None
    minimum_request_interval = 60 / config.requests_per_minute
    conn = db.connect(db_path)
    try:
        db.reset_processing(conn)
        exhausted_keys_result = _read_exhausted_keys(config.exhausted_keys_path)
        if isinstance(exhausted_keys_result, str):
            error = f"exhausted key file error: {exhausted_keys_result}"
            _log(log, error)
            return _summary(conn, "fatal_error", error=error)
        keys = KeyRing(
            config.api_keys,
            exhausted_keys=exhausted_keys_result,
            shuffle=key_shuffle,
        )
        if exhausted_keys_result:
            _log(
                log,
                "loaded exhausted Gemini keys: "
                f"count={len(exhausted_keys_result)} path={config.exhausted_keys_path}",
            )
        while True:
            current_annotated = db.annotated_count(conn)
            if current_annotated >= config.target_annotated_count:
                return _summary(conn, "target_reached")
            if force_shutdown():
                return _summary(conn, "forced_shutdown", error="forced shutdown requested")
            if shutdown_requested():
                return _summary(conn, "shutdown_requested")
            remaining = config.target_annotated_count - current_annotated
            request_size = min(batch_size, remaining)
            samples = db.next_pending(conn, request_size)
            if not samples:
                return _summary(conn, "no_pending")
            key = keys.current()
            if key is None:
                return _summary(conn, "all_keys_exhausted")
            last_request_started_at = _throttle_requests(
                last_request_started_at,
                minimum_request_interval,
                sleep=sleep,
                now=now,
                log=log,
            )
            if force_shutdown():
                return _summary(conn, "forced_shutdown", error="forced shutdown requested")
            if shutdown_requested():
                return _summary(conn, "shutdown_requested")
            db.mark_processing(conn, samples)
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
                    shutdown_requested=shutdown_requested,
                    force_shutdown=force_shutdown,
                    shutdown_deadline=shutdown_deadline,
                    now=now,
                )
            except GeminiOverloadedError as exc:
                _log(
                    log,
                    f"batch overloaded: sleep={config.overload_sleep_seconds}s error={_safe_error(exc)}",
                )
                sleep(config.overload_sleep_seconds)
                db.mark_pending(conn, samples, f"Gemini overloaded: {_safe_error(exc)}")
                continue
            except GeminiRateLimitError as exc:
                _log(
                    log,
                    f"Gemini rate limit: sleep={RATE_LIMIT_RETRY_SLEEP_SECONDS}s error={_safe_error(exc)}",
                )
                db.mark_pending(conn, samples, f"Gemini rate limit: {_safe_error(exc)}")
                sleep(RATE_LIMIT_RETRY_SLEEP_SECONDS)
                continue
            except GeminiQuotaError as exc:
                _log(log, f"Gemini key exhausted: {_safe_error(exc)}")
                exhausted_key = keys.mark_exhausted()
                if exhausted_key is not None:
                    write_error = _write_exhausted_key(
                        config.exhausted_keys_path, exhausted_key
                    )
                    if write_error is not None:
                        error = f"exhausted key file error: {write_error}"
                        _log(log, error)
                        db.mark_pending(conn, samples, error)
                        return _summary(conn, "fatal_error", error=error)
                    _log(log, f"persisted exhausted Gemini key to {config.exhausted_keys_path}")
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
            except GeminiShutdownError as exc:
                error = _safe_error(exc)
                _log(log, error)
                db.mark_pending(conn, samples, error)
                if exc.forced:
                    return _summary(conn, "forced_shutdown", error=error)
                return _summary(conn, "shutdown_requested", error=error)
            except GeminiEmptyResponseError as exc:
                _log(log, f"empty Gemini response: {_safe_error(exc)}")
                batch_size, consecutive_successes = _handle_batch_failure(
                    conn, samples, batch_size, "empty response", exc
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
            for item in validation.items:
                _log(log, _format_annotation_log_item(item))
            if validation.warnings:
                _log(log, "validation warnings: " + "; ".join(validation.warnings))
            consecutive_successes += 1
            if consecutive_successes >= 3:
                batch_size = max(1, math.ceil(batch_size * 1.5))
                _log(log, f"batch size increased to {batch_size}")
                consecutive_successes = 0
            if force_shutdown():
                return _summary(conn, "forced_shutdown", error="forced shutdown requested")
            if shutdown_requested():
                return _summary(conn, "shutdown_requested")
    finally:
        conn.close()


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


def _throttle_requests(
    last_request_started_at: float | None,
    minimum_request_interval: float,
    *,
    sleep: Callable[[float], None],
    now: Callable[[], float],
    log: Callable[[str], None] | None,
) -> float:
    current_time = now()
    if last_request_started_at is None:
        return current_time
    elapsed = current_time - last_request_started_at
    wait_seconds = minimum_request_interval - elapsed
    if wait_seconds > 0:
        _log(log, f"rate limit: sleeping {wait_seconds:.1f}s before next Gemini request")
        sleep(wait_seconds)
        current_time = now()
    return current_time


def _summary(conn, reason: str, *, error: str | None = None) -> AnnotateSummary:
    return AnnotateSummary(
        annotated_count=db.annotated_count(conn),
        pending_count=db.pending_count(conn),
        stopped_reason=reason,
        error=error,
    )


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:500]


def _format_annotation_log_item(item: dict) -> str:
    tokens = json.dumps(item.get("tokens") or [], ensure_ascii=False, separators=(",", ":"))
    return (
        "{\n"
        f'  "id": {json.dumps(item["id"], ensure_ascii=False)},\n'
        f'  "tatar": {str(item["tatar"]).lower()},\n'
        f'  "tokens": {tokens}\n'
        "}"
    )


def _read_exhausted_keys(path: str) -> set[str] | str:
    key_path = Path(path)
    if not key_path.exists():
        return set()
    try:
        data = json.loads(key_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return str(exc)
    except json.JSONDecodeError as exc:
        return f"invalid JSON in {key_path}: {exc}"
    if not isinstance(data, dict):
        return f"{key_path} must contain a JSON object"
    keys = data.get("exhausted_keys")
    if not isinstance(keys, list):
        return f"{key_path} must contain an exhausted_keys list"
    exhausted = {key for key in keys if isinstance(key, str) and key}
    if len(exhausted) != len(keys):
        return f"{key_path} exhausted_keys must contain only non-empty strings"
    return exhausted


def _write_exhausted_key(path: str, key: str) -> str | None:
    existing = _read_exhausted_keys(path)
    if isinstance(existing, str):
        return existing
    existing.add(key)
    key_path = Path(path)
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = key_path.with_suffix(key_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps({"exhausted_keys": sorted(existing)}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(key_path)
    except OSError as exc:
        return str(exc)
    return None


def _log(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)
