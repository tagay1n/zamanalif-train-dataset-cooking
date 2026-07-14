from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import db
from .word_export import normalize_word


DECISIONS = frozenset({"N", "RL", "U", "contextual_homonym"})


class ConflictResolverError(ValueError):
    """Raised when word-conflict resolution cannot proceed."""


@dataclass(frozen=True)
class WordResolution:
    normalized_word: str
    decision: str


@dataclass
class ConflictCandidate:
    normalized_word: str
    frequency: int
    label_counts: Counter[str]
    homonym_counts: Counter[bool]
    examples: dict[str, list[dict[str, str]]]
    resolved_decision: str | None = None


def ensure_word_resolution_schema(conn: sqlite3.Connection) -> None:
    """Create the auxiliary table for word-level conflict decisions."""
    conn.execute(
        """
        create table if not exists word_resolutions (
            normalized_word text primary key,
            decision text not null check(decision in ('N', 'RL', 'U', 'contextual_homonym')),
            updated_at text not null
        )
        """
    )


def load_word_resolutions(db_path: str | Path) -> dict[str, WordResolution]:
    """Load all saved word-level conflict decisions."""
    database = Path(db_path)
    if not database.exists():
        return {}
    with closing(sqlite3.connect(database)) as conn:
        ensure_word_resolution_schema(conn)
        rows = conn.execute(
            "select normalized_word, decision from word_resolutions"
        ).fetchall()
    return {
        str(row[0]): WordResolution(normalized_word=str(row[0]), decision=str(row[1]))
        for row in rows
    }


def save_word_resolution(
    conn: sqlite3.Connection,
    normalized_word: str,
    decision: str,
) -> None:
    """Persist one word-level conflict decision."""
    if decision not in DECISIONS:
        raise ConflictResolverError(
            f"decision must be one of: {', '.join(sorted(DECISIONS))}"
        )
    normalized = normalize_word(normalized_word)
    if not normalized:
        raise ConflictResolverError("normalized_word must contain a Cyrillic word")
    ensure_word_resolution_schema(conn)
    conn.execute(
        """
        insert into word_resolutions(normalized_word, decision, updated_at)
        values (?, ?, ?)
        on conflict(normalized_word) do update set
            decision=excluded.decision,
            updated_at=excluded.updated_at
        """,
        (normalized, decision, _now()),
    )
    conn.commit()


def conflict_candidates_from_db(db_path: str | Path) -> list[ConflictCandidate]:
    """Build unresolved word-conflict candidates from annotated sentence tokens."""
    database = Path(db_path)
    if not database.exists():
        raise ConflictResolverError(f"database file does not exist: {database}")
    with closing(db.connect(database)) as conn:
        db.ensure_preannotation_schema(conn)
        ensure_word_resolution_schema(conn)
        resolutions = {
            str(row["normalized_word"]): str(row["decision"])
            for row in conn.execute(
                "select normalized_word, decision from word_resolutions"
            ).fetchall()
        }
        rows = conn.execute(
            """
            select s.id, s.text, p.tokens_json
            from preannotation_state p
            join samples s on s.id = p.sample_id
            where p.status = 'annotated'
              and p.tatar = 1
              and p.tokens_json is not null
            order by s.id
            """
        ).fetchall()
    return build_conflict_candidates(rows, resolutions=resolutions)


