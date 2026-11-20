"""Command-line interface for the corpus audit."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

from .corpus_corrections import correct
from .corpus_index import CorpusAuditError, connect, occurrences, reindex, search


def add_corpus_commands(subparsers, default_db: str):
    parser = subparsers.add_parser("corpus-audit", help="Search and correct the annotated corpus.")
    commands = parser.add_subparsers(dest="audit_command", required=True)
    for action, description in (
        ("reindex", "Build the search index under an export policy."),
        ("search", "Find word forms or list the occurrences of a search result."),
        ("serve", "Start the local Datasette browser."),
        ("preview", "Validate a correction request and print the before-and-after preview."),
        ("apply", "Apply a previously previewed correction batch."),
    ):
        command = commands.add_parser(action, help=description)
        command.add_argument("--db", type=Path, default=Path(default_db))
        if action == "reindex":
            command.add_argument("--choice", action="append", default=[], metavar="RULE=OPTION")
        elif action == "search":
            lookup = command.add_mutually_exclusive_group(required=True)
            lookup.add_argument("--query")
            lookup.add_argument("--form-id", type=int)
            command.add_argument("--field", default="cyrillic", choices=("cyrillic", "zamanalif"))
            command.add_argument("--mode", default="contains", choices=("contains", "exact"))
            command.add_argument("--limit", type=int, default=100)
            command.add_argument("--after", type=int, default=0, help="Continue after this form ID.")
            command.add_argument("--after-sample", default="")
            command.add_argument("--after-token", type=int, default=-1)
        elif action == "serve":
            command.add_argument("--port", type=int, default=8767)
        else:
            command.add_argument("--input", required=True, type=Path)


def run_corpus_command(args) -> int:
    try:
        action = args.audit_command
        if action == "reindex":
            result = reindex(args.db, args.choice)
        elif action == "search":
            if args.limit < 1:
                raise CorpusAuditError("--limit requires a positive integer.")
            with closing(connect(args.db)) as connection, connection:
                connection.execute("begin")
                if args.form_id is None:
                    result = search(connection, args.field, args.mode, args.query,
                                    after=args.after, limit=args.limit)
                else:
                    result = occurrences(connection, args.form_id, after_sample=args.after_sample,
                                         after_token=args.after_token, limit=args.limit)
        elif action == "serve":
            try:
                from .corpus_web import serve
            except ModuleNotFoundError as exc:
                if exc.name != "datasette":
                    raise
                raise CorpusAuditError("Install the browser dependency with pip install -e '.[audit]'.") from exc
            serve(args.db, args.port)
            return 0
        else:
            request = json.loads(args.input.read_text(encoding="utf-8"))
            if action == "preview":
                result = correct(args.db, request)
            else:
                if not isinstance(request, dict) or "request" not in request:
                    raise CorpusAuditError("Pass the JSON returned by corpus-audit preview.")
                result = correct(args.db, request["request"], approved=request)
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"Corpus audit: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
