from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tatar_preannotator.manual_preannotate import tokenize_sentence
from tatar_preannotator.manual_preannotate_web import (
    ManualWebReviewService,
    _make_server,
)


class ManualPreannotateWebTests(unittest.TestCase):
    def test_tokenize_sentence_uses_exact_cyrillic_word_runs(self) -> None:
        text = "«Яңа тормыш»та 1909 елда К.Насыйриның фикере әйтелә."

        self.assertEqual(
            tokenize_sentence(text),
            ["Яңа", "тормыш", "та", "елда", "К", "Насыйриның", "фикере", "әйтелә"],
        )

    def test_service_loads_item_with_suggested_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.sqlite"
            _create_db(db_path, [("sent_1", "Мин проект турында әйттем һәм күрә.")])
            service = ManualWebReviewService(db_path)
            try:
                item = service.item(0)
            finally:
                service.close()

        self.assertEqual(item["sample"]["id"], "sent_1")
        self.assertEqual(
            [token["text"] for token in item["tokens"]],
            ["Мин", "проект", "турында", "әйттем", "һәм", "күрә"],
        )
        self.assertTrue(item["sample"]["suggested_tatar"])

    def test_service_saves_non_tatar_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.sqlite"
            _create_db(db_path, [("sent_1", "Это русское предложение.")])
            service = ManualWebReviewService(db_path)
            try:
                result = service.save({"id": "sent_1", "tatar": False, "tokens": []})
            finally:
                service.close()
            row = _state_row(db_path, "sent_1")

        self.assertTrue(result["ok"])
        self.assertEqual(row["status"], "annotated")
        self.assertEqual(row["tatar"], 0)
        self.assertEqual(json.loads(row["tokens_json"]), [])
        self.assertEqual(row["annotated_by_model"], "manual-web")

    def test_service_rejects_invalid_homonym(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.sqlite"
            _create_db(db_path, [("sent_1", "Мин проект турында әйттем.")])
            service = ManualWebReviewService(db_path)
            try:
                with self.assertRaisesRegex(Exception, "homonym"):
                    service.save(
                        {
                            "id": "sent_1",
                            "tatar": True,
                            "tokens": [{"text": "Мин", "label": "N", "homonym": True}],
                        }
                    )
            finally:
                service.close()

    def test_http_api_loads_and_saves(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.sqlite"
            _create_db(db_path, [("sent_1", "Мин проект турында әйттем.")])
            service = ManualWebReviewService(db_path)
            server = _make_server(service, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                base = f"http://{host}:{port}"
                item = _get_json(f"{base}/api/item?index=0")
                self.assertEqual(item["sample"]["id"], "sent_1")

                payload = {
                    "id": "sent_1",
                    "tatar": True,
                    "tokens": [
                        {"text": "Мин", "label": "N"},
                        {"text": "проект", "label": "RL"},
                        {"text": "турында", "label": "N"},
                        {"text": "әйттем", "label": "N"},
                    ],
                }
                result = _post_json(f"{base}/api/save", payload)
                self.assertTrue(result["ok"])
            finally:
                server.shutdown()
                server.server_close()
                service.close()
                thread.join(timeout=2)
            row = _state_row(db_path, "sent_1")

        self.assertEqual(row["status"], "annotated")
        self.assertEqual(row["annotated_by_model"], "manual-web")

    def test_http_api_returns_bad_request_for_invalid_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.sqlite"
            _create_db(db_path, [("sent_1", "Мин проект турында әйттем.")])
            service = ManualWebReviewService(db_path)
            server = _make_server(service, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                with self.assertRaises(HTTPError) as ctx:
                    _post_json(f"http://{host}:{port}/api/save", {"id": "sent_1", "tatar": "yes"})
                self.assertEqual(ctx.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                service.close()
                thread.join(timeout=2)


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _create_db(db_path: Path, samples: list[tuple[str, str]]) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table samples (
              id text primary key,
              source_id text,
              text text not null
            )
            """
        )
        conn.execute(
            """
            create table preannotation_state (
              sample_id text primary key references samples(id),
              status text not null,
              tatar integer,
              tokens_json text,
              attempts integer not null default 0,
              last_error text,
              updated_at text not null,
              annotated_by_model text
            )
            """
        )
        for sample_id, text in samples:
            conn.execute(
                "insert into samples(id, source_id, text) values (?, null, ?)",
                (sample_id, text),
            )
            conn.execute(
                """
                insert into preannotation_state(
                  sample_id, status, tatar, tokens_json, attempts, last_error, updated_at
                ) values (?, 'unprocessable', null, null, 1, 'test error', 'now')
                """,
                (sample_id,),
            )


def _state_row(db_path: Path, sample_id: str) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "select * from preannotation_state where sample_id = ?",
            (sample_id,),
        ).fetchone()
    finally:
        conn.close()


if __name__ == "__main__":
    unittest.main()
