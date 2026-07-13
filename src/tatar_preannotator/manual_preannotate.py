from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import re
import sqlite3
from typing import Callable

from zamanalif_selector.features import TATAR_SPECIFIC_LETTERS, count_tatar_specific_letters

from . import db
from .schema import Sample
from .validate import validate_response
from .word_export import conversion_branches, load_reviewed_words, normalize_word


CYRILLIC_WORD_RE = re.compile(r"[А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]+")
LABELS = {"N", "RL", "U"}
MANUAL_MODEL_NAME = "manual-cli"
LABEL_STYLES = {"N": "green", "RL": "yellow", "U": "red"}


@dataclass(frozen=True)
class ManualPreannotateSummary:
    reviewed: int
    saved_tatar: int
    saved_non_tatar: int
    skipped: int
    remaining: int


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


class PlainManualUi:
    def __init__(self, output: Callable[[str], None]) -> None:
        self.output = output

    def print_sentence(
        self,
        *,
        index: int,
        total: int,
        sample: Sample,
        last_error: str | None,
    ) -> None:
        self.output("")
        self.output(f"[{index}/{total}] {sample.id}")
        self.output(sample.text)
        if last_error:
            self.output(f"last_error: {last_error}")

    def print_tokens(self, tokens: list[EditableToken]) -> None:
        for index, token in enumerate(tokens, start=1):
            homonym = " homonym" if token.homonym else ""
            self.output(f"{index:>3}. {token.text} [{token.label}{homonym}]")


class RichManualUi(PlainManualUi):
    def __init__(self, output: Callable[[str], None]) -> None:
        super().__init__(output)
        from rich.box import ROUNDED
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        self._box = ROUNDED
        self._console = Console()
        self._panel = Panel
        self._table = Table
        self._text = Text

    def print_sentence(
        self,
        *,
        index: int,
        total: int,
        sample: Sample,
        last_error: str | None,
    ) -> None:
        body = self._text()
        body.append(sample.text, style="bold white")
        if last_error:
            body.append("\n\nlast_error: ", style="bold red")
            body.append(_shorten(last_error, 360), style="red")
        self._console.print()
        self._console.print(
            self._panel(
                body,
                title=f"[bold cyan]{sample.id}[/] [dim]({index}/{total})[/]",
                border_style="cyan",
                box=self._box,
                expand=False,
            )
        )

    def print_tokens(self, tokens: list[EditableToken]) -> None:
        table = self._table(
            title="Tokens",
            box=self._box,
            show_lines=False,
            header_style="bold cyan",
        )
        table.add_column("#", justify="right", style="dim", no_wrap=True)
        table.add_column("Token", style="bold")
        table.add_column("Label", justify="center", no_wrap=True)
        table.add_column("Homonym", justify="center", no_wrap=True)
        for index, token in enumerate(tokens, start=1):
            label_style = LABEL_STYLES.get(token.label, "white")
            table.add_row(
                str(index),
                token.text,
                f"[bold {label_style}]{token.label}[/]",
                "[magenta]yes[/]" if token.homonym else "",
            )
        self._console.print(table)


def _make_ui(output: Callable[[str], None], *, rich_ui: bool) -> PlainManualUi:
    if not rich_ui:
        return PlainManualUi(output)
    try:
        return RichManualUi(output)
    except ImportError:
        return PlainManualUi(output)


def tokenize_sentence(text: str) -> list[str]:
    """Extract exact Cyrillic word tokens from a sentence."""
    return [match.group(0) for match in CYRILLIC_WORD_RE.finditer(text)]


def run_manual_preannotation(
    db_path: str,
    *,
    input_func: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    limit: int | None = None,
    rich_ui: bool = True,
) -> ManualPreannotateSummary:
    """Interactively repair rows whose preannotation status is unprocessable."""
    ui = _make_ui(output, rich_ui=rich_ui and output is print)
    with db.connect(db_path) as conn:
        db.ensure_preannotation_schema(conn)
        reviewed = load_reviewed_words(db_path)
        prior_labels, prior_homonyms = _load_prior_token_stats(conn)
        rows = _load_unprocessable(conn, limit=limit)
        saved_tatar = 0
        saved_non_tatar = 0
        skipped = 0

        for index, row in enumerate(rows, start=1):
            sample = Sample(id=row["id"], text=row["text"])
            ui.print_sentence(
                index=index,
                total=len(rows),
                sample=sample,
                last_error=row["last_error"],
            )

            decision = _ask_tatar_decision(sample.text, input_func=input_func, output=output)
            if decision == "quit":
                break
            if decision == "skip":
                skipped += 1
                continue
            if decision == "non_tatar":
                _save_item(conn, sample, {"id": sample.id, "tatar": False, "tokens": []})
                saved_non_tatar += 1
                continue

            tokens = [
                EditableToken(
                    text=token,
                    label=_suggest_label(token, reviewed, prior_labels),
                    homonym=_suggest_homonym(token, prior_homonyms),
                )
                for token in tokenize_sentence(sample.text)
            ]
            action = _edit_tokens(
                sample,
                tokens,
                input_func=input_func,
                output=output,
                ui=ui,
            )
            if action == "quit":
                break
            if action == "skip":
                skipped += 1
                continue
            item = {"id": sample.id, "tatar": True, "tokens": [token.to_json() for token in tokens]}
            _save_item(conn, sample, item)
            saved_tatar += 1

        remaining = _count_unprocessable(conn)
    return ManualPreannotateSummary(
        reviewed=saved_tatar + saved_non_tatar,
        saved_tatar=saved_tatar,
        saved_non_tatar=saved_non_tatar,
        skipped=skipped,
        remaining=remaining,
    )


