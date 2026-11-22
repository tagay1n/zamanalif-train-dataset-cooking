"""Search projection of the annotated corpus under one training policy."""
from __future__ import annotations

from contextlib import closing
from functools import lru_cache
import json
from pathlib import Path
import sqlite3
import unicodedata

from .conflict_resolver import ensure_word_resolution_schema
from .contextual_review import ContextualReview, ContextualReviewError, OccurrenceKey, _token_spans, ensure_contextual_review_schema
from .training_export import TokenConversion, TrainingExportError, parse_policy_overrides, resolve_training_token
from .literal_projection import literal_source_view
from .word_export import ensure_review_state_schema, load_reviewed_words, normalize_word


class CorpusAuditError(ValueError):
    pass


SEARCH_SQL = """
with input as (
    select audit_fold(:field, :query) as term
    where :field in ('cyrillic', 'zamanalif') and length(:query) > 0
), candidates as (
    select f.id from audit_forms f, input
    where :mode = 'exact' and :field = 'cyrillic' and f.cyr_key = term
    union all
    select f.id from audit_forms f, input
    where :mode = 'exact' and :field = 'zamanalif' and f.lat_key = term
    union all
    select f.id from audit_forms f, input
    where :mode = 'contains' and length(term) < 3
      and instr(case :field when 'cyrillic' then f.cyr_key else f.lat_key end, term) > 0
    union all
    select audit_fts.rowid from audit_fts, input
    where :mode = 'contains' and length(term) >= 3 and audit_fts match
      (case :field when 'cyrillic' then 'cyr_key' else 'lat_key' end)
      || ' : "' || replace(term, '"', '""') || '"'
)
select f.id as form_id, f.cyrillic, f.zamanalif, f.origin, f.source,
       (select count(*) from audit_occurrences o where o.form_id = f.id) as occurrences
from candidates c join audit_forms f on f.id = c.id
where exists (select 1 from audit_state where revision = index_revision)
  and f.id > cast(coalesce(nullif(:after, ''), '0') as integer)
order by f.id limit coalesce(cast(nullif(:limit, '') as integer), 100)
"""

OCCURRENCES_SQL = """
select o.sample_id, o.token_index, o.surface, o.start_offset, o.end_offset,
       f.cyrillic, o.rendered as zamanalif, f.origin, f.source, s.text as sentence
from audit_occurrences o join audit_forms f on f.id = o.form_id
join samples s on s.id = o.sample_id
where o.form_id = :form_id
  and exists (select 1 from audit_state where revision = index_revision)
  and (o.sample_id, o.token_index) > (:after_sample, cast(coalesce(nullif(:after_token, ''), '-1') as integer))
order by o.sample_id, o.token_index limit coalesce(cast(nullif(:limit, '') as integer), 100)
"""

TABLES = (
    "samples", "preannotation_state", "reviewed_words", "reviewed_word_variants",
    "reviewed_word_derivations", "reviewed_word_compound_derivations",
    "contextual_reviews", "word_resolutions",
)


def fold(field: str, value: str) -> str:
    value = unicodedata.normalize("NFC", value or "")
    if field == "zamanalif":
        value = value.translate(str.maketrans({"I": "ı", "İ": "i"}))
    return value.casefold()


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys = on")
    connection.create_function("audit_fold", 2, fold, deterministic=True)
    return connection


def state(connection: sqlite3.Connection) -> sqlite3.Row:
    try:
        row = connection.execute("select * from audit_state where id = 1").fetchone()
    except sqlite3.OperationalError as exc:
        raise CorpusAuditError("Run corpus-audit reindex to build the search index.") from exc
    if row is None or row["revision"] != row["index_revision"]:
        raise CorpusAuditError("The corpus changed. Run corpus-audit reindex to refresh search.")
    return row


def search(connection: sqlite3.Connection, field: str, mode: str, query: str,
           *, after: int = 0, limit: int = 100) -> list[dict]:
    if field not in {"cyrillic", "zamanalif"} or mode not in {"exact", "contains"} or not query:
        raise CorpusAuditError("Choose a search field, exact or contains, and a nonempty query.")
    state(connection)
    params = dict(field=field, mode=mode, query=query, after=after, limit=limit)
    return [dict(row) for row in connection.execute(SEARCH_SQL, params)]


