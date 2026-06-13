from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tatar_preannotator.annotate import run_annotation
from tatar_preannotator.gemini_client import (
    GeminiEmptyResponseError,
    GeminiFatalError,
    GeminiOverloadedError,
    GeminiQuotaError,
    GeminiRateLimitError,
    GeminiShutdownError,
    KeyRing,
    _classify_exception,
)
from tatar_preannotator.schema import PreannotationConfig
from zamanalif_selector.cli import _write_selected_sqlite


class FakeClient:
    def __init__(self, actions: list[object]):
        self.actions = list(actions)
        self.calls: list[tuple[str, str]] = []

    def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        timeout_seconds: int,
        **kwargs,
    ) -> str:
        self.calls.append((api_key, model))
        action = self.actions.pop(0)
        if callable(action):
            return str(action(kwargs))
        if isinstance(action, Exception):
            raise action
        return str(action)


class AnnotateTests(unittest.TestCase):
    def test_gemini_429_rpm_error_is_not_key_exhaustion(self) -> None:
        class ApiError(Exception):
            status_code = 429

        self.assertIsInstance(
            _classify_exception(ApiError("requests per minute exceeded")),
            GeminiRateLimitError,
        )
        self.assertIsInstance(
            _classify_exception(ApiError("quota exceeded")),
            GeminiQuotaError,
        )

    def test_successful_run_saves_annotations(self) -> None:
        logs: list[str] = []
        with _db_path(
            [
                {"id": "doc-a", "sentence": "Казан."},
                {"id": "doc-b", "sentence": "Проект."},
            ]
        ) as path:
            response = _response(
                [
                    {"id": "sent_000001", "tatar": True, "tokens": [{"text": "Казан", "label": "N"}]},
                    {
                        "id": "sent_000002",
                        "tatar": True,
                        "tokens": [{"text": "Проект", "label": "RL"}],
                    },
                ]
            )
            summary = run_annotation(
                db_path=path,
                config=_config(target=2, batch_size=2),
                client=FakeClient([response]),
                sleep=lambda seconds: None,
                log=logs.append,
            )

            rows = _states(path)

        self.assertEqual(summary.stopped_reason, "target_reached")
        self.assertEqual([row["status"] for row in rows], ["annotated", "annotated"])
        self.assertEqual(json.loads(rows[0]["tokens_json"]), [{"label": "N", "text": "Казан"}])
        joined_logs = "\n".join(logs)
        self.assertIn("batch start: size=2", joined_logs)
        self.assertIn("batch annotated: count=2", joined_logs)
        self.assertIn('"id": "sent_000001"', joined_logs)
        self.assertIn('"tatar": true', joined_logs)
        self.assertIn('"tokens": [{"text":"Казан","label":"N"}]', joined_logs)
        self.assertIn('"tokens": [{"text":"Проект","label":"RL"}]', joined_logs)
        self.assertNotIn("sentence:", joined_logs)

    def test_shutdown_requested_before_batch_does_not_mark_processing(self) -> None:
        with _db_path([{"id": "doc-a", "sentence": "Казан."}]) as path:
            client = FakeClient([])
            summary = run_annotation(
                db_path=path,
                config=_config(target=1, batch_size=1),
                client=client,
                sleep=lambda seconds: None,
                shutdown_requested=lambda: True,
            )
            status = _status(path, "sent_000001")

        self.assertEqual(summary.stopped_reason, "shutdown_requested")
        self.assertEqual(client.calls, [])
        self.assertEqual(status, "pending")

    def test_quota_rotates_to_next_key(self) -> None:
        with _db_path([{"id": "doc-a", "sentence": "Казан."}]) as path:
            exhausted_path = str(Path(path).parent / "exhausted_keys.json")
            client = FakeClient(
                [
                    GeminiQuotaError("quota"),
                    _response(
                        [
                            {
                                "id": "sent_000001",
                                "tatar": True,
                                "tokens": [{"text": "Казан", "label": "N"}],
                            }
                        ]
                    ),
                ]
            )

            summary = run_annotation(
                db_path=path,
                config=_config(
                    target=1,
                    batch_size=1,
                    keys=("key-a", "key-b"),
                    exhausted_keys_path=exhausted_path,
                ),
                client=client,
                sleep=lambda seconds: None,
                key_shuffle=_keep_key_order,
            )
            exhausted_data = json.loads(Path(exhausted_path).read_text(encoding="utf-8"))

        self.assertEqual(summary.stopped_reason, "target_reached")
        self.assertEqual(client.calls, [("key-a", "model-a"), ("key-b", "model-a")])
        self.assertEqual(exhausted_data, {"exhausted_keys": ["key-a"]})

    def test_startup_excludes_persisted_exhausted_keys(self) -> None:
        with _db_path([{"id": "doc-a", "sentence": "Казан."}]) as path:
            exhausted_path = Path(path).parent / "exhausted_keys.json"
            exhausted_path.write_text(
                json.dumps({"exhausted_keys": ["key-a"]}),
                encoding="utf-8",
            )
            client = FakeClient(
                [
                    _response(
                        [
                            {
                                "id": "sent_000001",
                                "tatar": True,
                                "tokens": [{"text": "Казан", "label": "N"}],
                            }
                        ]
                    )
                ]
            )

            summary = run_annotation(
                db_path=path,
                config=_config(
                    target=1,
                    batch_size=1,
                    keys=("key-a", "key-b"),
                    exhausted_keys_path=str(exhausted_path),
                ),
                client=client,
                sleep=lambda seconds: None,
                key_shuffle=_keep_key_order,
            )

        self.assertEqual(summary.stopped_reason, "target_reached")
        self.assertEqual(client.calls, [("key-b", "model-a")])

    def test_key_ring_shuffles_available_non_exhausted_keys(self) -> None:
        seen_available: list[list[str]] = []

        def reverse(keys: list[str]) -> None:
            seen_available.append(list(keys))
            keys.reverse()

        keys = KeyRing(
            ("key-a", "key-b", "key-c"),
            exhausted_keys={"key-b"},
            shuffle=reverse,
        )

        self.assertEqual(seen_available, [["key-a", "key-c"]])
        self.assertEqual(keys.current(), "key-c")
        self.assertEqual(keys.mark_exhausted(), "key-c")
        self.assertEqual(keys.current(), "key-a")

    def test_overload_sleeps_and_retries_without_exhausting_key(self) -> None:
        sleeps: list[int] = []
        with _db_path([{"id": "doc-a", "sentence": "Казан."}]) as path:
            client = FakeClient(
                [
                    GeminiOverloadedError("503"),
                    _response(
                        [
                            {
                                "id": "sent_000001",
                                "tatar": True,
                                "tokens": [{"text": "Казан", "label": "N"}],
                            }
                        ]
                    ),
                ]
            )

            summary = run_annotation(
                db_path=path,
                config=_config(target=1, batch_size=1, keys=("key-a", "key-b")),
                client=client,
                sleep=sleeps.append,
                key_shuffle=_keep_key_order,
            )

        self.assertEqual(summary.stopped_reason, "target_reached")
        self.assertEqual(sleeps, [7])
        self.assertEqual(client.calls, [("key-a", "model-a"), ("key-a", "model-a")])

    def test_global_rate_limit_sleeps_between_fast_requests(self) -> None:
        clock = FakeClock()
        logs: list[str] = []
        statuses_during_sleep: list[str] = []
        with _db_path(
            [
                {"id": "doc-a", "sentence": "Казан."},
                {"id": "doc-b", "sentence": "Проект."},
            ]
        ) as path:
            client = FakeClient(
                [
                    _response(
                        [
                            {
                                "id": "sent_000001",
                                "tatar": True,
                                "tokens": [{"text": "Казан", "label": "N"}],
                            }
                        ]
                    ),
                    _response(
                        [
                            {
                                "id": "sent_000002",
                                "tatar": True,
                                "tokens": [{"text": "Проект", "label": "RL"}],
                            }
                        ]
                    ),
                ]
            )

            def sleep_and_check_status(seconds: float) -> None:
                statuses_during_sleep.append(_status(path, "sent_000002"))
                clock.sleep(seconds)

            summary = run_annotation(
                db_path=path,
                config=_config(target=2, batch_size=1, requests_per_minute=5),
                client=client,
                sleep=sleep_and_check_status,
                now=clock.now,
                log=logs.append,
            )

        self.assertEqual(summary.stopped_reason, "target_reached")
        self.assertEqual(clock.sleeps, [12.0])
        self.assertEqual(statuses_during_sleep, ["pending"])
        self.assertIn("rate limit: sleeping 12.0s", "\n".join(logs))

    def test_shutdown_during_throttle_stops_before_processing_next_batch(self) -> None:
        clock = FakeClock()
        shutdown = {"requested": False}
        with _db_path(
            [
                {"id": "doc-a", "sentence": "Казан."},
                {"id": "doc-b", "sentence": "Проект."},
            ]
        ) as path:
            client = FakeClient(
                [
                    _response(
                        [
                            {
                                "id": "sent_000001",
                                "tatar": True,
                                "tokens": [{"text": "Казан", "label": "N"}],
                            }
                        ]
                    )
                ]
            )

            def sleep_and_request_shutdown(seconds: float) -> None:
                shutdown["requested"] = True
                clock.sleep(seconds)

            summary = run_annotation(
                db_path=path,
                config=_config(target=2, batch_size=1, requests_per_minute=5),
                client=client,
                sleep=sleep_and_request_shutdown,
                now=clock.now,
                shutdown_requested=lambda: shutdown["requested"],
            )
            first_status = _status(path, "sent_000001")
            second_status = _status(path, "sent_000002")

        self.assertEqual(summary.stopped_reason, "shutdown_requested")
        self.assertEqual(first_status, "annotated")
        self.assertEqual(second_status, "pending")

    def test_rate_limit_error_sleeps_and_retries_without_exhausting_key(self) -> None:
        sleeps: list[float] = []
        with _db_path([{"id": "doc-a", "sentence": "Казан."}]) as path:
            exhausted_path = Path(path).parent / "exhausted_keys.json"
            client = FakeClient(
                [
                    GeminiRateLimitError("requests per minute exceeded"),
                    _response(
                        [
                            {
                                "id": "sent_000001",
                                "tatar": True,
                                "tokens": [{"text": "Казан", "label": "N"}],
                            }
                        ]
                    ),
                ]
            )

            summary = run_annotation(
                db_path=path,
                config=_config(
                    target=1,
                    batch_size=1,
                    exhausted_keys_path=str(exhausted_path),
                ),
                client=client,
                sleep=sleeps.append,
            )

        self.assertEqual(summary.stopped_reason, "target_reached")
        self.assertEqual(sleeps, [60])
        self.assertFalse(exhausted_path.exists())
        self.assertEqual(client.calls, [("key-a", "model-a"), ("key-a", "model-a")])

    def test_invalid_single_sample_marks_unprocessable(self) -> None:
        with _db_path([{"id": "doc-a", "sentence": "Казан."}]) as path:
            summary = run_annotation(
                db_path=path,
                config=_config(target=1, batch_size=1),
                client=FakeClient(["not-json"]),
                sleep=lambda seconds: None,
            )
            rows = _states(path)

        self.assertEqual(summary.stopped_reason, "no_pending")
        self.assertEqual(rows[0]["status"], "unprocessable")
        self.assertIn("invalid JSON", rows[0]["last_error"])

    def test_empty_response_reduces_batch_instead_of_stopping(self) -> None:
        logs: list[str] = []
        with _db_path(
            [
                {"id": "doc-a", "sentence": "Казан."},
                {"id": "doc-b", "sentence": "Проект."},
            ]
        ) as path:
            client = FakeClient(
                [
                    GeminiEmptyResponseError("Gemini response did not contain text"),
                    _response(
                        [
                            {
                                "id": "sent_000001",
                                "tatar": True,
                                "tokens": [{"text": "Казан", "label": "N"}],
                            }
                        ]
                    ),
                    _response(
                        [
                            {
                                "id": "sent_000002",
                                "tatar": True,
                                "tokens": [{"text": "Проект", "label": "RL"}],
                            }
                        ]
                    ),
                ]
            )

            summary = run_annotation(
                db_path=path,
                config=_config(target=2, batch_size=2),
                client=client,
                sleep=lambda seconds: None,
                log=logs.append,
            )
            rows = _states(path)

        self.assertEqual(summary.stopped_reason, "target_reached")
        self.assertEqual([row["status"] for row in rows], ["annotated", "annotated"])
        self.assertEqual(len(client.calls), 3)
        joined_logs = "\n".join(logs)
        self.assertIn("empty Gemini response", joined_logs)
        self.assertIn("batch size reduced to 1", joined_logs)

    def test_shutdown_during_request_saves_completed_response_then_stops(self) -> None:
        shutdown = {"requested": False}
        with _db_path([{"id": "doc-a", "sentence": "Казан."}]) as path:
            def complete_after_shutdown(kwargs) -> str:
                shutdown["requested"] = True
                return _response(
                    [
                        {
                            "id": "sent_000001",
                            "tatar": True,
                            "tokens": [{"text": "Казан", "label": "N"}],
                        }
                    ]
                )

            summary = run_annotation(
                db_path=path,
                config=_config(target=2, batch_size=1),
                client=FakeClient([complete_after_shutdown]),
                sleep=lambda seconds: None,
                shutdown_requested=lambda: shutdown["requested"],
            )
            rows = _states(path)

        self.assertEqual(summary.stopped_reason, "shutdown_requested")
        self.assertEqual(rows[0]["status"], "annotated")

    def test_shutdown_timeout_marks_current_batch_pending(self) -> None:
        with _db_path([{"id": "doc-a", "sentence": "Казан."}]) as path:
            summary = run_annotation(
                db_path=path,
                config=_config(target=1, batch_size=1),
                client=FakeClient([GeminiShutdownError("Gemini request exceeded graceful shutdown timeout")]),
                sleep=lambda seconds: None,
            )
            rows = _states(path)

        self.assertEqual(summary.stopped_reason, "shutdown_requested")
        self.assertEqual(rows[0]["status"], "pending")
        self.assertIn("graceful shutdown timeout", rows[0]["last_error"])

    def test_forced_shutdown_marks_current_batch_pending(self) -> None:
        with _db_path([{"id": "doc-a", "sentence": "Казан."}]) as path:
            summary = run_annotation(
                db_path=path,
                config=_config(target=1, batch_size=1),
                client=FakeClient([GeminiShutdownError("Gemini request cancelled by forced shutdown", forced=True)]),
                sleep=lambda seconds: None,
            )
            rows = _states(path)

        self.assertEqual(summary.stopped_reason, "forced_shutdown")
        self.assertEqual(rows[0]["status"], "pending")
        self.assertIn("forced shutdown", rows[0]["last_error"])

    def test_fatal_error_is_returned_in_summary(self) -> None:
        with _db_path([{"id": "doc-a", "sentence": "Казан."}]) as path:
            summary = run_annotation(
                db_path=path,
                config=_config(target=1, batch_size=1),
                client=FakeClient([GeminiFatalError("google-genai is not installed")]),
                sleep=lambda seconds: None,
            )
            rows = _states(path)

        self.assertEqual(summary.stopped_reason, "fatal_error")
        self.assertEqual(summary.error, "fatal Gemini error: google-genai is not installed")
        self.assertIn("google-genai is not installed", rows[0]["last_error"])


