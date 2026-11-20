"""Datasette integration for local corpus review."""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlencode

from datasette import Response, hookimpl
from datasette.app import Datasette, pm
from datasette.database import Database
from markupsafe import Markup

from .corpus_corrections import correct
from .corpus_index import CorpusAuditError, OCCURRENCES_SQL, SEARCH_SQL, connect, fold, records, state
from .contextual_review import _token_spans


def _url(path: str, **params) -> str:
    return path + ("?" + urlencode(params) if params else "")


def _search_url() -> str:
    return _url("/corpus/search", field="cyrillic", mode="contains", limit=100)


def _occurrence_url(form_id: int) -> str:
    return _url("/corpus/occurrences", form_id=form_id, after_token=-1, limit=100)


def _details(path: Path, args: dict) -> dict:
    with closing(connect(path)) as connection, connection:
        connection.execute("begin")
        current = state(connection)
        details = dict(search_url=_search_url(), revision=current["revision"], kind=args.get("kind", "bulk"))
        details.update({key: args.get(key, "") for key in ("field", "mode", "query")})
        if word := args.get("word"):
            details["word"] = word
            row = connection.execute("""
                select zamanalif_dsl, origin from reviewed_words where normalized_word = ?
            """, (word,)).fetchone()
            details["review"] = dict(row) if row else {}
            details["variants"] = [dict(row) for row in connection.execute("""
                select position, zamanalif, policies_json from reviewed_word_variants
                where normalized_word = ? order by position
            """, (word,))]
        if sample_id := args.get("sample_id"):
            index = int(args.get("token_index", "0"))
            row = next(records(connection, {sample_id}), None)
            if row is None:
                raise CorpusAuditError(f"Unknown or ineligible sentence {sample_id}.")
            tokens = json.loads(row["tokens_json"])
            if not 0 <= index < len(tokens):
                raise CorpusAuditError("Token index is outside the sentence.")
            start, end = _token_spans(sample_id, row["text"], tokens)[index]
            row_review = connection.execute("""
                select zamanalif_dsl, origin, normalized_word from contextual_reviews
                where sample_id = ? and token_index = ?
            """, (sample_id, index)).fetchone()
            details.update(sample_id=sample_id, token_index=index, tokens=tokens,
                           before_token=row["text"][:start], surface=row["text"][start:end],
                           after_token=row["text"][end:], contextual=dict(row_review) if row_review else {})
        return details


def _form_changes(values: dict) -> list[dict]:
    kind = values.get("kind")
    if kind == "bulk":
        item = {key: values[key] for key in ("target", "field", "mode", "query", "find", "replace")}
        if values.get("variant"):
            item["variant"] = int(values["variant"])
        if values.get("form_ids", "").strip():
            item["form_ids"] = [int(value.strip()) for value in values["form_ids"].split(",")]
    elif kind == "dictionary":
        item = {"word": values["word"]}
        if values.get("origin"):
            item["origin"] = values["origin"]
        if values.get("value"):
            item["value"] = values["value"]
        if values.get("variant"):
            item["variant"] = int(values["variant"])
    elif kind == "contextual":
        item = {"sample_id": values["sample_id"], "token_index": int(values["token_index"]),
                "value": values["value"], "origin": values["origin"]}
    elif kind == "source":
        item = {"sample_id": values["sample_id"], "start": int(values["token_index"]),
                "end": int(values["end"]), "value": values["value"]}
        if values.get("tokens", "").strip():
            tokens = []
            contextual = []
            for line in values["tokens"].splitlines():
                if not line.strip():
                    continue
                fields = [field.strip() for field in line.split("|")]
                if len(fields) not in (2, 3) or (len(fields) == 3 and fields[1] not in ("N", "RL")):
                    raise CorpusAuditError("Enter one token per line as text | origin or text | origin | contextual spelling.")
                token = dict(text=fields[0], label=fields[1])
                if len(fields) == 3:
                    token["homonym"] = True
                    contextual.append(dict(offset=len(tokens), origin=fields[1], value=fields[2]))
                tokens.append(token)
            item["tokens"] = tokens
            if contextual:
                item["contextual"] = contextual
        if values.get("replace_context") and "contextual" not in item:
            item["contextual"] = []
        approvals = []
        for line in values.get("approvals", "").splitlines():
            if not line.strip():
                continue
            fields = [field.strip() for field in line.split("|")]
            if len(fields) != 3:
                raise CorpusAuditError("Enter one approval per line as word | origin | spelling.")
            approvals.append(dict(kind="dictionary", word=fields[0], origin=fields[1], value=fields[2]))
        return [dict(item, kind=kind), *approvals]
    elif kind == "batch":
        return json.loads(values["batch"])
    else:
        raise CorpusAuditError("Choose a correction type.")
    return [dict(item, kind=kind)]


