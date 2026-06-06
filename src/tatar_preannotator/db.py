from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .schema import Sample


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the annotation database with foreign keys enabled."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def reset_processing(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE preannotation_state SET status='pending', updated_at=? WHERE status='processing'",
        (_now(),),
    )
    conn.commit()


def annotated_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM preannotation_state WHERE status='annotated'"
    ).fetchone()
    return int(row["count"])


def pending_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM preannotation_state WHERE status='pending'"
    ).fetchone()
    return int(row["count"])


def next_pending(conn: sqlite3.Connection, limit: int) -> list[Sample]:
    rows = conn.execute(
        """
        SELECT s.id, s.text
        FROM samples s
        JOIN preannotation_state p ON p.sample_id = s.id
        WHERE p.status = 'pending'
        ORDER BY s.id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [Sample(id=str(row["id"]), text=str(row["text"])) for row in rows]


def mark_processing(conn: sqlite3.Connection, samples: Iterable[Sample]) -> None:
    now = _now()
    conn.executemany(
        """
        UPDATE preannotation_state
        SET status='processing', attempts=attempts+1, updated_at=?
        WHERE sample_id=?
        """,
        [(now, sample.id) for sample in samples],
    )
    conn.commit()


def mark_pending(conn: sqlite3.Connection, samples: Iterable[Sample], error: str | None = None) -> None:
    now = _now()
    conn.executemany(
        """
        UPDATE preannotation_state
        SET status='pending', last_error=?, updated_at=?
        WHERE sample_id=?
        """,
        [(error, now, sample.id) for sample in samples],
    )
    conn.commit()


def save_annotations(conn: sqlite3.Connection, items: Iterable[dict]) -> None:
    now = _now()
    conn.executemany(
        """
        UPDATE preannotation_state
        SET status='annotated', tatar=?, tokens_json=?, last_error=NULL, updated_at=?
        WHERE sample_id=?
        """,
        [
            (
                1 if item["tatar"] else 0,
                json.dumps(item["tokens"], ensure_ascii=False, sort_keys=True),
                now,
                item["id"],
            )
            for item in items
        ],
    )
    conn.commit()


def mark_unprocessable(conn: sqlite3.Connection, sample: Sample, error: str) -> None:
    conn.execute(
        """
        UPDATE preannotation_state
        SET status='unprocessable', last_error=?, updated_at=?
        WHERE sample_id=?
        """,
        (error, _now(), sample.id),
    )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

