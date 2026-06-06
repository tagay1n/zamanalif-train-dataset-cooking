from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from pathlib import Path
import random
import re
import sqlite3
from typing import Any

from .features import (
    TATAR_SPECIFIC_LETTERS,
    count_tatar_specific_letters,
    has_conditional_letter,
    has_min_tatar_specific_letters,
    has_mixed_harmony_word,
    sentence_quality,
)
from .io import read_jsonl, write_json, write_jsonl
from .progress import cli_progress
from .report import build_report
from .segment import split_sentence_records
from .selector import select_candidates_streaming


DATASET_NAME = "yasalma/tt-structured-content"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zamanalif-select")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Download dataset and write sentence candidates.")
    prepare.add_argument("--dataset", default=DATASET_NAME)
    prepare.add_argument("--split", default="train")
    prepare.add_argument("--output", default="data/candidates.jsonl")
    prepare.add_argument("--min-chars", type=int, default=20)
    prepare.add_argument("--max-chars", type=int, default=400)
    prepare.add_argument("--seed", type=int, default=13)
    prepare.add_argument("--max-docs", type=int, default=None)
    prepare.add_argument("--max-candidates", type=int, default=250_000)
    prepare.add_argument("--max-candidates-per-doc", type=int, default=30)
    prepare.add_argument("--max-doc-chars", type=int, default=20_000)
    prepare.add_argument("--window-chars", type=int, default=4_000)
    prepare.add_argument("--windows-per-doc", type=int, default=5)
    prepare.add_argument("--general-keep-probability", type=float, default=0.03)
    prepare.add_argument("--min-tatar-specific-letters", type=int, default=2)
    prepare.add_argument(
        "--exhaustive",
        action="store_true",
        help="Keep every accepted sentence from every document.",
    )
    prepare.add_argument("--quiet", action="store_true", help="Disable progress output.")

    select = subparsers.add_parser("select", help="Select weighted sentence examples.")
    select.add_argument("--candidates", default="data/candidates.jsonl")
    select.add_argument("--output", default="data/selected.sqlite")
    select.add_argument("--target-size", type=int, default=10_000)
    select.add_argument("--seed", type=int, default=13)
    select.add_argument("--min-word-frequency", type=int, default=2)
    select.add_argument("--max-word-frequency", type=int, default=10_000)
    select.add_argument("--conditional-target-ratio", type=float, default=0.85)
    select.add_argument("--multi-conditional-target-ratio", type=float, default=0.50)
    select.add_argument("--shortlist-size", type=int, default=250_000)
    select.add_argument("--source-penalty", type=float, default=0.15)
    select.add_argument("--min-tatar-specific-letters", type=int, default=2)
    select.add_argument("--force", action="store_true", help="Overwrite an existing SQLite output.")
    select.add_argument("--quiet", action="store_true", help="Disable progress output.")

    report = subparsers.add_parser("report", help="Regenerate report from selected Parquet.")
    report.add_argument("--selected", default="data/selected.parquet")
    report.add_argument("--output", default="data/report.json")
    report.add_argument("--seed", type=int, default=13)
    report.add_argument("--quiet", action="store_true", help="Disable progress output.")

    migrate = subparsers.add_parser(
        "export-parquet-to-sqlite",
        help="Export existing selected Parquet rows to the minimal SQLite annotation queue.",
    )
    migrate.add_argument("--input", required=True)
    migrate.add_argument("--output", default="data/selected.sqlite")
    migrate.add_argument("--force", action="store_true", help="Overwrite an existing SQLite output.")

    args = parser.parse_args(argv)
    if args.command == "prepare":
        return _prepare(args)
    if args.command == "select":
        return _select(args)
    if args.command == "report":
        return _report(args)
    if args.command == "export-parquet-to-sqlite":
        return _export_parquet_to_sqlite(args)
    raise AssertionError(args.command)


