from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import re
import sqlite3

from zamanalif_selector.features import TATAR_SPECIFIC_LETTERS

from . import db
from .schema import Sample
from .validate import validate_response
from .word_export import conversion_branches, normalize_word


CYRILLIC_WORD_RE = re.compile(r"[А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]+")
LABELS = {"N", "RL", "U"}
MANUAL_WEB_MODEL_NAME = "manual-web"


@dataclass
class EditableToken:
    text: str
    label: str
    homonym: bool = False

    def to_json(self) -> dict[str, object]:
        item: dict[str, object] = {"text": self.text, "label": self.label}
        if self.homonym:
            item["homonym"] = True
        return item


class ManualPreannotateError(ValueError):
    """Raised when a manual pre-annotation command cannot be applied."""


def tokenize_sentence(text: str) -> list[str]:
    """Extract exact Cyrillic word tokens from a sentence."""
    return [match.group(0) for match in CYRILLIC_WORD_RE.finditer(text)]


def build_editable_tokens(
    text: str,
    *,
    reviewed: dict[str, object],
    prior_labels: dict[str, Counter[str]],
    prior_homonyms: Counter[str],
) -> list[EditableToken]:
    """Tokenize text and attach suggested labels and homonym flags."""
    tokens: list[EditableToken] = []
    for token in tokenize_sentence(text):
        label = _suggest_label(token, reviewed, prior_labels)
        tokens.append(
            EditableToken(
                text=token,
                label=label,
                homonym=_suggest_homonym(token, prior_homonyms),
            )
        )
    return tokens


def _suggest_label(
    token: str,
    reviewed: dict[str, object],
    prior_labels: dict[str, Counter[str]],
) -> str:
    normalized = normalize_word(token)
    if not normalized:
        return "U"
    reviewed_item = reviewed.get(normalized)
    if reviewed_item is not None and reviewed_item.origin in LABELS:
        return reviewed_item.origin
    counts = prior_labels.get(normalized)
    if counts:
        return counts.most_common(1)[0][0]
    if any(char in TATAR_SPECIFIC_LETTERS for char in normalized):
        return "N"
    branches = conversion_branches(normalized)
    if branches.state == "origin_independent":
        return "N"
    return "U"


def _suggest_homonym(token: str, prior_homonyms: Counter[str]) -> bool:
    normalized = normalize_word(token)
    return bool(normalized and prior_homonyms[normalized] >= 2)


def save_manual_item(
    conn: sqlite3.Connection,
    sample: Sample,
    item: dict[str, object],
    *,
    model: str,
) -> None:
    """Validate and save a manual sentence annotation using Gemini-compatible schema."""
    raw = json.dumps([item], ensure_ascii=False)
    validation = validate_response(raw, [sample])
    if not validation.ok:
        raise ManualPreannotateError("; ".join(validation.errors))
    db.save_annotations(conn, validation.items, model=model)


def _load_unprocessable(conn: sqlite3.Connection, *, limit: int | None) -> list[sqlite3.Row]:
    sql = """
        SELECT s.id, s.text, p.last_error
        FROM samples s
        JOIN preannotation_state p ON p.sample_id = s.id
        WHERE p.status = 'unprocessable'
        ORDER BY s.id
    """
    params: tuple[int, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return list(conn.execute(sql, params).fetchall())


def _count_unprocessable(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM preannotation_state WHERE status='unprocessable'"
    ).fetchone()
    return int(row["count"])


def _load_prior_token_stats(
    conn: sqlite3.Connection,
) -> tuple[dict[str, Counter[str]], Counter[str]]:
    labels: dict[str, Counter[str]] = defaultdict(Counter)
    homonyms: Counter[str] = Counter()
    rows = conn.execute(
        """
        SELECT p.tokens_json
        FROM preannotation_state p
        WHERE p.status = 'annotated'
          AND p.tatar = 1
          AND p.tokens_json IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        try:
            tokens = json.loads(row["tokens_json"])
        except json.JSONDecodeError:
            continue
        if not isinstance(tokens, list):
            continue
        for token in tokens:
            if not isinstance(token, dict):
                continue
            normalized = normalize_word(str(token.get("text", "")))
            label = token.get("label")
            if not normalized or label not in LABELS:
                continue
            labels[normalized][str(label)] += 1
            if token.get("homonym") is True:
                homonyms[normalized] += 1
    return dict(labels), homonyms
