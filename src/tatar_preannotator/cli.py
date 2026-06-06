from __future__ import annotations

import argparse
from pathlib import Path

from .annotate import run_annotation
from .config import load_config
from .gemini_client import GoogleGeminiClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tatar_preannotator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    annotate = subparsers.add_parser("annotate", help="Run Gemini pre-annotation.")
    annotate.add_argument("--db", required=True)
    annotate.add_argument("--config", required=True)

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
    summary = run_annotation(
        db_path=args.db,
        config=config,
        client=GoogleGeminiClient(),
        sleep=lambda seconds: __import__("time").sleep(seconds),
    )
    print(
        "annotation stopped: "
        f"{summary.stopped_reason}; annotated={summary.annotated_count}; pending={summary.pending_count}"
    )
    return 0
