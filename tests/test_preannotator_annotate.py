from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest

from tatar_preannotator.annotate import run_annotation
from tatar_preannotator.gemini_client import GeminiFatalError, GeminiOverloadedError, GeminiQuotaError
from tatar_preannotator.schema import PreannotationConfig
from zamanalif_selector.cli import _write_selected_sqlite


class FakeClient:
    def __init__(self, actions: list[object]):
        self.actions = list(actions)
        self.calls: list[tuple[str, str]] = []

    def generate(self, *, api_key: str, model: str, prompt: str, timeout_seconds: int) -> str:
        self.calls.append((api_key, model))
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return str(action)


class AnnotateTests(unittest.TestCase):
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

    def test_quota_rotates_to_next_key(self) -> None:
        with _db_path([{"id": "doc-a", "sentence": "Казан."}]) as path:
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
                config=_config(target=1, batch_size=1, keys=("key-a", "key-b")),
                client=client,
                sleep=lambda seconds: None,
            )

        self.assertEqual(summary.stopped_reason, "target_reached")
        self.assertEqual(client.calls, [("key-a", "model-a"), ("key-b", "model-a")])

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
            )

        self.assertEqual(summary.stopped_reason, "target_reached")
        self.assertEqual(sleeps, [7])
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


def _config(
    *,
    target: int,
    batch_size: int,
    keys: tuple[str, ...] = ("key-a",),
) -> PreannotationConfig:
    return PreannotationConfig(
        model="model-a",
        api_keys=keys,
        initial_batch_size=batch_size,
        request_timeout_seconds=5,
        overload_sleep_seconds=7,
        target_annotated_count=target,
    )


class _db_path:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self.path = ""

    def __enter__(self) -> str:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = f"{self._tmpdir.name}/selected.sqlite"
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


if __name__ == "__main__":
    unittest.main()
