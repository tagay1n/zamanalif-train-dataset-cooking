from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tatar_preannotator.antat_reference import (
    SOURCE_CYRILLIC,
    SOURCE_ZAMANALIF,
    align_entries,
    align_page,
    download_antat_reference,
    ensure_schema,
    list_listing_entries,
    parse_listing_page,
    plain_text_from_html,
    save_entry_page,
    save_listing_entries,
)
from tatar_preannotator.cli import main
from zamanalif_selector.progress import NullProgress


LISTING_HTML = """
<TABLE>
<tr><td><FONT SIZE='+1'><b>Эзләү нәтиҗәләре</font></strong>&nbsp;&nbsp;<a href="words.php?sort=0">&#9650</a></b></td><td><b>ИT 2018</b></td></tr>
<tr><td>ABANDON </td><td><a href='text.php?id=278928&sourname=29&kind=1#actual'>🔎</a></td></tr>
<tr><td>ABASH </td><td><a href='text.php?id=278929&sourname=29&kind=1#actual'>🔎</a></td></tr>
</TABLE>
"""

ENTRY_HTML = """
<html>
<head><script>noise()</script></head>
<body>
<table><tr><td><b>abandon</b> <i>v</i> 1) ташлап китәргә; 2) баш тартырга</td></tr></table>
<table><tr><td><a>Алдагы мәкалә</a></td></tr></table>
</body>
</html>
"""


