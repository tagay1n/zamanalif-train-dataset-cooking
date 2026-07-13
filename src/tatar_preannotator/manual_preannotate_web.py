from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import parse_qs, urlparse

from zamanalif_selector.features import count_tatar_specific_letters

from . import db
from .manual_preannotate import (
    MANUAL_WEB_MODEL_NAME,
    ManualPreannotateError,
    _count_unprocessable,
    _load_prior_token_stats,
    _load_unprocessable,
    build_editable_tokens,
    save_manual_item,
)
from .schema import Sample
from .word_export import load_reviewed_words


@dataclass
class WebSample:
    id: str
    text: str
    last_error: str | None
    skipped: bool = False
    saved: bool = False


class ManualWebReviewService:
    """SQLite-backed manual review service used by the local browser UI."""

    def __init__(self, db_path: str | Path, *, limit: int | None = None) -> None:
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        db.ensure_preannotation_schema(self.conn)
        self.reviewed = load_reviewed_words(db_path)
        self.prior_labels, self.prior_homonyms = _load_prior_token_stats(self.conn)
        self.samples = [
            WebSample(id=str(row["id"]), text=str(row["text"]), last_error=row["last_error"])
            for row in _load_unprocessable(self.conn, limit=limit)
        ]

    @property
    def total(self) -> int:
        return len(self.samples)

    def close(self) -> None:
        self.conn.close()

    def item(self, index: int) -> dict[str, Any]:
        sample = self._sample_at(index)
        tokens = build_editable_tokens(
            sample.text,
            reviewed=self.reviewed,
            prior_labels=self.prior_labels,
            prior_homonyms=self.prior_homonyms,
        )
        return {
            "index": index,
            "total": self.total,
            "remaining": _count_unprocessable(self.conn),
            "saved": sample.saved,
            "skipped": sample.skipped,
            "sample": {
                "id": sample.id,
                "text": sample.text,
                "last_error": sample.last_error,
                "suggested_tatar": count_tatar_specific_letters(sample.text) >= 2,
            },
            "tokens": [token.to_json() for token in tokens],
        }

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        sample, index = self._sample_by_id(str(payload.get("id", "")))
        tatar = payload.get("tatar")
        if not isinstance(tatar, bool):
            raise ManualPreannotateError("tatar must be boolean")
        tokens = payload.get("tokens", [])
        if not isinstance(tokens, list):
            raise ManualPreannotateError("tokens must be a list")
        item = {
            "id": sample.id,
            "tatar": tatar,
            "tokens": tokens if tatar else [],
        }
        save_manual_item(
            self.conn,
            Sample(id=sample.id, text=sample.text),
            item,
            model=MANUAL_WEB_MODEL_NAME,
        )
        sample.saved = True
        sample.skipped = False
        return {
            "ok": True,
            "next_index": self.next_review_index(index),
            "remaining": _count_unprocessable(self.conn),
        }

    def skip(self, payload: dict[str, Any]) -> dict[str, Any]:
        sample, index = self._sample_by_id(str(payload.get("id", "")))
        sample.skipped = True
        return {
            "ok": True,
            "next_index": self.next_review_index(index),
            "remaining": _count_unprocessable(self.conn),
        }

    def next_review_index(self, current_index: int) -> int | None:
        for index in range(current_index + 1, self.total):
            if not self.samples[index].saved and not self.samples[index].skipped:
                return index
        for index in range(0, current_index + 1):
            if not self.samples[index].saved and not self.samples[index].skipped:
                return index
        return None

    def _sample_at(self, index: int) -> WebSample:
        if index < 0 or index >= self.total:
            raise ManualPreannotateError(f"sample index out of range: {index}")
        return self.samples[index]

    def _sample_by_id(self, sample_id: str) -> tuple[WebSample, int]:
        for index, sample in enumerate(self.samples):
            if sample.id == sample_id:
                return sample, index
        raise ManualPreannotateError(f"unknown sample id: {sample_id}")


def serve_manual_preannotation_web(
    db_path: str | Path,
    *,
    host: str,
    port: int,
    limit: int | None = None,
    log=print,
) -> None:
    """Run the local browser UI until interrupted."""
    service = ManualWebReviewService(db_path, limit=limit)
    server = _make_server(service, host=host, port=port)
    try:
        actual_host, actual_port = server.server_address[:2]
        log(f"manual preannotation web UI: http://{actual_host}:{actual_port}")
        server.serve_forever()
    finally:
        server.server_close()
        service.close()


