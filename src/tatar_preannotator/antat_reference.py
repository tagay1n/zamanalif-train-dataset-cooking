from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
import re
import sqlite3
from time import sleep
from typing import Callable, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SOURCE_CYRILLIC = 29
SOURCE_ZAMANALIF = 30
EXPECTED_PAGE_COUNT = 484
REQUEST_DELAY_SECONDS = 0.2
REQUEST_TIMEOUT_SECONDS = 30
RETRY_COUNT = 3
BASE_URL = "https://suzlek.antat.ru"


class ProgressLike(Protocol):
    def add_task(self, description: str, *, total: int | None = None, **fields: object) -> int: ...

    def advance(self, task_id: int, advance: int = 1, **fields: object) -> None: ...

    def update(self, task_id: int, **fields: object) -> None: ...


@dataclass(frozen=True)
class ListingEntry:
    source_id: int
    page: int
    position: int
    entry_id: str
    headword: str
    entry_url: str


@dataclass(frozen=True)
class DownloadSummary:
    output_path: Path
    listing_rows: int
    entry_pages: int
    skipped_entry_pages: int
    aligned_rows: int
    mismatch_rows: int
    missing_side_rows: int


FetchText = Callable[[str], str]
Log = Callable[[str], None]


def download_antat_reference(
    db_path: str | Path,
    *,
    resume: bool,
    force: bool,
    progress: ProgressLike,
    fetch_text: FetchText | None = None,
    log: Log | None = None,
) -> DownloadSummary:
    """Download Antat source 29/30 entries into the application SQLite database."""
    output = Path(db_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fetch_text = fetch_text or fetch_url_text
    log = log or (lambda message: None)

    with sqlite3.connect(output) as conn:
        conn.row_factory = sqlite3.Row
        if force:
            reset_antat_tables(conn)
        ensure_schema(conn)
        if not force and not resume and antat_has_rows(conn):
            raise ValueError("database already has Antat reference rows; use --resume or --force")
        listing_task = progress.add_task("Antat listings", total=EXPECTED_PAGE_COUNT * 2)
        for source_id in (SOURCE_CYRILLIC, SOURCE_ZAMANALIF):
            for page in range(1, EXPECTED_PAGE_COUNT + 1):
                if resume and listing_page_exists(conn, source_id, page):
                    progress.advance(
                        listing_task,
                        summary=f"source={source_id} page={page} skipped",
                    )
                    continue
                url = listing_url(source_id, page)
                log(f"fetch listing url: {url}")
                html = fetch_text(url)
                entries = parse_listing_page(html, source_id=source_id, page=page)
                save_listing_entries(conn, entries)
                progress.advance(
                    listing_task,
                    summary=f"source={source_id} page={page} rows={len(entries)}",
                )
                sleep(REQUEST_DELAY_SECONDS)

        entries = list_listing_entries(conn)
        entry_task = progress.add_task("Antat entries", total=len(entries))
        skipped = 0
        for entry in entries:
            if resume and entry_page_exists(conn, entry.source_id, entry.entry_id):
                skipped += 1
                progress.advance(
                    entry_task,
                    summary=f"source={entry.source_id} id={entry.entry_id} skipped",
                )
                continue
            log(f"fetch entry url: {entry.entry_url}")
            html = fetch_text(entry.entry_url)
            save_entry_page(conn, entry, html, plain_text_from_html(html))
            progress.advance(
                entry_task,
                summary=f"source={entry.source_id} id={entry.entry_id}",
            )
            sleep(REQUEST_DELAY_SECONDS)

        align_task = progress.add_task("Antat alignment", total=1)
        align_entries(conn)
        progress.advance(align_task, summary="complete")

        return summary(conn, output, skipped)


def fetch_url_text(url: str) -> str:
    """Fetch a UTF-8 page with fixed retry policy."""
    last_error: Exception | None = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return response.read().decode("utf-8", "replace")
        except Exception as exc:  # pragma: no cover - exercised only by real network failures
            last_error = exc
            if attempt == RETRY_COUNT:
                break
            sleep(REQUEST_DELAY_SECONDS * attempt)
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def listing_url(source_id: int, page: int) -> str:
    params = [
        ("txtW", "*"),
        ("submit", "Эзләү"),
        ("sourcesseq", "1"),
        ("txtkind", "1"),
        ("sort", "0"),
        ("lang[]", "E"),
        ("source[]", str(source_id)),
        ("page", str(page)),
    ]
    return f"{BASE_URL}/words.php?{urlencode(params)}"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists antat_listing_entries (
            source_id integer not null,
            page integer not null,
            position integer not null,
            entry_id text not null,
            headword text not null,
            entry_url text not null,
            primary key (source_id, entry_id),
            unique (source_id, page, position)
        );

        create table if not exists antat_entry_pages (
            source_id integer not null,
            entry_id text not null,
            headword text not null,
            html text not null,
            plain_text text not null,
            fetched_at text not null,
            primary key (source_id, entry_id)
        );

        create table if not exists antat_aligned_entries (
            align_id integer primary key autoincrement,
            page integer not null,
            position integer not null,
            headword text not null,
            cyrillic_entry_id text,
            zamanalif_entry_id text,
            cyrillic_text text not null,
            zamanalif_text text not null,
            status text not null
        );

        create table if not exists download_state (
            key text primary key,
            value text not null
        );
        """
    )
    conn.execute(
        """
        insert or replace into download_state(key, value)
        values ('antat_expected_page_count', ?)
        """,
        (str(EXPECTED_PAGE_COUNT),),
    )
    conn.commit()


def reset_antat_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        drop table if exists antat_aligned_entries;
        drop table if exists antat_entry_pages;
        drop table if exists antat_listing_entries;
        """
    )
    if _table_exists(conn, "download_state"):
        conn.execute("delete from download_state where key like 'antat_%'")
    conn.commit()