class AntatReferenceTests(unittest.TestCase):
    def test_parse_listing_page(self) -> None:
        entries = parse_listing_page(LISTING_HTML, source_id=SOURCE_CYRILLIC, page=1)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].headword, "ABANDON")
        self.assertEqual(entries[0].entry_id, "278928")
        self.assertEqual(entries[0].source_id, SOURCE_CYRILLIC)
        self.assertEqual(entries[0].page, 1)
        self.assertEqual(entries[0].position, 1)
        self.assertIn("sourname=29", entries[0].entry_url)

    def test_plain_text_from_html_strips_tags_and_scripts(self) -> None:
        text = plain_text_from_html(ENTRY_HTML)

        self.assertIn("abandon", text)
        self.assertIn("ташлап китәргә", text)
        self.assertNotIn("noise", text)
        self.assertNotIn("<script", text)

    def test_schema_save_and_resume_helpers(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            ensure_schema(conn)
            entries = parse_listing_page(LISTING_HTML, source_id=SOURCE_CYRILLIC, page=1)
            save_listing_entries(conn, entries)
            save_listing_entries(conn, entries)

            rows = list_listing_entries(conn)

        self.assertEqual(len(rows), 2)

    def test_align_page_records_aligned_and_missing_side_statuses(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            ensure_schema(conn)
            cyr = parse_listing_page(LISTING_HTML, source_id=SOURCE_CYRILLIC, page=1)
            zam_html = LISTING_HTML.replace("sourname=29", "sourname=30").replace(
                "278928", "288608"
            ).replace("278929", "288609").replace("ABASH", "DIFFERENT")
            zam = parse_listing_page(zam_html, source_id=SOURCE_ZAMANALIF, page=1)
            save_listing_entries(conn, cyr)
            save_listing_entries(conn, zam)
            save_entry_page(conn, cyr[0], ENTRY_HTML, "abandon ташлап китәргә")
            save_entry_page(conn, zam[0], ENTRY_HTML, "abandon taşlap kitärğä")

            align_page(conn, 1)

            statuses = [
                row["status"]
                for row in conn.execute(
                    "select status from antat_aligned_entries order by position"
                )
            ]

        self.assertEqual(statuses, ["aligned", "missing_side", "missing_side"])

    def test_align_entries_recovers_after_shifted_source_page(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            ensure_schema(conn)
            cyr_html = """
            <table>
            <tr><td>ATTIC</td><td><a href='text.php?id=1&sourname=29&kind=1'>x</a></td></tr>
            <tr><td>ATTIRE</td><td><a href='text.php?id=2&sourname=29&kind=1'>x</a></td></tr>
            <tr><td>ATTITUDE</td><td><a href='text.php?id=3&sourname=29&kind=1'>x</a></td></tr>
            </table>
            """
            zam_html = """
            <table>
            <tr><td>ATTIC I</td><td><a href='text.php?id=11&sourname=30&kind=1'>x</a></td></tr>
            <tr><td>ATTIC II</td><td><a href='text.php?id=12&sourname=30&kind=1'>x</a></td></tr>
            <tr><td>ATTIRE</td><td><a href='text.php?id=13&sourname=30&kind=1'>x</a></td></tr>
            <tr><td>ATTITUDE</td><td><a href='text.php?id=14&sourname=30&kind=1'>x</a></td></tr>
            </table>
            """
            cyr = parse_listing_page(cyr_html, source_id=SOURCE_CYRILLIC, page=30)
            zam = parse_listing_page(zam_html, source_id=SOURCE_ZAMANALIF, page=30)
            save_listing_entries(conn, cyr)
            save_listing_entries(conn, zam)
            for entry in cyr + zam:
                save_entry_page(conn, entry, ENTRY_HTML, f"text {entry.headword}")

            align_entries(conn)

            rows = conn.execute(
                """
                select headword, cyrillic_entry_id, zamanalif_entry_id, status
                from antat_aligned_entries
                order by headword
                """
            ).fetchall()

        by_headword = {row["headword"]: row for row in rows}
        self.assertEqual(by_headword["ATTIRE"]["status"], "aligned")
        self.assertEqual(by_headword["ATTIRE"]["cyrillic_entry_id"], "2")
        self.assertEqual(by_headword["ATTIRE"]["zamanalif_entry_id"], "13")
        self.assertEqual(by_headword["ATTITUDE"]["status"], "aligned")
        self.assertEqual(by_headword["ATTIC"]["status"], "missing_side")

    def test_download_antat_reference_uses_mock_fetcher_and_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "antat.sqlite"
            log_messages: list[str] = []

            def fetch(url: str) -> str:
                if "words.php" in url and "source%5B%5D=29" in url:
                    return LISTING_HTML
                if "words.php" in url and "source%5B%5D=30" in url:
                    return LISTING_HTML.replace("sourname=29", "sourname=30").replace(
                        "278928", "288608"
                    ).replace("278929", "288609")
                return ENTRY_HTML

            with patch("tatar_preannotator.antat_reference.EXPECTED_PAGE_COUNT", 1), patch(
                "tatar_preannotator.antat_reference.REQUEST_DELAY_SECONDS", 0
            ):
                summary = download_antat_reference(
                    db_path,
                    resume=False,
                    force=False,
                    progress=NullProgress(),
                    fetch_text=fetch,
                    log=log_messages.append,
                )

            with sqlite3.connect(db_path) as conn:
                listing_count = conn.execute(
                    "select count(*) from antat_listing_entries"
                ).fetchone()[0]
                aligned_count = conn.execute(
                    "select count(*) from antat_aligned_entries where status='aligned'"
                ).fetchone()[0]

        self.assertEqual(summary.listing_rows, 4)
        self.assertEqual(listing_count, 4)
        self.assertEqual(aligned_count, 2)
        self.assertTrue(any(message.startswith("fetch listing url:") for message in log_messages))
        self.assertTrue(any(message.startswith("fetch entry url:") for message in log_messages))

    def test_download_refuses_existing_antat_rows_without_resume_or_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "zamanalif.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                ensure_schema(conn)
                save_listing_entries(
                    conn,
                    parse_listing_page(LISTING_HTML, source_id=SOURCE_CYRILLIC, page=1),
                )

            with self.assertRaisesRegex(ValueError, "--resume or --force"):
                download_antat_reference(
                    db_path,
                    resume=False,
                    force=False,
                    progress=NullProgress(),
                    fetch_text=lambda url: "",
                )

    def test_cli_help_includes_antat_download_command(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(output):
            main(["download-antat-reference", "--help"])

        help_text = output.getvalue()
        self.assertIn("--db", help_text)
        self.assertIn("data/zamanalif.sqlite", help_text)
        self.assertIn("--resume", help_text)
        self.assertIn("--force", help_text)


if __name__ == "__main__":
    unittest.main()