def _make_server(
    service: ManualWebReviewService,
    *,
    host: str,
    port: int,
) -> HTTPServer:
    class Handler(ManualPreannotateWebHandler):
        review_service = service

    return HTTPServer((host, port), Handler)


class ManualPreannotateWebHandler(BaseHTTPRequestHandler):
    review_service: ManualWebReviewService

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
            if parsed.path == "/api/skip":
                self._send_json(HTTPStatus.OK, self.review_service.skip(payload))
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except (ManualPreannotateError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except sqlite3.Error as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8")
        parsed = json.loads(data or "{}")
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


HTML_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Zamanalif manual preannotation</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; }
    body { margin: 0; background: #f7f7f4; color: #20201d; }
    main { max-width: 1180px; margin: 0 auto; padding: 20px; }
    header { display: flex; justify-content: space-between; gap: 16px; align-items: baseline; margin-bottom: 16px; }
    h1 { margin: 0; font-size: 22px; }
    .panel { background: #fff; border: 1px solid #d8d8d0; border-radius: 8px; padding: 16px; margin-bottom: 14px; }
    .meta { color: #666; font-size: 14px; }
    .sentence { font-size: 20px; line-height: 1.45; white-space: pre-wrap; }
    .error { margin-top: 12px; color: #9a3412; font-size: 13px; border-top: 1px solid #eee; padding-top: 10px; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    button { border: 1px solid #aaa; background: #fff; padding: 8px 12px; border-radius: 6px; cursor: pointer; }
    button.primary { background: #1f6feb; border-color: #1f6feb; color: #fff; }
    button.warn { background: #fff7ed; border-color: #fdba74; }
    button:disabled { opacity: .45; cursor: not-allowed; }
    table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8d8d0; border-radius: 8px; overflow: hidden; }
    th, td { border-bottom: 1px solid #eee; padding: 8px; text-align: left; }
    th { background: #f0f3f6; font-size: 13px; color: #444; }
    tr.selected { outline: 2px solid #1f6feb; outline-offset: -2px; background: #eff6ff; }
    .label-cell { white-space: nowrap; }
    .label-cell label { margin-right: 10px; }
    .pill { display: inline-block; padding: 2px 7px; border-radius: 999px; font-size: 12px; font-weight: 700; }
    .n { background: #dcfce7; color: #166534; }
    .rl { background: #fef3c7; color: #92400e; }
    .u { background: #fee2e2; color: #991b1b; }
    .status { min-height: 22px; color: #166534; }
    .status.error { color: #b91c1c; }
    .shortcuts { font-size: 13px; color: #555; }
  </style>
</head>
<body>
<main>
  <header>
    <h1>Manual preannotation</h1>
    <div class="meta" id="progress"></div>
  </header>

  <section class="panel">
    <div class="meta" id="sample-id"></div>
    <div class="sentence" id="sentence"></div>
    <div class="error" id="last-error" hidden></div>
  </section>

  <section class="panel toolbar">
    <label><input type="radio" name="tatar" value="true"> Tatar</label>
    <label><input type="radio" name="tatar" value="false"> Non-Tatar</label>
    <button class="primary" id="save">Save & Next</button>
    <button class="warn" id="skip">Skip</button>
    <button id="prev">Previous</button>
    <button id="next">Next</button>
    <span class="status" id="status"></span>
  </section>

  <table>
    <thead><tr><th>#</th><th>Token</th><th>Label</th><th>Homonym</th></tr></thead>
    <tbody id="tokens"></tbody>
  </table>
  <p class="shortcuts">Keyboard: ↑/↓ select token, ←/→ change label, 1=N, 2=RL, 3=U, Space toggles homonym, Enter saves.</p>
</main>
<script>
let currentIndex = 0;
let current = null;
let selectedToken = 0;
const labels = ["N", "RL", "U"];

function el(id) { return document.getElementById(id); }

async function loadItem(index) {
  const response = await fetch(`/api/item?index=${index}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "failed to load");
  currentIndex = data.index;
  current = data;
  selectedToken = 0;
  render();
}

function render() {
  el("progress").textContent = `${current.index + 1}/${current.total} · remaining ${current.remaining}`;
  el("sample-id").textContent = current.sample.id;
  el("sentence").textContent = current.sample.text;
  el("last-error").hidden = !current.sample.last_error;
  el("last-error").textContent = current.sample.last_error || "";
  document.querySelector(`input[name=tatar][value="${current.sample.suggested_tatar}"]`).checked = true;
  renderTokens();
  setStatus("");
}

function renderTokens() {
  const tbody = el("tokens");
  tbody.innerHTML = "";
  current.tokens.forEach((token, index) => {
    const row = document.createElement("tr");
    row.className = index === selectedToken ? "selected" : "";
    row.addEventListener("click", () => { selectedToken = index; renderTokens(); });
    row.innerHTML = `
      <td>${index + 1}</td>
      <td><strong>${escapeHtml(token.text)}</strong> <span class="pill ${token.label.toLowerCase()}">${token.label}</span></td>
      <td class="label-cell">${labels.map(label => `
        <label><input type="radio" name="label-${index}" value="${label}" ${token.label === label ? "checked" : ""}> ${label}</label>
      `).join("")}</td>
      <td><input type="checkbox" class="homonym" ${token.homonym ? "checked" : ""} ${token.label === "RL" ? "" : "disabled"}></td>
    `;
    row.querySelectorAll(`input[name="label-${index}"]`).forEach(input => {
      input.addEventListener("change", () => {
        token.label = input.value;
        if (token.label !== "RL") token.homonym = false;
        renderTokens();
      });
    });
    row.querySelector(".homonym").addEventListener("change", event => {
      token.homonym = event.target.checked;
    });
    tbody.appendChild(row);
  });
}

async function save() {
  const tatar = document.querySelector("input[name=tatar]:checked").value === "true";
  const tokens = tatar ? current.tokens.map(token => {
    const out = {text: token.text, label: token.label};
    if (token.homonym) out.homonym = true;
    return out;
  }) : [];
  const result = await post("/api/save", {id: current.sample.id, tatar, tokens});
  if (result.next_index === null) {
    setStatus("No remaining rows.");
    return;
  }
  await loadItem(result.next_index);
}

async function skip() {
  const result = await post("/api/skip", {id: current.sample.id});
  if (result.next_index === null) {
    setStatus("No remaining rows.");
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

function moveToken(delta) {
  selectedToken = Math.max(0, Math.min(current.tokens.length - 1, selectedToken + delta));
  renderTokens();
}

function setSelectedLabel(label) {
  if (!current || !current.tokens.length) return;
  current.tokens[selectedToken].label = label;
  if (label !== "RL") current.tokens[selectedToken].homonym = false;
  renderTokens();
}

function setStatus(message, isError=false) {
  el("status").textContent = message;
  el("status").className = isError ? "status error" : "status";
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}

document.addEventListener("keydown", event => {
  if (!current) return;
  if (event.key === "ArrowDown") { event.preventDefault(); moveToken(1); }
  if (event.key === "ArrowUp") { event.preventDefault(); moveToken(-1); }
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    const idx = labels.indexOf(current.tokens[selectedToken].label);
    setSelectedLabel(labels[Math.max(0, idx - 1)]);
  }
  if (event.key === "ArrowRight") {
    event.preventDefault();
    const idx = labels.indexOf(current.tokens[selectedToken].label);
    setSelectedLabel(labels[Math.min(labels.length - 1, idx + 1)]);
  }
  if (event.key === "1") setSelectedLabel("N");
  if (event.key === "2") setSelectedLabel("RL");
  if (event.key === "3") setSelectedLabel("U");
  if (event.key === " " && current.tokens[selectedToken]?.label === "RL") {
    event.preventDefault();
    current.tokens[selectedToken].homonym = !current.tokens[selectedToken].homonym;
    renderTokens();
  }
  if (event.key === "Enter") {
    event.preventDefault();
    save().catch(error => setStatus(error.message, true));
  }
});

el("save").addEventListener("click", () => save().catch(error => setStatus(error.message, true)));
el("skip").addEventListener("click", () => skip().catch(error => setStatus(error.message, true)));
el("prev").addEventListener("click", () => loadItem(Math.max(0, currentIndex - 1)).catch(error => setStatus(error.message, true)));
el("next").addEventListener("click", () => loadItem(Math.min(current.total - 1, currentIndex + 1)).catch(error => setStatus(error.message, true)));
loadItem(0).catch(error => setStatus(error.message, true));
</script>
</body>
</html>
"""