def _reset_aligned_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        drop table if exists antat_aligned_entries;

        create table antat_aligned_entries (
            align_id integer primary key autoincrement,
            page integer not null,
            position integer not null,
            headword text not null,
            cyrillic_entry_id text,
            zamanalif_entry_id text,
            cyrillic_text text not null,
            zamanalif_text text not null,
            status text not null
        );
        """
    )


def antat_has_rows(conn: sqlite3.Connection) -> bool:
    for table in ("antat_listing_entries", "antat_entry_pages", "antat_aligned_entries"):
        if not _table_exists(conn, table):
            continue
        if _count(conn, table) > 0:
            return True
    return False


def parse_listing_page(html: str, *, source_id: int, page: int) -> list[ListingEntry]:
    rows: list[ListingEntry] = []
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row_html, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 2:
            continue
        link = re.search(
            r"<a\b[^>]*href=['\"](?P<href>text\.php\?(?P<query>[^'\"]+))['\"]",
            cells[1],
            flags=re.IGNORECASE | re.DOTALL,
        )
        if link is None:
            continue
        query = link.group("query").replace("&amp;", "&")
        entry_id = _query_value(query, "id")
        sourname = _query_value(query, "sourname")
        kind = _query_value(query, "kind")
        if not entry_id or sourname != str(source_id) or kind != "1":
            continue
        headword = _clean_cell_text(cells[0])
        href = link.group("href").replace("&amp;", "&").split("#", 1)[0]
        rows.append(
            ListingEntry(
                source_id=source_id,
                page=page,
                position=len(rows) + 1,
                entry_id=entry_id,
                headword=headword,
                entry_url=f"{BASE_URL}/{href}",
            )
        )
    return rows


def plain_text_from_html(html: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = text.replace("\ufeff", " ")
    return re.sub(r"\s+", " ", text).strip()


def save_listing_entries(conn: sqlite3.Connection, entries: list[ListingEntry]) -> None:
    conn.executemany(
        """
        insert or ignore into antat_listing_entries(
            source_id, page, position, entry_id, headword, entry_url
        ) values (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                entry.source_id,
                entry.page,
                entry.position,
                entry.entry_id,
                entry.headword,
                entry.entry_url,
            )
            for entry in entries
        ],
    )
    conn.commit()


