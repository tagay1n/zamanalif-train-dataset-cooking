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
from .conflict_resolver import (
    ConflictResolverError,
    auto_resolve_conflicts,
    serve_conflict_resolver_web,
)
from .contextual_review import (
    PROJECT_KEY as CONTEXTUAL_PROJECT_KEY,
    export_contextual_tasks_from_db,
)
from .gemini_client import GoogleGeminiClient
from .labelstudio_import import (
    LabelStudioImportError,
    audit_labelstudio_export,
    import_labelstudio_annotations,
)
from .local_repair import repair_unprocessable
from .manual_preannotate import ManualPreannotateError
from .manual_preannotate_web import serve_manual_preannotation_web
from .morphology import MorphologyError, default_morphology_analyzer
from .training_export import TrainingExportError, export_training_dataset
from .word_export import (
    attach_contextual_project,
    export_labelstudio_project_tasks_from_db,
    write_split_outputs,
)


DEFAULT_DB_PATH = "data/zamanalif.sqlite"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tatar_preannotator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        allow_abbrev=False,
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
    annotate.add_argument(
        "--retry-unprocessable",
        action="store_true",
        help="Requeue all unprocessable samples before annotation.",
    )

    repair = subparsers.add_parser(
        "repair-unprocessable",
        help="Repair unprocessable sentence preannotations locally.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    repair.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite application database.")
    repair.add_argument("--limit", type=int, help="Maximum unprocessable rows to repair.")
    repair.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows would be repaired without writing changes.",
    )

    export_words = subparsers.add_parser(
        "annotation-export",
        help="Export dictionary and contextual Label Studio projects.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        allow_abbrev=False,
    )
    export_words.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite application database.")
    export_words.add_argument(
        "--output-dir",
        required=True,
        help="Directory for split Label Studio project JSON outputs.",
    )
    export_words.add_argument(
        "--max-items",
        type=int,
        help="Maximum dictionary words and contextual occurrences, applied separately.",
    )
    export_words.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Maximum tasks per generated Label Studio JSON file.",
    )
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
    export_words.add_argument(
        "--apertium-tat-dir",
        help="Compiled pinned Apertium-tat directory.",
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

    annotation_import = subparsers.add_parser(
        "annotation-import",
        help="Import completed Label Studio word annotations into SQLite.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    annotation_import.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="SQLite application database.",
    )
    annotation_import.add_argument(
        "--input",
        required=True,
        help="Label Studio JSON export.",
    )
    annotation_import.add_argument(
        "--apertium-tat-dir",
        help="Compiled pinned Apertium-tat directory.",
    )

    annotation_audit = subparsers.add_parser(
        "annotation-audit",
        help="Validate Label Studio annotations and show genuine human edits.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    annotation_audit.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="SQLite application database.",
    )
    annotation_audit.add_argument(
        "--input",
        required=True,
        help="Label Studio JSON export or task API response.",
    )

    antat = subparsers.add_parser(
        "download-antat-reference",
        help="Download Antat English-Tatar Cyrillic/Zamanalif dictionary into SQLite.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    antat.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite application database.")
    antat.add_argument("--resume", action="store_true", help="Continue existing Antat reference rows.")
    antat.add_argument("--force", action="store_true", help="Replace existing Antat reference tables.")

    manual = subparsers.add_parser(
        "manual-preannotate",
        help="Run a local browser UI for unprocessable sentence preannotations.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    manual.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite application database.")
    manual.add_argument("--host", default="127.0.0.1", help="Local bind host.")
    manual.add_argument("--port", type=int, default=8765, help="Local bind port.")
    manual.add_argument("--limit", type=int, help="Maximum unprocessable rows to review.")

    conflicts = subparsers.add_parser(
        "resolve-conflicts",
        help="Run a local browser UI for word-origin and homonym conflicts.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    conflicts.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite application database.")
    conflicts.add_argument("--host", default="127.0.0.1", help="Local bind host.")
    conflicts.add_argument("--port", type=int, default=8766, help="Local bind port.")
    conflicts.add_argument("--limit", type=int, help="Maximum conflict rows to review.")

    auto_conflicts = subparsers.add_parser(
        "auto-resolve-conflicts",
        help="Conservatively auto-resolve low-risk word conflicts.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    auto_conflicts.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite application database.")
    auto_conflicts.add_argument(
        "--dry-run",
        action="store_true",
        help="Report decisions without writing word_resolutions.",
    )

    args = parser.parse_args(argv)
    if args.command == "annotate":
        return _annotate(args)
    if args.command == "repair-unprocessable":
        return _repair_unprocessable(args)
    if args.command == "annotation-export":
        return _annotation_export(args)
    if args.command == "training-export":
        return _training_export(args)
    if args.command == "annotation-import":
        return _annotation_import(args)
    if args.command == "annotation-audit":
        return _annotation_audit(args)
    if args.command == "download-antat-reference":
        return _download_antat_reference(args)
    if args.command == "manual-preannotate":
        return _manual_preannotate(args)
    if args.command == "resolve-conflicts":
        return _resolve_conflicts(args)
    if args.command == "auto-resolve-conflicts":
        return _auto_resolve_conflicts(args)
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
            retry_unprocessable=args.retry_unprocessable,
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


def _repair_unprocessable(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    try:
        summary = repair_unprocessable(args.db, limit=args.limit, dry_run=args.dry_run)
    except (OSError, sqlite3.Error, ManualPreannotateError) as exc:
        print(f"local repair failed: {exc}")
        return 1
    mode = "dry-run" if summary.dry_run else "written"
    print(
        "local repair complete: "
        f"mode={mode} "
        f"total={summary.total} "
        f"repaired={summary.repaired} "
        f"skipped_low_tatar_specific={summary.skipped_low_tatar_specific} "
        f"skipped_no_tokens={summary.skipped_no_tokens} "
        f"invalid={summary.invalid} "
        f"remaining={summary.remaining}"
    )
    return 0


def _annotation_export(args: argparse.Namespace) -> int:
    if args.max_items is not None and args.max_items < 1:
        raise SystemExit("--max-items must be positive")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    if args.min_frequency < 1:
        raise SystemExit("--min-frequency must be positive")

    try:
        export_kwargs = {
            "max_items": args.max_items,
            "include_rl": args.include_rl,
            "include_unknown": args.include_unknown,
            "min_frequency": args.min_frequency,
            "sort_by": args.sort_by,
            "morphology_analyzer": default_morphology_analyzer(
                args.apertium_tat_dir
            ),
        }
        result = export_labelstudio_project_tasks_from_db(args.db, **export_kwargs)
        contextual = export_contextual_tasks_from_db(
            args.db,
            max_items=args.max_items,
        )
        result = attach_contextual_project(result, contextual)
        write_split_outputs(result, args.output_dir, batch_size=args.batch_size)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"annotation export failed: {exc}")
        return 1

    print(
        "annotation export complete: "
        f"dictionary={len(result.exported_words)} "
        f"contextual={len(result.contextual_occurrences)} "
        f"contextual_pending={contextual.report['pending_occurrence_count']} "
        f"initials_grouped={contextual.report['grouped_initial_occurrence_count']} "
        f"output={args.output_dir}"
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


def _annotation_import(args: argparse.Namespace) -> int:
    try:
        summary = import_labelstudio_annotations(
            args.db,
            args.input,
            morphology_analyzer=default_morphology_analyzer(
                args.apertium_tat_dir
            ),
        )
    except (OSError, sqlite3.Error, LabelStudioImportError, MorphologyError) as exc:
        print(f"annotation import failed: {exc}")
        return 1

    print(
        "annotation import complete: "
        f"project={summary.project_key} "
        f"tasks={summary.total_tasks} "
        f"completed={summary.completed_tasks} "
        f"imported={summary.imported_items} "
        f"homonyms={summary.homonym_items} "
        f"inherited={summary.inherited_items} "
        f"unchanged={summary.unchanged_items} "
        f"unannotated={summary.skipped_unannotated_tasks}"
    )
    if summary.project_key != CONTEXTUAL_PROJECT_KEY:
        print(
            "family propagation: "
            f"inherited={summary.inherited_items} "
            f"current_batch={summary.inherited_current_batch_items} "
            f"historical_backfill={summary.inherited_backfill_items} "
            f"literal_subwords={summary.inherited_literal_subword_items} "
            f"deterministic_divergent="
            f"{summary.inherited_deterministic_divergent_items} "
            f"source_families={summary.inherited_source_families}"
        )
    else:
        print(
            "initial propagation: "
            f"source_groups={summary.contextual_initial_source_groups} "
            f"propagated={summary.contextual_initial_propagated_items} "
            f"historical_backfill={summary.contextual_initial_backfill_items}"
        )
    return 0


def _annotation_audit(args: argparse.Namespace) -> int:
    try:
        summary = audit_labelstudio_export(args.db, args.input)
    except (OSError, LabelStudioImportError) as exc:
        print(f"annotation audit failed: {exc}")
        return 1

    print(
        "annotation audit complete: "
        f"project={summary.project_key} "
        f"tasks={summary.total_tasks} "
        f"completed={summary.completed_tasks} "
        f"unchanged={summary.unchanged_tasks} "
        f"changed={len(summary.changes)} "
        f"origin_changes={summary.origin_changes} "
        f"conversion_changes={summary.conversion_changes} "
        f"homonym_changes={summary.homonym_changes} "
        f"unannotated={summary.skipped_unannotated_tasks}"
    )
    if summary.project_key == CONTEXTUAL_PROJECT_KEY:
        print(
            "initial propagation: "
            f"source_groups={summary.contextual_initial_source_groups} "
            f"propagated={summary.contextual_initial_propagated_items} "
            f"historical_backfill={summary.contextual_initial_backfill_items}"
        )
    for change in summary.changes:
        print(f"task={change.task_id} word={change.word}")
        if change.is_homonym:
            print("  decision: contextual_homonym")
            continue
        if change.suggested_origin != change.reviewed_origin:
            print(
                f"  origin: {change.suggested_origin} -> {change.reviewed_origin}"
            )
        if change.suggested_zamanalif != change.reviewed_zamanalif:
            print(
                f"  conversion: "
                f"{change.suggested_zamanalif} -> {change.reviewed_zamanalif}"
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


def _manual_preannotate(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    if args.port < 1 or args.port > 65535:
        raise SystemExit("--port must be between 1 and 65535")
    try:
        serve_manual_preannotation_web(
            args.db,
            host=args.host,
            port=args.port,
            limit=args.limit,
            log=print,
        )
    except KeyboardInterrupt:
        print("\nmanual preannotation web UI stopped.")
        return 130
    except (OSError, sqlite3.Error, ManualPreannotateError) as exc:
        print(f"manual preannotation web UI failed: {exc}")
        return 1
    return 0


def _resolve_conflicts(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    if args.port < 1 or args.port > 65535:
        raise SystemExit("--port must be between 1 and 65535")
    try:
        serve_conflict_resolver_web(
            args.db,
            host=args.host,
            port=args.port,
            limit=args.limit,
            log=print,
        )
    except KeyboardInterrupt:
        print("\nword conflict resolver web UI stopped.")
        return 130
    except (OSError, sqlite3.Error, ConflictResolverError) as exc:
        print(f"word conflict resolver web UI failed: {exc}")
        return 1
    return 0


def _auto_resolve_conflicts(args: argparse.Namespace) -> int:
    try:
        summary = auto_resolve_conflicts(args.db, dry_run=args.dry_run)
    except (OSError, sqlite3.Error, ConflictResolverError) as exc:
        print(f"auto-resolve conflicts failed: {exc}")
        return 1
    mode = "dry-run" if summary.dry_run else "written"
    print(
        "auto-resolve conflicts complete: "
        f"mode={mode} "
        f"inspected={summary.inspected} "
        f"auto_resolved={summary.auto_resolved} "
        f"skipped_homonym_conflict={summary.skipped_homonym_conflict} "
        f"skipped_manual={summary.skipped_manual} "
        f"by_decision={summary.by_decision}"
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