def occurrences(connection: sqlite3.Connection, form_id: int, *, after_sample: str = "",
                after_token: int = -1, limit: int = 100) -> list[dict]:
    state(connection)
    params = dict(form_id=form_id, after_sample=after_sample, after_token=after_token, limit=limit)
    return [dict(row) for row in connection.execute(OCCURRENCES_SQL, params)]


class CorpusResolver:
    def __init__(self, path: Path, connection: sqlite3.Connection, policy: dict):
        self.words = load_reviewed_words(path, connection=connection)
        self.context = {
            OccurrenceKey(row["sample_id"], row["token_index"]): ContextualReview(**dict(row))
            for row in connection.execute("""
                select sample_id, token_index, normalized_word, zamanalif_dsl, origin
                from contextual_reviews
            """)
        }
        self.resolutions = dict(connection.execute("select normalized_word, decision from word_resolutions"))
        self.policy = policy
        self.ordinary = lru_cache(maxsize=200_000)(self._ordinary)

    def _ordinary(self, text: str, label: str, homonym: bool, projected_text: str | None = None):
        return resolve_training_token(
            "corpus", 0, {"text": text, "label": label, "homonym": homonym},
            self.words, {}, self.policy, self.resolutions,
            projected_text=projected_text,
        )

    def token(self, sample_id: str, index: int, token: dict, *, projected_text: str | None = None):
        contextual = self.context.get(OccurrenceKey(sample_id, index))
        try:
            if contextual is not None:
                return resolve_training_token(
                    sample_id, index, token, self.words, self.context, self.policy, self.resolutions,
                    projected_text=projected_text,
                )
            return self.ordinary(token["text"], token["label"], token.get("homonym") is True, projected_text)
        except TrainingExportError:
            word = normalize_word(token["text"])
            source = "stale_contextual" if contextual and contextual.normalized_word != word else "invalid_conversion"
            return TokenConversion(word, None, token["label"], source)

    def sentence(self, sample_id: str, text: str, tokens: list[dict]):
        spans = _token_spans(sample_id, text, tokens)
        literal_view = literal_source_view(text)
        return [(start, end, self.token(sample_id, index, token, projected_text=literal_view[start:end]))
                for index, ((start, end), token) in enumerate(zip(spans, tokens))]


RECORD_SQL = """
select s.id, s.text, p.tokens_json from samples s
join preannotation_state p on p.sample_id = s.id
where p.status = 'annotated' and p.tatar = 1 and p.tokens_json is not null
"""


def records(connection: sqlite3.Connection, ids: set[str] | None = None):
    if ids is None:
        yield from connection.execute(RECORD_SQL + " order by s.id")
    else:
        ordered = sorted(ids)
        for start in range(0, len(ordered), 500):
            chunk = ordered[start:start + 500]
            placeholders = ",".join("?" for _ in chunk)
            yield from connection.execute(RECORD_SQL + f" and s.id in ({placeholders}) order by s.id", chunk)


class IndexWriter:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.forms = {}

    def form(self, word: str, rendered: str | None, origin: str, source: str) -> int:
        # Group case variants of the same rendered form. Occurrence rows keep the exact output.
        key = (word, fold("zamanalif", rendered), origin, source)
        if key not in self.forms:
            row = self.connection.execute("""
                select id from audit_forms where cyrillic = ? and lat_key = ? and origin = ? and source = ?
            """, key).fetchone()
            if row is None:
                cyr_key = fold("cyrillic", word)
                form_id = self.connection.execute("""
                    insert into audit_forms(cyrillic, zamanalif, origin, source, cyr_key, lat_key)
                    values (?, ?, ?, ?, ?, ?)
                """, (word, key[1] if rendered is not None else None, origin, source, cyr_key, key[1])).lastrowid
                self.connection.execute("insert into audit_fts(rowid, cyr_key, lat_key) values (?, ?, ?)",
                                        (form_id, cyr_key, key[1]))
            else:
                form_id = row[0]
            self.forms[key] = form_id
        return self.forms[key]

    def sentence(self, sample_id: str, tokens: list[dict], resolved: list):
        self.connection.executemany("""
            insert into audit_occurrences(sample_id, token_index, form_id, surface,
                                          start_offset, end_offset, rendered)
            values (?, ?, ?, ?, ?, ?, ?)
        """, (
            (sample_id, index, self.form(result.normalized_word, result.text, result.origin, result.source),
             token["text"], start, end, result.text)
            for index, (token, (start, end, result)) in enumerate(zip(tokens, resolved))
            if result.normalized_word
        ))


