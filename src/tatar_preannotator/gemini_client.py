from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Callable, Protocol


class GeminiError(Exception):
    """Base class for Gemini request failures."""


class GeminiTimeoutError(GeminiError):
    """The request exceeded the configured timeout."""


class GeminiShutdownError(GeminiError):
    """The request was interrupted by a forced or timed-out shutdown."""

    def __init__(self, message: str, *, forced: bool = False):
        super().__init__(message)
        self.forced = forced


class GeminiEmptyResponseError(GeminiError):
    """Gemini returned a response object without generated text."""


class GeminiOverloadedError(GeminiError):
    """Gemini returned a retryable overload/server error."""


class GeminiQuotaError(GeminiError):
    """The current API key has exhausted longer-lived quota."""


class GeminiRateLimitError(GeminiError):
    """Gemini rejected the request due to short-window request rate."""


class GeminiFatalError(GeminiError):
    """A non-retryable Gemini request/configuration error."""


class GeminiClientProtocol(Protocol):
    def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        timeout_seconds: int,
        shutdown_requested: Callable[[], bool] | None = None,
        force_shutdown: Callable[[], bool] | None = None,
        shutdown_deadline: Callable[[], float | None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> str:
        """Generate text from Gemini."""


@dataclass
class GoogleGeminiClient:
    """Small wrapper around the official google-genai SDK."""

    def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        timeout_seconds: int,
        shutdown_requested: Callable[[], bool] | None = None,
        force_shutdown: Callable[[], bool] | None = None,
        shutdown_deadline: Callable[[], float | None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> str:
        if now is None:
            from time import monotonic

            now = monotonic
        shutdown_requested = shutdown_requested or (lambda: False)
        force_shutdown = force_shutdown or (lambda: False)
        shutdown_deadline = shutdown_deadline or (lambda: None)
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_generate_sync, api_key, model, prompt)
        request_deadline = now() + timeout_seconds
        try:
            while True:
                if force_shutdown():
                    future.cancel()
                    raise GeminiShutdownError("Gemini request cancelled by forced shutdown", forced=True)
                deadline = request_deadline
                graceful_deadline = shutdown_deadline() if shutdown_requested() else None
                if graceful_deadline is not None:
                    deadline = min(deadline, graceful_deadline)
                remaining = deadline - now()
                if remaining <= 0:
                    future.cancel()
                    if graceful_deadline is not None and graceful_deadline <= request_deadline:
                        raise GeminiShutdownError("Gemini request exceeded graceful shutdown timeout")
                    raise GeminiTimeoutError("Gemini request timed out")
                try:
                    return future.result(timeout=min(0.25, remaining))
                except FutureTimeoutError:
                    continue
        except GeminiError:
            raise
        except FutureTimeoutError as exc:
            future.cancel()
            raise GeminiTimeoutError("Gemini request timed out") from exc
        except Exception as exc:
            raise _classify_exception(exc) from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def _generate_sync(api_key: str, model: str, prompt: str) -> str:
    try:
        from google import genai
    except ImportError as exc:
        raise GeminiFatalError("google-genai is not installed") from exc
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(model=model, contents=prompt)
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text:
        raise GeminiEmptyResponseError(
            "Gemini response did not contain text: " + _describe_empty_response(response)
        )
    return text


def _classify_exception(exc: Exception) -> GeminiError:
    if isinstance(exc, GeminiError):
        return exc
    text = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if _looks_like_rate_limit(text) and "quota" not in text:
        return GeminiRateLimitError(str(exc))
    if "quota" in text or "resource_exhausted" in text:
        return GeminiQuotaError(str(exc))
    if status == 429:
        return GeminiRateLimitError(str(exc))
    if status == 503 or "overloaded" in text or "unavailable" in text:
        return GeminiOverloadedError(str(exc))
    if isinstance(status, int) and 500 <= status < 600:
        return GeminiOverloadedError(str(exc))
    return GeminiFatalError(str(exc))


def _looks_like_rate_limit(text: str) -> bool:
    return (
        "rate limit" in text
        or "requests per minute" in text
        or "per minute" in text
        or "rpm" in text
    )


def _describe_empty_response(response: object) -> str:
    parts: list[str] = []
    prompt_feedback = getattr(response, "prompt_feedback", None)
    if prompt_feedback is not None:
        parts.append(f"prompt_feedback={prompt_feedback!r}")
    candidates = getattr(response, "candidates", None)
    if candidates:
        candidate_parts: list[str] = []
        for index, candidate in enumerate(candidates[:3]):
            finish_reason = getattr(candidate, "finish_reason", None)
            safety_ratings = getattr(candidate, "safety_ratings", None)
            candidate_parts.append(
                f"candidate[{index}].finish_reason={finish_reason!r} "
                f"safety_ratings={safety_ratings!r}"
            )
        parts.append("; ".join(candidate_parts))
    if not parts:
        parts.append(repr(response)[:500])
    return " | ".join(parts)[:1000]


class KeyRing:
    """Rotate through configured keys and remember exhausted keys for this run."""

    def __init__(self, keys: tuple[str, ...], exhausted_keys: set[str] | None = None):
        self._keys = keys
        self._index = 0
        exhausted_keys = exhausted_keys or set()
        self._exhausted: set[int] = {
            index for index, key in enumerate(keys) if key in exhausted_keys
        }

    def current(self) -> str | None:
        if len(self._exhausted) >= len(self._keys):
            return None
        for _ in self._keys:
            if self._index not in self._exhausted:
                return self._keys[self._index]
            self._index = (self._index + 1) % len(self._keys)
        return None

    def mark_exhausted(self) -> str | None:
        if not self._keys:
            return None
        key = self._keys[self._index]
        self._exhausted.add(self._index)
        self._index = (self._index + 1) % len(self._keys)
        return key