def save_entry_page(
    conn: sqlite3.Connection,
    entry: ListingEntry,
    html: str,
    plain_text: str,
) -> None:
    conn.execute(
        """
        insert or replace into antat_entry_pages(
            source_id, entry_id, headword, html, plain_text, fetched_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (entry.source_id, entry.entry_id, entry.headword, html, plain_text, _now()),
    )
    conn.commit()


def align_page(conn: sqlite3.Connection, page: int) -> None:
    """Align one listing page by normalized headword.

    This is mainly useful in tests. Full downloads should use ``align_entries``
    because an extra entry in one source can shift the rest of a page and spill
    into following pages.
    """
    left_rows = list(_listing_by_position(conn, SOURCE_CYRILLIC, page).values())
    right_rows = list(_listing_by_position(conn, SOURCE_ZAMANALIF, page).values())
    _reset_aligned_table(conn)
    _replace_aligned_rows(conn, _aligned_rows(left_rows, right_rows))
    conn.commit()


def align_entries(conn: sqlite3.Connection) -> None:
    """Align all downloaded Cyrillic/Zamanalif entries by normalized headword."""
    left_rows = _listing_by_source(conn, SOURCE_CYRILLIC)
    right_rows = _listing_by_source(conn, SOURCE_ZAMANALIF)
    _reset_aligned_table(conn)
    _replace_aligned_rows(conn, _aligned_rows(left_rows, right_rows))
    conn.commit()


def listing_page_exists(conn: sqlite3.Connection, source_id: int, page: int) -> bool:
    row = conn.execute(
        """
        select count(*) as count
        from antat_listing_entries
        where source_id=? and page=?
        """,
        (source_id, page),
    ).fetchone()
    return int(row["count"]) > 0


def entry_page_exists(conn: sqlite3.Connection, source_id: int, entry_id: str) -> bool:
    row = conn.execute(
        """
        select count(*) as count
        from antat_entry_pages
        where source_id=? and entry_id=?
        """,
        (source_id, entry_id),
    ).fetchone()
    return int(row["count"]) > 0


def list_listing_entries(conn: sqlite3.Connection) -> list[ListingEntry]:
    rows = conn.execute(
        """
        select source_id, page, position, entry_id, headword, entry_url
        from antat_listing_entries
        order by source_id, page, position
        """
    ).fetchall()
    return [
        ListingEntry(
            source_id=int(row["source_id"]),
            page=int(row["page"]),
            position=int(row["position"]),
            entry_id=str(row["entry_id"]),
            headword=str(row["headword"]),
            entry_url=str(row["entry_url"]),
        )
        for row in rows
    ]


def summary(conn: sqlite3.Connection, output_path: Path, skipped_entry_pages: int) -> DownloadSummary:
    listing_rows = _count(conn, "antat_listing_entries")
    entry_pages = _count(conn, "antat_entry_pages")
    aligned_rows = _count_status(conn, "aligned")
    mismatch_rows = _count_status(conn, "headword_mismatch")
    missing_side_rows = _count_status(conn, "missing_side")
    conn.execute(
        """
        insert or replace into download_state(key, value)
        values ('antat_last_completed_at', ?)
        """,
        (_now(),),
    )
    conn.commit()
    return DownloadSummary(
        output_path=output_path,
        listing_rows=listing_rows,
        entry_pages=entry_pages,
        skipped_entry_pages=skipped_entry_pages,
        aligned_rows=aligned_rows,
        mismatch_rows=mismatch_rows,
        missing_side_rows=missing_side_rows,
    )


def _query_value(query: str, key: str) -> str | None:
    for part in query.split("&"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        if name == key:
            return value.split("#", 1)[0]
    return None


def _clean_cell_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _listing_by_position(
    conn: sqlite3.Connection,
    source_id: int,
    page: int,
) -> dict[int, sqlite3.Row]:
    rows = conn.execute(
        """
        select page, position, entry_id, headword
        from antat_listing_entries
        where source_id=? and page=?
        order by position
        """,
        (source_id, page),
    ).fetchall()
    return {int(row["position"]): row for row in rows}


def _listing_by_source(conn: sqlite3.Connection, source_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        select page, position, entry_id, headword
        from antat_listing_entries
        where source_id=?
        order by page, position
        """,
        (source_id,),
    ).fetchall()


def _aligned_rows(
    left_rows: list[sqlite3.Row],
    right_rows: list[sqlite3.Row],
) -> list[tuple[int, int, str, str | None, str | None, str]]:
    left_by_key = {_normalize_headword(str(row["headword"])): row for row in left_rows}
    right_by_key = {_normalize_headword(str(row["headword"])): row for row in right_rows}
    left_counts = _headword_counts(left_rows)
    right_counts = _headword_counts(right_rows)
    rows: list[tuple[int, int, str, str | None, str | None, str]] = []
    for key in sorted(set(left_by_key) | set(right_by_key)):
        left = left_by_key.get(key)
        right = right_by_key.get(key)
        if left is None or right is None:
            status = "missing_side"
        elif left_counts[key] > 1 or right_counts[key] > 1:
            status = "headword_mismatch"
        else:
            status = "aligned"
        row = left or right
        rows.append(
            (
                int(row["page"]),
                int(row["position"]),
                str(row["headword"]),
                str(left["entry_id"]) if left is not None else None,
                str(right["entry_id"]) if right is not None else None,
                status,
            )
        )
    return rows


def _replace_aligned_rows(
    conn: sqlite3.Connection,
    rows: list[tuple[int, int, str, str | None, str | None, str]],
) -> None:
    conn.executemany(
        """
        insert or replace into antat_aligned_entries(
            page, position, headword, cyrillic_entry_id, zamanalif_entry_id,
            cyrillic_text, zamanalif_text, status
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                page,
                position,
                headword,
                cyrillic_entry_id,
                zamanalif_entry_id,
                _entry_plain_text(conn, SOURCE_CYRILLIC, cyrillic_entry_id),
                _entry_plain_text(conn, SOURCE_ZAMANALIF, zamanalif_entry_id),
                status,
            )
            for (
                page,
                position,
                headword,
                cyrillic_entry_id,
                zamanalif_entry_id,
                status,
            ) in rows
        ],
    )


def _headword_counts(rows: list[sqlite3.Row]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = _normalize_headword(str(row["headword"]))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _entry_plain_text(
    conn: sqlite3.Connection,
    source_id: int,
    entry_id: str | None,
) -> str:
    if entry_id is None:
        return ""
    row = conn.execute(
        """
        select plain_text
        from antat_entry_pages
        where source_id=? and entry_id=?
        """,
        (source_id, entry_id),
    ).fetchone()
    return str(row["plain_text"]) if row is not None else ""


def _normalize_headword(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"select count(*) as count from {table}").fetchone()
    return int(row["count"])


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        select count(*) as count
        from sqlite_master
        where type='table' and name=?
        """,
        (table_name,),
    ).fetchone()
    return int(row["count"]) > 0


def _count_status(conn: sqlite3.Connection, status: str) -> int:
    row = conn.execute(
        """
        select count(*) as count
        from antat_aligned_entries
        where status=?
        """,
        (status,),
    ).fetchone()
    return int(row["count"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