SCHEMA = (
    """create table if not exists audit_state (
       id integer primary key check(id = 1), revision integer not null,
       index_revision integer, policy_json text not null, identity text not null)""",
    """create table if not exists audit_forms (
       id integer primary key, cyrillic text not null, zamanalif text,
       origin text not null, source text not null, cyr_key text not null, lat_key text not null,
       unique(cyrillic, lat_key, origin, source))""",
    """create table if not exists audit_occurrences (
       sample_id text not null, token_index integer not null, form_id integer not null,
       surface text not null, start_offset integer not null, end_offset integer not null, rendered text,
       primary key(sample_id, token_index)) without rowid""",
    """create index if not exists audit_occurrence_form on audit_occurrences(form_id, sample_id, token_index)""",
    """create index if not exists audit_cyr_key on audit_forms(cyr_key)""",
    """create index if not exists audit_lat_key on audit_forms(lat_key)""",
    """create virtual table if not exists audit_fts using fts5(cyr_key, lat_key, tokenize='trigram case_sensitive 1')""",
    """create table if not exists audit_alignment_issues (sample_id text primary key, issue text not null)""",
    """create table if not exists audit_history (
       id integer primary key, created_at text not null, backup text not null, preview_json text not null)""",
)


def reindex(path: Path, choices: list[str]) -> dict:
    policy, _ = parse_policy_overrides(choices)
    with closing(connect(path)) as connection, connection:
        connection.execute("begin immediate")
        ensure_review_state_schema(connection)
        ensure_contextual_review_schema(connection)
        ensure_word_resolution_schema(connection)
        for statement in SCHEMA:
            connection.execute(statement)
        connection.execute("""
            insert into audit_state(id, revision, policy_json, identity)
            values (1, 0, ?, lower(hex(randomblob(16))))
            on conflict(id) do update set revision = revision + 1, policy_json = excluded.policy_json
        """, (json.dumps(policy, sort_keys=True),))
        for table in TABLES:
            for action in ("insert", "update", "delete"):
                connection.execute(f"""
                    create trigger if not exists audit_dirty_{table}_{action} after {action} on {table}
                    begin update audit_state set revision = revision + 1 where id = 1; end
                """)
        for table in ("audit_occurrences", "audit_fts", "audit_forms", "audit_alignment_issues"):
            connection.execute(f"delete from {table}")
        resolver = CorpusResolver(path, connection, policy)
        writer = IndexWriter(connection)
        sentences = 0
        for row in records(connection):
            tokens = json.loads(row["tokens_json"])
            try:
                resolved = resolver.sentence(row["id"], row["text"], tokens)
            except ContextualReviewError as exc:
                connection.execute("insert into audit_alignment_issues values (?, ?)", (row["id"], str(exc)))
                continue
            writer.sentence(row["id"], tokens, resolved)
            sentences += 1
        connection.execute("update audit_state set index_revision = revision")
        return {
            "sentences": sentences,
            "forms": connection.execute("select count(*) from audit_forms").fetchone()[0],
            "occurrences": connection.execute("select count(*) from audit_occurrences").fetchone()[0],
            "alignment_issues": connection.execute("select count(*) from audit_alignment_issues").fetchone()[0],
            "conversion_issues": dict(connection.execute("""
                select f.source, count(*)
                from audit_forms f join audit_occurrences o on o.form_id = f.id
                where f.source in ('stale_contextual', 'invalid_conversion') group by f.source
            """)),
            "policy": policy,
        }