def build_conflict_candidates(
    rows: list[sqlite3.Row] | list[dict[str, Any]],
    *,
    resolutions: dict[str, str] | None = None,
) -> list[ConflictCandidate]:
    """Detect words with mixed labels or mixed homonym flags."""
    resolutions = resolutions or {}
    labels: dict[str, Counter[str]] = defaultdict(Counter)
    homonyms: dict[str, Counter[bool]] = defaultdict(Counter)
    examples: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for row in rows:
        sample_id = str(row["id"])
        sentence = str(row["text"])
        try:
            tokens = json.loads(str(row["tokens_json"]))
        except json.JSONDecodeError:
            continue
        if not isinstance(tokens, list):
            continue
        for token in tokens:
            if not isinstance(token, dict):
                continue
            text = token.get("text")
            label = token.get("label")
            if not isinstance(text, str) or label not in {"N", "RL", "U"}:
                continue
            normalized = normalize_word(text)
            if not normalized:
                continue
            homonym = token.get("homonym") is True
            labels[normalized][str(label)] += 1
            homonyms[normalized][homonym] += 1
            key = f"{label}:{'homonym' if homonym else 'plain'}"
            if len(examples[normalized][key]) < 5:
                examples[normalized][key].append(
                    {"id": sample_id, "text": sentence, "token": text}
                )

    candidates: list[ConflictCandidate] = []
    for normalized, label_counts in labels.items():
        active_labels = sum(1 for count in label_counts.values() if count)
        homonym_counts = homonyms[normalized]
        has_homonym_conflict = bool(homonym_counts[True] and homonym_counts[False])
        if active_labels <= 1 and not has_homonym_conflict:
            continue
        candidates.append(
            ConflictCandidate(
                normalized_word=normalized,
                frequency=sum(label_counts.values()),
                label_counts=label_counts,
                homonym_counts=homonym_counts,
                examples=dict(examples[normalized]),
                resolved_decision=resolutions.get(normalized),
            )
        )
    candidates.sort(key=lambda item: (-item.frequency, item.normalized_word))
    return candidates


class ConflictReviewService:
    """SQLite-backed service for the browser conflict resolver."""

    def __init__(self, db_path: str | Path, *, limit: int | None = None) -> None:
        self.db_path = str(db_path)
        self.conn = db.connect(db_path)
        db.ensure_preannotation_schema(self.conn)
        ensure_word_resolution_schema(self.conn)
        self.candidates = conflict_candidates_from_db(db_path)
        if limit is not None:
            self.candidates = self.candidates[:limit]

    @property
    def total(self) -> int:
        return len(self.candidates)

    def close(self) -> None:
        self.conn.close()

    def item(self, index: int) -> dict[str, Any]:
        candidate = self._candidate_at(index)
        return {
            "index": index,
            "total": self.total,
            "candidate": _candidate_json(candidate),
        }

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        word = payload.get("word")
        decision = payload.get("decision")
        if not isinstance(word, str) or not word:
            raise ConflictResolverError("word must be a non-empty string")
        if not isinstance(decision, str):
            raise ConflictResolverError("decision must be a string")
        candidate, index = self._candidate_by_word(word)
        save_word_resolution(self.conn, candidate.normalized_word, decision)
        candidate.resolved_decision = decision
        return {"ok": True, "next_index": self.next_unresolved_index(index)}

    def next_unresolved_index(self, current_index: int) -> int | None:
        for index in range(current_index + 1, self.total):
            if self.candidates[index].resolved_decision is None:
                return index
        for index in range(0, current_index + 1):
            if self.candidates[index].resolved_decision is None:
                return index
        return None

    def _candidate_at(self, index: int) -> ConflictCandidate:
        if index < 0 or index >= self.total:
            raise ConflictResolverError(f"candidate index out of range: {index}")
        return self.candidates[index]

    def _candidate_by_word(self, word: str) -> tuple[ConflictCandidate, int]:
        normalized = normalize_word(word)
        for index, candidate in enumerate(self.candidates):
            if candidate.normalized_word == normalized:
                return candidate, index
        raise ConflictResolverError(f"unknown conflict word: {word}")


def serve_conflict_resolver_web(
    db_path: str | Path,
    *,
    host: str,
    port: int,
    limit: int | None = None,
    log=print,
) -> None:
    """Run the local conflict resolver UI until interrupted."""
    service = ConflictReviewService(db_path, limit=limit)
    server = _make_server(service, host=host, port=port)
    try:
        actual_host, actual_port = server.server_address[:2]
        log(f"word conflict resolver web UI: http://{actual_host}:{actual_port}")
        server.serve_forever()
    finally:
        server.server_close()
        service.close()


