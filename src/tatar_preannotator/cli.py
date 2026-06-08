from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import signal
from time import monotonic, sleep as real_sleep

from .annotate import run_annotation
from .config import load_config
from .gemini_client import GoogleGeminiClient


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
    annotate.add_argument("--db", default="data/selected.sqlite", help="SQLite annotation queue.")
    annotate.add_argument("--config", default="config.yaml", help="YAML config file.")
    annotate.add_argument(
        "--model",
        help="Gemini model to use; overrides gemini.model from config.",
    )

    args = parser.parse_args(argv)
    if args.command == "annotate":
        return _annotate(args)
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
