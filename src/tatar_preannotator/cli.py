from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import signal
import sqlite3
from time import monotonic, sleep as real_sleep

from zamanalif_selector.progress import RichCliProgress

from .annotate import run_annotation
from .antat_reference import download_antat_reference
from .config import load_config
from .gemini_client import GoogleGeminiClient
from .training_export import TrainingExportError, export_training_dataset
from .word_export import (
    export_labelstudio_tasks_from_db,
    load_exported_words,
    mark_exported_words,
    write_outputs,
)


DEFAULT_DB_PATH = "data/zamanalif.sqlite"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tatar_preannotator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    annotate = subparsers.add_parser(
        "annotate",
        help="Run Gemini pre-annotation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    annotate.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite application database.")
    annotate.add_argument("--config", default="config.yaml", help="YAML config file.")
    annotate.add_argument(
        "--model",
        help="Gemini model to use; overrides gemini.model from config.",
    )

    export_words = subparsers.add_parser(
        "annotation-export",
        help="Export unique word forms for Label Studio Project 1 review.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    export_words.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite application database.")
    export_words.add_argument("--output", required=True, help="Label Studio JSON output.")
    export_words.add_argument("--max-items", type=int, help="Maximum exported words.")
    export_words.add_argument("--include-rl", action=argparse.BooleanOptionalAction, default=True)
    export_words.add_argument(
        "--include-unknown",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    export_words.add_argument("--min-frequency", type=int, default=1)
    export_words.add_argument(
        "--sort-by",
        choices=["frequency_desc", "word"],
        default="frequency_desc",
    )
    export_words.add_argument("--report-output", help="Report JSON output path.")
    export_words.add_argument(
        "--track-exported",
        action="store_true",
        help="Skip and persist exported words in SQLite.",
    )
    export_words.add_argument(
        "--state-db",
        help="SQLite DB for export state; defaults to --db.",
    )

    training_export = subparsers.add_parser(
        "training-export",
        help="Export resolved Cyrillic/Zamanalif training pairs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    training_export.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="SQLite application database.",
    )
    training_export.add_argument("--output", required=True, help="Training JSONL output.")
    training_export.add_argument(
        "--choice",
        action="append",
        default=[],
        metavar="RULE=OPTION",
        help="Override one registered DSL rule; repeat for multiple rules.",
    )

    antat = subparsers.add_parser(
        "download-antat-reference",
        help="Download Antat English-Tatar Cyrillic/Zamanalif dictionary into SQLite.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    antat.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite application database.")
    antat.add_argument("--resume", action="store_true", help="Continue existing Antat reference rows.")
    antat.add_argument("--force", action="store_true", help="Replace existing Antat reference tables.")

    args = parser.parse_args(argv)
    if args.command == "annotate":
        return _annotate(args)
    if args.command == "annotation-export":
        return _annotation_export(args)
    if args.command == "training-export":
        return _training_export(args)
    if args.command == "download-antat-reference":
        return _download_antat_reference(args)
    raise AssertionError(args.command)


def _annotate(args: argparse.Namespace) -> int:
    if not Path(args.db).exists():
        raise SystemExit(f"database file does not exist: {args.db}")
    try:
        config = load_config(args.config)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.model is not None:
        model = args.model.strip()
        if not model:
            raise SystemExit("--model must be a non-empty string")
        config = replace(config, model=model)
    with ShutdownController(config.graceful_shutdown_timeout_seconds) as shutdown:
        summary = run_annotation(
            db_path=args.db,
            config=config,
            client=GoogleGeminiClient(),
            sleep=shutdown.sleep,
            log=print,
            now=shutdown.now,
            shutdown_requested=shutdown.requested,
            force_shutdown=shutdown.forced,
            shutdown_deadline=shutdown.deadline,
        )
    print(
        "annotation stopped: "
        f"{summary.stopped_reason}; annotated={summary.annotated_count}; pending={summary.pending_count}"
    )
    if summary.error:
        print(f"error: {summary.error}")
    if summary.stopped_reason in {"fatal_error", "all_keys_exhausted"}:
        return 1
    if summary.stopped_reason == "forced_shutdown":
        return 130
    return 0


def _annotation_export(args: argparse.Namespace) -> int:
    if args.max_items is not None and args.max_items < 1:
        raise SystemExit("--max-items must be positive")
    if args.min_frequency < 1:
        raise SystemExit("--min-frequency must be positive")

    state_db = args.state_db or args.db
    already_exported = load_exported_words(state_db) if args.track_exported else set()
    try:
        export_kwargs = {
            "max_items": args.max_items,
            "include_rl": args.include_rl,
            "include_unknown": args.include_unknown,
            "min_frequency": args.min_frequency,
            "sort_by": args.sort_by,
            "already_exported": already_exported,
        }
        result = export_labelstudio_tasks_from_db(args.db, **export_kwargs)
        report_path = write_outputs(result, args.output, report_output=args.report_output)
        if args.track_exported:
            mark_exported_words(state_db, result.exported_words)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"annotation export failed: {exc}")
        return 1

    print(
        "annotation export complete: "
        f"exported={len(result.tasks)} output={args.output} report={report_path}"
    )
    return 0