def _prepare(args: argparse.Namespace) -> int:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise SystemExit("Install project dependencies before running prepare.") from exc

    dataset = load_dataset(args.dataset, split=args.split)
    rows: list[dict[str, Any]] = []
    diagnostics: Counter[str] = Counter()
    total_docs = _safe_len(dataset)
    if args.max_docs is not None and total_docs is not None:
        total_docs = min(total_docs, args.max_docs)
    rng = random.Random(args.seed)
    seen_sentences: set[str] = set()
    with cli_progress(quiet=args.quiet) as progress:
        task = progress.add_task("prepare documents", total=total_docs)
        for doc_index, source in enumerate(dataset):
            if args.max_docs is not None and doc_index >= args.max_docs:
                diagnostics["stopped:max_docs"] += 1
                break
            if not args.exhaustive and args.max_candidates is not None and len(rows) >= args.max_candidates:
                diagnostics["stopped:max_candidates"] += 1
                break
            text = source.get("text") or ""
            remaining_candidates = None
            if not args.exhaustive and args.max_candidates is not None:
                remaining_candidates = args.max_candidates - len(rows)
            doc_rows = _prepare_source_rows(
                source,
                text,
                exhaustive=args.exhaustive,
                min_chars=args.min_chars,
                max_chars=args.max_chars,
                max_doc_chars=args.max_doc_chars,
                window_chars=args.window_chars,
                windows_per_doc=args.windows_per_doc,
                max_candidates_per_doc=args.max_candidates_per_doc,
                max_candidates_for_doc=remaining_candidates,
                general_keep_probability=args.general_keep_probability,
                min_tatar_specific_letters=args.min_tatar_specific_letters,
                rng=rng,
                seen_sentences=seen_sentences,
                diagnostics=diagnostics,
            )
            rows.extend(doc_rows)
            if not args.exhaustive and args.max_candidates is not None and len(rows) >= args.max_candidates:
                diagnostics["stopped:max_candidates"] += 1
                progress.advance(task, summary=_diagnostic_summary(rows, diagnostics))
                break
            progress.advance(task, summary=_diagnostic_summary(rows, diagnostics))
        with progress.status(f"writing candidates to {args.output}"):
            write_jsonl(args.output, rows)
    print(f"wrote {len(rows)} candidates to {args.output}")
    if diagnostics:
        diagnostic_summary = ", ".join(
            f"{key}={diagnostics[key]}" for key in sorted(diagnostics)
        )
        print(f"sentence split diagnostics: {diagnostic_summary}")
    return 0


def _prepare_source_rows(
    source: dict[str, Any],
    text: str,
    *,
    exhaustive: bool,
    min_chars: int,
    max_chars: int,
    max_doc_chars: int,
    window_chars: int,
    windows_per_doc: int,
    max_candidates_per_doc: int | None,
    max_candidates_for_doc: int | None,
    general_keep_probability: float,
    min_tatar_specific_letters: int,
    rng: random.Random,
    seen_sentences: set[str],
    diagnostics: Counter[str],
) -> list[dict[str, Any]]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    seen_in_doc: set[str] = set()
    windows = _document_windows(
        text,
        exhaustive=exhaustive,
        max_doc_chars=max_doc_chars,
        window_chars=window_chars,
        windows_per_doc=windows_per_doc,
        rng=rng,
    )
    if not exhaustive and len(text) > max_doc_chars:
        diagnostics["prepare:docs_windowed"] += 1

    for window_index, window_start, window_text in windows:
        diagnostics["prepare:windows_scanned"] += 1
        result = split_sentence_records(window_text, min_chars=min_chars, max_chars=max_chars)
        diagnostics.update(result.diagnostics)
        for record in result.sentences:
            keep_reason = _candidate_keep_reason(
                record.sentence,
                exhaustive=exhaustive,
                general_keep_probability=general_keep_probability,
                rng=rng,
            )
            if keep_reason is None:
                diagnostics["prefilter:skipped_general"] += 1
                continue
            if not exhaustive and not has_min_tatar_specific_letters(
                record.sentence,
                min_count=min_tatar_specific_letters,
            ):
                diagnostics["prefilter:skipped_language"] += 1
                continue
            quality = sentence_quality(record.sentence)
            if not exhaustive and quality.is_artifact:
                diagnostics["prefilter:skipped_quality"] += 1
                for reason in quality.artifact_reasons:
                    diagnostics[f"prefilter:skipped_quality:{reason}"] += 1
                continue

            sentence_key = _sentence_key(record.sentence)
            if sentence_key in seen_in_doc:
                diagnostics["prefilter:skipped_doc_duplicate"] += 1
                continue
            if sentence_key in seen_sentences:
                diagnostics["prefilter:skipped_global_duplicate"] += 1
                continue
            seen_in_doc.add(sentence_key)

            row = {
                "id": source.get("id"),
                "source_sentence_index": len(candidates),
                "source_start_char": window_start + record.start_char,
                "source_end_char": window_start + record.end_char,
                "reason": keep_reason,
                "index": window_index,
                "tatar_specific_letter_count": count_tatar_specific_letters(record.sentence),
                "tatar_specific_letters": "".join(
                    sorted(
                        {
                            char
                            for char in record.sentence.lower()
                            if char in TATAR_SPECIFIC_LETTERS
                        }
                    )
                ),
                "quality_penalty": quality.penalty,
                "quality_reasons": ",".join(quality.artifact_reasons),
                "sentence": record.sentence,
            }
            candidates.append((_keep_priority(keep_reason), row))

    candidates.sort(key=lambda item: (item[0], item[1]["source_start_char"], item[1]["sentence"]))
    if not exhaustive and max_candidates_per_doc is not None and len(candidates) > max_candidates_per_doc:
        diagnostics["prefilter:skipped_doc_cap"] += len(candidates) - max_candidates_per_doc
        candidates = candidates[:max_candidates_per_doc]
    if not exhaustive and max_candidates_for_doc is not None and len(candidates) > max_candidates_for_doc:
        diagnostics["prefilter:skipped_global_cap"] += len(candidates) - max_candidates_for_doc
        candidates = candidates[:max_candidates_for_doc]

    rows: list[dict[str, Any]] = []
    for source_sentence_index, (_, row) in enumerate(candidates):
        row["source_sentence_index"] = source_sentence_index
        seen_sentences.add(_sentence_key(row["sentence"]))
        diagnostics[f"prefilter:kept:{row['reason']}"] += 1
        rows.append(row)
    return rows