def apply_token_command(tokens: list[EditableToken], command: str) -> None:
    """Apply one token-editing command to editable tokens."""
    command = command.strip()
    if not command:
        return
    if command == "show":
        return
    if command.startswith("all="):
        label = _parse_label(command[4:])
        for token in tokens:
            token.label = label
            if label != "RL":
                token.homonym = False
        return
    if command.endswith("h") and command[:-1].isdigit():
        index = int(command[:-1]) - 1
        _require_index(tokens, index)
        if tokens[index].label != "RL":
            raise ManualPreannotateError("homonym can only be set on RL tokens")
        tokens[index].homonym = not tokens[index].homonym
        return
    if "=" not in command:
        raise ManualPreannotateError(f"unknown command: {command}")
    target, raw_label = command.split("=", 1)
    label = _parse_label(raw_label)
    if "-" in target:
        raw_start, raw_end = target.split("-", 1)
        if not raw_start.isdigit() or not raw_end.isdigit():
            raise ManualPreannotateError(f"invalid range: {target}")
        start = int(raw_start) - 1
        end = int(raw_end) - 1
        _require_index(tokens, start)
        _require_index(tokens, end)
        if start > end:
            raise ManualPreannotateError(f"invalid range: {target}")
        indexes = range(start, end + 1)
    else:
        if not target.isdigit():
            raise ManualPreannotateError(f"invalid token index: {target}")
        index = int(target) - 1
        _require_index(tokens, index)
        indexes = range(index, index + 1)
    for index in indexes:
        tokens[index].label = label
        if label != "RL":
            tokens[index].homonym = False


def _ask_tatar_decision(
    text: str,
    *,
    input_func: Callable[[str], str],
    output: Callable[[str], None],
) -> str:
    suggested = "t" if count_tatar_specific_letters(text) >= 2 else "n"
    while True:
        answer = input_func(f"Mostly Tatar? [Y/n/s/q] default={'Y' if suggested == 't' else 'n'}: ")
        answer = answer.strip().lower()
        if not answer:
            return "tatar" if suggested == "t" else "non_tatar"
        if answer in {"y", "yes", "t", "tatar"}:
            return "tatar"
        if answer in {"n", "no", "non", "non-tatar"}:
            return "non_tatar"
        if answer == "s":
            return "skip"
        if answer == "q":
            return "quit"
        output("Use Enter/y/t, n, s, or q.")


def _edit_tokens(
    sample: Sample,
    tokens: list[EditableToken],
    *,
    input_func: Callable[[str], str],
    output: Callable[[str], None],
    ui: PlainManualUi,
) -> str:
    while True:
        ui.print_tokens(tokens)
        answer = input_func(
            "Edit labels (3=RL, 2-5=N, all=U, 3h), Enter=save, s=skip, q=quit: "
        ).strip()
        if not answer:
            item = {"id": sample.id, "tatar": True, "tokens": [token.to_json() for token in tokens]}
            validation = validate_response(json.dumps([item], ensure_ascii=False), [sample])
            if validation.ok:
                return "save"
            output("Cannot save:")
            for error in validation.errors:
                output(f"  {error}")
            continue
        if answer == "s":
            return "skip"
        if answer == "q":
            return "quit"
        try:
            for command in _split_commands(answer):
                apply_token_command(tokens, command)
        except ManualPreannotateError as exc:
            output(f"error: {exc}")


def _split_commands(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\s]+", value) if part.strip()]


def _parse_label(raw: str) -> str:
    label = raw.strip().upper()
    if label not in LABELS:
        raise ManualPreannotateError(f"invalid label: {raw}")
    return label


def _require_index(tokens: list[EditableToken], index: int) -> None:
    if index < 0 or index >= len(tokens):
        raise ManualPreannotateError(f"token index out of range: {index + 1}")


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


def _save_item(conn: sqlite3.Connection, sample: Sample, item: dict[str, object]) -> None:
    raw = json.dumps([item], ensure_ascii=False)
    validation = validate_response(raw, [sample])
    if not validation.ok:
        raise ManualPreannotateError("; ".join(validation.errors))
    db.save_annotations(conn, validation.items, model=MANUAL_MODEL_NAME)


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


def _shorten(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
