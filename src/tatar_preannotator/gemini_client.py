from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Protocol


class GeminiError(Exception):
    """Base class for Gemini request failures."""


class GeminiTimeoutError(GeminiError):
    """The request exceeded the configured timeout."""


class GeminiOverloadedError(GeminiError):
    """Gemini returned a retryable overload/server error."""


class GeminiQuotaError(GeminiError):
    """The current API key is quota or rate limited."""


class GeminiFatalError(GeminiError):
    """A non-retryable Gemini request/configuration error."""


class GeminiClientProtocol(Protocol):
    def generate(self, *, api_key: str, model: str, prompt: str, timeout_seconds: int) -> str:
        """Generate text from Gemini."""


@dataclass
class GoogleGeminiClient:
    """Small wrapper around the official google-genai SDK."""

    def generate(self, *, api_key: str, model: str, prompt: str, timeout_seconds: int) -> str:
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_generate_sync, api_key, model, prompt)
        try:
            return future.result(timeout=timeout_seconds)
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
        raise GeminiFatalError("Gemini response did not contain text")
    return text


def _classify_exception(exc: Exception) -> GeminiError:
    if isinstance(exc, GeminiError):
        return exc
    text = str(exc).lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 429 or "quota" in text or "rate limit" in text or "resource_exhausted" in text:
        return GeminiQuotaError(str(exc))
    if status == 503 or "overloaded" in text or "unavailable" in text:
        return GeminiOverloadedError(str(exc))
    if isinstance(status, int) and 500 <= status < 600:
        return GeminiOverloadedError(str(exc))
    return GeminiFatalError(str(exc))


class KeyRing:
    """Rotate through configured keys and remember exhausted keys for this run."""

    def __init__(self, keys: tuple[str, ...]):
        self._keys = keys
        self._index = 0
        self._exhausted: set[int] = set()

    def current(self) -> str | None:
        if len(self._exhausted) >= len(self._keys):
            return None
        for _ in self._keys:
            if self._index not in self._exhausted:
                return self._keys[self._index]
            self._index = (self._index + 1) % len(self._keys)
        return None

    def mark_exhausted(self) -> None:
        self._exhausted.add(self._index)
        self._index = (self._index + 1) % len(self._keys)