def _document_windows(
    text: str,
    *,
    exhaustive: bool,
    max_doc_chars: int,
    window_chars: int,
    windows_per_doc: int,
    rng: random.Random,
) -> list[tuple[int, int, str]]:
    if exhaustive or len(text) <= max_doc_chars:
        return [(0, 0, text)]
    if window_chars <= 0 or windows_per_doc <= 0:
        return []

    budget = max(1, min(max_doc_chars, window_chars * windows_per_doc))
    window_count = max(1, min(windows_per_doc, (budget + window_chars - 1) // window_chars))
    max_start = max(0, len(text) - window_chars)
    starts: set[int] = {0, max_start}
    if window_count > 2:
        for index in range(1, window_count - 1):
            starts.add(round(max_start * index / (window_count - 1)))
    while len(starts) < window_count:
        starts.add(rng.randint(0, max_start))
    ordered = sorted(starts)[:window_count]
    return [
        (window_index, start, text[start : start + window_chars])
        for window_index, start in enumerate(ordered)
    ]


def _keep_priority(keep_reason: str) -> int:
    return {
        "exhaustive": 0,
        "conditional": 0,
        "mixed_harmony": 1,
        "general_sample": 2,
    }.get(keep_reason, 9)


def _sentence_key(sentence: str) -> str:
    normalized = re.sub(r"\s+", " ", sentence.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _select(args: argparse.Namespace) -> int:
    with cli_progress(quiet=args.quiet) as progress:
        result = select_candidates_streaming(
            args.candidates,
            target_size=args.target_size,
            seed=args.seed,
            min_word_frequency=args.min_word_frequency,
            max_word_frequency=args.max_word_frequency,
            conditional_target_ratio=args.conditional_target_ratio,
            multi_conditional_target_ratio=args.multi_conditional_target_ratio,
            shortlist_size=args.shortlist_size,
            source_penalty=args.source_penalty,
            min_tatar_specific_letters=args.min_tatar_specific_letters,
            progress=progress,
        )
        selected = result.selected
        with progress.status(f"writing selected SQLite to {args.output}"):
            _write_selected_sqlite(args.output, selected, force=args.force)
    print(f"wrote {len(selected)} selected sentences to {args.output}")
    return 0


def _report(args: argparse.Namespace) -> int:
    with cli_progress(quiet=args.quiet) as progress:
        with progress.status(f"reading selected Parquet from {args.selected}"):
            selected = _read_parquet(args.selected)
        with progress.status(f"writing report to {args.output}"):
            write_json(args.output, build_report(selected, seed=args.seed, config={}))
    print(f"wrote report to {args.output}")
    return 0


def _write_parquet(path: str | Path, rows: list[dict[str, Any]]) -> None:
    try:
        import pandas as pd
    except ImportError as exc:
        raise SystemExit("Install pandas and pyarrow before writing Parquet.") from exc

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def _read_parquet(path: str | Path) -> list[dict[str, Any]]:
    try:
        import pandas as pd
    except ImportError as exc:
        raise SystemExit("Install pandas and pyarrow before reading Parquet.") from exc

    return pd.read_parquet(path).to_dict(orient="records")


def _export_parquet_to_sqlite(args: argparse.Namespace) -> int:
    rows = _read_parquet(args.input)
    rows.sort(key=lambda row: int(row.get("selected_rank") or 0))
    _write_selected_sqlite(args.output, rows, force=args.force)
    print(f"wrote {len(rows)} selected sentences to {args.output}")
    return 0


def _write_selected_sqlite(path: str | Path, rows: list[dict[str, Any]], *, force: bool) -> None:
    path = Path(path)
    if path.exists():
        if not force:
            raise SystemExit(f"{path} already exists; pass --force to overwrite it.")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE samples (
              id TEXT PRIMARY KEY,
              source_id TEXT,
              text TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE preannotation_state (
              sample_id TEXT PRIMARY KEY REFERENCES samples(id),
              status TEXT NOT NULL,
              tatar INTEGER,
              tokens_json TEXT,
              attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              updated_at TEXT NOT NULL
            )
            """
        )
        now = _utc_now()
        sample_rows = []
        state_rows = []
        for index, row in enumerate(rows, start=1):
            sample_id = f"sent_{index:06d}"
            source_id = row.get("id")
            sentence = row.get("sentence") or row.get("text")
            if sentence is None:
                raise SystemExit(f"selected row {index} has no sentence/text field")
            sample_rows.append((sample_id, str(source_id) if source_id is not None else None, str(sentence)))
            state_rows.append((sample_id, "pending", None, None, 0, None, now))
        conn.executemany("INSERT INTO samples (id, source_id, text) VALUES (?, ?, ?)", sample_rows)
        conn.executemany(
            """
            INSERT INTO preannotation_state (
              sample_id, status, tatar, tokens_json, attempts, last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            state_rows,
        )


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _read_candidates_with_progress(path: str | Path, progress: Any) -> list[dict[str, Any]]:
    total = _count_lines(path)
    task = progress.add_task("select read candidates", total=total)
    rows: list[dict[str, Any]] = []
    for row in read_jsonl(path):
        rows.append(row)
        progress.advance(task, summary=f"rows={len(rows)}")
    return rows


def _count_lines(path: str | Path) -> int:
    with Path(path).open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _safe_len(value: object) -> int | None:
    try:
        return len(value)  # type: ignore[arg-type]
    except TypeError:
        return None


def _diagnostic_summary(rows: list[dict[str, Any]], diagnostics: Counter[str]) -> str:
    parts = [f"cand={len(rows)}"]
    for label, key in [
        ("win", "prepare:windows_scanned"),
        ("wdoc", "prepare:docs_windowed"),
        ("cond", "prefilter:kept:conditional"),
        ("mix", "prefilter:kept:mixed_harmony"),
        ("gen", "prefilter:kept:general_sample"),
        ("skip", "prefilter:skipped_general"),
        ("lang", "prefilter:skipped_language"),
        ("qual", "prefilter:skipped_quality"),
        ("cap", "prefilter:skipped_doc_cap"),
        ("gcap", "prefilter:skipped_global_cap"),
        ("dup", "prefilter:skipped_global_duplicate"),
        ("short", "rejected:too_short"),
        ("long", "rejected:too_long"),
        ("noncyr", "rejected:non_cyrillic"),
    ]:
        if diagnostics[key]:
            parts.append(f"{label}={diagnostics[key]}")
    return " ".join(parts)


def _candidate_keep_reason(
    sentence: str,
    *,
    exhaustive: bool,
    general_keep_probability: float,
    rng: random.Random,
) -> str | None:
    if exhaustive:
        return "exhaustive"
    if has_conditional_letter(sentence):
        return "conditional"
    if has_mixed_harmony_word(sentence):
        return "mixed_harmony"
    if general_keep_probability > 0 and rng.random() < general_keep_probability:
        return "general_sample"
    return None


if __name__ == "__main__":
    raise SystemExit(main())