def _make_server(service: ConflictReviewService, *, host: str, port: int) -> HTTPServer:
    class Handler(ConflictResolverWebHandler):
        review_service = service

    return HTTPServer((host, port), Handler)


class ConflictResolverWebHandler(BaseHTTPRequestHandler):
    review_service: ConflictReviewService

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(HTTPStatus.OK, HTML_PAGE, content_type="text/html; charset=utf-8")
            return
        if parsed.path == "/api/item":
            query = parse_qs(parsed.query)
            index = int(query.get("index", ["0"])[0])
            self._send_json(HTTPStatus.OK, self.review_service.item(index))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/save":
                self._send_json(HTTPStatus.OK, self.review_service.save(payload))
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except (ConflictResolverError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except sqlite3.Error as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        parsed = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("request body must be a JSON object")
        return parsed

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json; charset=utf-8",
        )

    def _send(self, status: HTTPStatus, body: str | bytes, *, content_type: str) -> None:
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _candidate_json(candidate: ConflictCandidate) -> dict[str, Any]:
    return {
        "word": candidate.normalized_word,
        "frequency": candidate.frequency,
        "label_counts": dict(candidate.label_counts),
        "homonym_counts": {
            "true": candidate.homonym_counts[True],
            "false": candidate.homonym_counts[False],
        },
        "examples": candidate.examples,
        "resolved_decision": candidate.resolved_decision,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


HTML_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Zamanalif word conflict resolver</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; }
    body { margin: 0; background: #f7f7f4; color: #20201d; }
    main { max-width: 1180px; margin: 0 auto; padding: 20px; }
    header { display: flex; justify-content: space-between; gap: 16px; align-items: baseline; margin-bottom: 16px; }
    h1 { margin: 0; font-size: 22px; }
    .panel { background: #fff; border: 1px solid #d8d8d0; border-radius: 8px; padding: 16px; margin-bottom: 14px; }
    .word { font-size: 34px; font-weight: 800; margin-bottom: 6px; }
    .meta { color: #666; font-size: 14px; }
    .counts { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }
    .pill { display: inline-block; padding: 4px 9px; border-radius: 999px; font-size: 13px; font-weight: 700; background: #eef2ff; color: #312e81; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .toolbar label { padding: 7px 10px; border: 1px solid #d8d8d0; border-radius: 6px; background: #fff; cursor: pointer; }
    button { border: 1px solid #aaa; background: #fff; padding: 8px 12px; border-radius: 6px; cursor: pointer; }
    button.primary { background: #1f6feb; border-color: #1f6feb; color: #fff; }
    .status { min-height: 22px; color: #166534; }
    .status.error { color: #b91c1c; }
    .example-group { margin-top: 16px; }
    .example-group h3 { font-size: 15px; margin: 0 0 8px; color: #333; }
    .example { border-top: 1px solid #eee; padding: 8px 0; }
    .sample-id { color: #312e81; font-weight: 700; font-size: 13px; }
    .sentence { white-space: pre-wrap; line-height: 1.4; }
  </style>
</head>
<body>
<main>
  <header>
    <h1>Word conflict resolver</h1>
    <div class="meta" id="progress"></div>
  </header>
  <section class="panel">
    <div class="word" id="word"></div>
    <div class="meta" id="resolved"></div>
    <div class="counts" id="counts"></div>
  </section>
  <section class="panel toolbar">
    <label><input type="radio" name="decision" value="N"> N</label>
    <label><input type="radio" name="decision" value="RL"> RL</label>
    <label><input type="radio" name="decision" value="U"> U</label>
    <label><input type="radio" name="decision" value="contextual_homonym"> contextual_homonym</label>
    <button class="primary" id="save">Save & Next</button>
    <button id="prev">Previous</button>
    <button id="next">Next</button>
    <span class="status" id="status"></span>
  </section>
  <p class="meta">Keyboard: N=native, R=RL, U=uncertain, H=contextual homonym, ←/→ navigate, Space saves.</p>
  <section class="panel" id="examples"></section>
</main>
<script>
let currentIndex = 0;
let currentTotal = 0;
let current = null;

function el(id) { return document.getElementById(id); }

async function loadItem(index) {
  const response = await fetch(`/api/item?index=${index}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "failed to load");
  currentIndex = data.index;
  currentTotal = data.total;
  current = data.candidate;
  render();
}

function render() {
  el("progress").textContent = `${currentIndex + 1}/${currentTotal}`;
  el("word").textContent = current.word;
  el("resolved").textContent = current.resolved_decision ? `Resolved: ${current.resolved_decision}` : "Unresolved";
  el("counts").innerHTML = [
    `freq ${current.frequency}`,
    `N ${current.label_counts.N || 0}`,
    `RL ${current.label_counts.RL || 0}`,
    `U ${current.label_counts.U || 0}`,
    `homonym ${current.homonym_counts.true || 0}`,
    `plain ${current.homonym_counts.false || 0}`,
  ].map(value => `<span class="pill">${escapeHtml(value)}</span>`).join("");
  const decision = current.resolved_decision || majorityDecision();
  document.querySelectorAll("input[name=decision]").forEach(input => {
    input.checked = input.value === decision;
  });
  renderExamples();
  setStatus("");
}

function majorityDecision() {
  const counts = current.label_counts || {};
  const labels = ["N", "RL", "U"];
  labels.sort((a, b) => (counts[b] || 0) - (counts[a] || 0));
  return labels[0] || "U";
}

function renderExamples() {
  el("examples").innerHTML = Object.entries(current.examples).map(([group, examples]) => `
    <div class="example-group">
      <h3>${escapeHtml(group)}</h3>
      ${examples.map(example => `
        <div class="example">
          <div class="sample-id">${escapeHtml(example.id)} · token ${escapeHtml(example.token)}</div>
          <div class="sentence">${escapeHtml(example.text)}</div>
        </div>
      `).join("")}
    </div>
  `).join("");
}

async function save() {
  const selected = document.querySelector("input[name=decision]:checked");
  if (!selected) throw new Error("choose a decision");
  const result = await post("/api/save", {word: current.word, decision: selected.value});
  if (result.next_index === null) {
    setStatus("No unresolved conflicts.");
    current.resolved_decision = selected.value;
    render();
    return;
  }
  await loadItem(result.next_index);
}

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "request failed");
  return data;
}

function setStatus(message, isError=false) {
  el("status").textContent = message;
  el("status").className = isError ? "status error" : "status";
}

function setDecision(decision) {
  const input = document.querySelector(`input[name=decision][value="${decision}"]`);
  if (input) input.checked = true;
}

function previousItem() {
  return loadItem(Math.max(0, currentIndex - 1));
}

function nextItem() {
  return loadItem(Math.min(currentTotal - 1, currentIndex + 1));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}

el("save").addEventListener("click", () => save().catch(error => setStatus(error.message, true)));
el("prev").addEventListener("click", () => previousItem().catch(error => setStatus(error.message, true)));
el("next").addEventListener("click", () => nextItem().catch(error => setStatus(error.message, true)));
document.addEventListener("keydown", event => {
  if (!current) return;
  const key = event.key.toLowerCase();
  if (key === "n" || event.key === "1") setDecision("N");
  if (key === "r" || event.key === "2") setDecision("RL");
  if (key === "u" || event.key === "3") setDecision("U");
  if (key === "h" || event.key === "4") setDecision("contextual_homonym");
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    previousItem().catch(error => setStatus(error.message, true));
  }
  if (event.key === "ArrowRight") {
    event.preventDefault();
    nextItem().catch(error => setStatus(error.message, true));
  }
  if (event.key === " " || event.key === "Enter") {
    event.preventDefault();
    save().catch(error => setStatus(error.message, true));
  }
});
loadItem(0).catch(error => setStatus(error.message, true));
</script>
</body>
</html>
"""