class CorpusPlugin:
    __name__ = __name__

    def __init__(self, datasette: Datasette, path: Path):
        self.datasette = datasette
        self.path = path

    @hookimpl
    def menu_links(self, datasette):
        if datasette is self.datasette:
            return [
                {"href": _search_url(), "label": "Corpus search"},
                {"href": "/-/corpus-edit?kind=bulk", "label": "Replace in matches"},
                {"href": "/-/corpus-edit?kind=batch", "label": "Correction batch"},
            ]

    @hookimpl
    def prepare_connection(self, conn, database, datasette):
        if datasette is self.datasette and database == "corpus":
            conn.create_function("audit_fold", 2, fold, deterministic=True)

    @hookimpl
    def render_cell(self, row, value, column, table, database, datasette):
        if datasette is not self.datasette or database != "corpus" or table is not None:
            return None
        if column == "occurrences" and "form_id" in row.keys():
            link = Markup('<a href="{}">{} occurrences</a>').format(_occurrence_url(row["form_id"]), value)
            if row["source"] not in ("contextual", "contextual_homonym", "stale_contextual"):
                link += Markup('<br><a href="{}">Dictionary correction</a>').format(
                    _url("/-/corpus-edit", kind="dictionary", word=row["cyrillic"])
                )
            return link
        if column == "surface" and "token_index" in row.keys():
            identity = dict(sample_id=row["sample_id"], token_index=row["token_index"])
            link = Markup('{}<br><a href="{}">Repair Cyrillic</a>').format(
                value, _url("/-/corpus-edit", kind="source", **identity)
            )
            if row["source"] in ("contextual", "contextual_homonym", "stale_contextual"):
                link += Markup('<br><a href="{}">Contextual correction</a>').format(
                    _url("/-/corpus-edit", kind="contextual", **identity)
                )
            return link
        if column == "sentence" and "start_offset" in row.keys():
            start, end = row["start_offset"], row["end_offset"]
            return Markup("{}<mark>{}</mark>{}").format(value[:start], value[start:end], value[end:])
        return None

    @hookimpl
    def register_routes(self, datasette):
        if datasette is self.datasette:
            return [(r"^/-/corpus-edit$", self.editor)]

    async def editor(self, request, datasette):
        values = dict(request.args)
        preview = None
        result = None
        error = None
        status = 200
        try:
            if request.method == "POST":
                values = await request.post_vars()
                if values.get("operation") == "apply":
                    approved = json.loads(values["approved"])
                    result = await asyncio.to_thread(correct, self.path, approved["request"], approved=approved)
                else:
                    changes = _form_changes(values)
                    preview = await asyncio.to_thread(correct, self.path, changes)
                    if values.get("revision") and preview["revision"] != int(values["revision"]):
                        raise CorpusAuditError("The form is stale. Review the latest values and try again.")
            elif request.method != "GET":
                return Response.text("Method not allowed", status=405)
        except (ValueError, sqlite3.Error, OSError, KeyError, TypeError) as exc:
            preview = None
            error = str(exc)
            status = 400
        details = dict(search_url=_search_url(), kind=values.get("kind", "bulk"))
        if not result and not preview:
            try:
                details = await asyncio.to_thread(_details, self.path, values)
            except (ValueError, sqlite3.Error) as exc:
                error = str(exc)
                status = 400
        approval = {key: preview[key] for key in ("database", "revision", "request", "confirmation")} if preview else None
        return Response.html(await datasette.render_template(
            "corpus_edit.html",
            dict(details=details, preview=preview, approval=approval, result=result, error=error, values=values),
            request=request,
        ), status=status)


def create_app(path: Path) -> Datasette:
    path = path.resolve()
    with closing(connect(path)) as connection:
        state(connection)
    metadata = {
        "title": "Zamanalif corpus audit",
        "databases": {"corpus": {"queries": {
            "search": {"title": "Corpus search", "hide_sql": True, "sql": SEARCH_SQL},
            "occurrences": {"title": "Corpus occurrences", "hide_sql": True, "sql": OCCURRENCES_SQL},
        }}},
    }
    datasette = Datasette(metadata=metadata, cache_headers=False,
                          template_dir=str(Path(__file__).with_name("corpus_templates")))
    datasette.add_database(Database(datasette, path=str(path)), name="corpus")
    pm.register(CorpusPlugin(datasette, path), name=f"corpus-audit-{id(datasette)}")
    return datasette


def serve(path: Path, port: int):
    import uvicorn

    datasette = create_app(path)
    print(f"Corpus audit at http://127.0.0.1:{port}{_search_url()}", flush=True)
    uvicorn.run(datasette.app(), host="127.0.0.1", port=port)