def _training_export(args: argparse.Namespace) -> int:
    try:
        summary = export_training_dataset(
            args.db,
            args.output,
            choice_overrides=args.choice,
        )
    except (OSError, sqlite3.Error, TrainingExportError) as exc:
        print(f"training export failed: {exc}")
        return 1

    print(
        "training export complete: "
        f"exported={summary.exported_count} "
        f"skipped={summary.skipped_count} "
        f"output={summary.output_path} "
        f"manifest={summary.manifest_path}"
    )
    return 0


def _download_antat_reference(args: argparse.Namespace) -> int:
    if args.resume and args.force:
        raise SystemExit("--resume and --force cannot be used together")
    try:
        with RichCliProgress() as progress:
            summary = download_antat_reference(
                args.db,
                resume=args.resume,
                force=args.force,
                progress=progress,
                log=print,
            )
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Antat reference download failed: {exc}")
        return 1

    print(
        "Antat reference download complete: "
        f"output={summary.output_path} "
        f"listing_rows={summary.listing_rows} "
        f"entry_pages={summary.entry_pages} "
        f"skipped_entry_pages={summary.skipped_entry_pages} "
        f"aligned={summary.aligned_rows} "
        f"mismatches={summary.mismatch_rows} "
        f"missing_side={summary.missing_side_rows}"
    )
    return 0


class ShutdownController:
    def __init__(self, graceful_timeout_seconds: int, *, log=print):
        self.graceful_timeout_seconds = graceful_timeout_seconds
        self._log = log
        self._requested_at: float | None = None
        self._force_requested = False
        self._previous_handler = None

    def __enter__(self) -> "ShutdownController":
        self._previous_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        signal.signal(signal.SIGINT, self._previous_handler)

    def _handle_sigint(self, signum, frame) -> None:
        if self._requested_at is None:
            self._requested_at = self.now()
            self._log(
                "shutdown requested: waiting for current Gemini request, "
                "press Ctrl+C again to force stop"
            )
            return
        self._force_requested = True
        self._log("forced shutdown requested")

    def requested(self) -> bool:
        return self._requested_at is not None

    def forced(self) -> bool:
        return self._force_requested

    def deadline(self) -> float | None:
        if self._requested_at is None:
            return None
        return self._requested_at + self.graceful_timeout_seconds

    def now(self) -> float:
        return monotonic()

    def sleep(self, seconds: float) -> None:
        end_at = self.now() + seconds
        while True:
            if self.requested() or self.forced():
                return
            remaining = end_at - self.now()
            if remaining <= 0:
                return
            real_sleep(min(0.25, remaining))