def _response(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False)


def _keep_key_order(keys: list[str]) -> None:
    return None


def _config(
    *,
    target: int,
    batch_size: int,
    keys: tuple[str, ...] = ("key-a",),
    exhausted_keys_path: str = "exhausted_keys.json",
    requests_per_minute: int = 1_000_000,
    graceful_shutdown_timeout_seconds: int = 300,
) -> PreannotationConfig:
    return PreannotationConfig(
        model="model-a",
        api_keys=keys,
        exhausted_keys_path=exhausted_keys_path,
        requests_per_minute=requests_per_minute,
        graceful_shutdown_timeout_seconds=graceful_shutdown_timeout_seconds,
        initial_batch_size=batch_size,
        request_timeout_seconds=5,
        overload_sleep_seconds=7,
        target_annotated_count=target,
    )


class FakeClock:
    def __init__(self) -> None:
        self.time = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.time

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.time += seconds


class _db_path:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self.path = ""

    def __enter__(self) -> str:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = f"{self._tmpdir.name}/zamanalif.sqlite"
        _write_selected_sqlite(self.path, self._rows, force=False)
        return self.path

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self._tmpdir is not None
        self._tmpdir.cleanup()


def _states(path: str) -> list[sqlite3.Row]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT sample_id, status, tatar, tokens_json, last_error FROM preannotation_state ORDER BY sample_id"
        ).fetchall()


def _status(path: str, sample_id: str) -> str:
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT status FROM preannotation_state WHERE sample_id=?",
            (sample_id,),
        ).fetchone()
    return str(row[0])


if __name__ == "__main__":
    unittest.main()
