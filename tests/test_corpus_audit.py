from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.contextual_review import ensure_contextual_review_schema
from tatar_preannotator.corpus_corrections import correct
from tatar_preannotator.corpus_index import CorpusAuditError, connect, occurrences, reindex, search
from tatar_preannotator.training_export import export_training_dataset
from tatar_preannotator.word_export import APERTIUM_TAT_REVISION, save_reviewed_word

from test_training_export import _write_db


def token(text, label="N", **extra):
    return dict(text=text, label=label, **extra)


class CorpusAuditTests(unittest.TestCase):
    def test_model_identifier_case_matches_export_and_preserves_source(self):
        db = _write_db(self.root / "model.sqlite", [{
            "id": "model", "text": "(Ц20)", "tokens": [token("Ц20", "U")],
        }])
        reindex(db, [])
        with closing(connect(db)) as connection:
            row = search(connection, "cyrillic", "exact", "ц")[0]
            self.assertEqual(search(connection, "zamanalif", "exact", "Ts20")[0]["form_id"], row["form_id"])
            hit = occurrences(connection, row["form_id"])[0]
            self.assertEqual((hit["surface"], hit["zamanalif"]), ("Ц20", "Ts20"))
        export_training_dataset(db, self.root / "model.jsonl")
        row = json.loads((self.root / "model.jsonl").read_text())
        self.assertEqual((row["cyrillic"], row["zamanalif"]), ("(Ц20)", "(Ts20)"))

    def test_celsius_projection_matches_export_and_retains_original_surfaces(self):
        db = _write_db(self.root / "celsius.sqlite", [{
            "id": "celsius", "text": "37 °С; 140°С; С.", "tokens": [
                token("С", "U"), token("140°С", "U"), token("С", "U"),
            ],
        }])
        reindex(db, [])
        with closing(connect(db)) as connection:
            rows = search(connection, "cyrillic", "exact", "с")
            hits = [hit for row in rows for hit in occurrences(connection, row["form_id"])]
            self.assertEqual(
                sorted((hit["token_index"], hit["surface"], hit["zamanalif"]) for hit in hits),
                [(0, "С", "C"), (1, "140°С", "140°C"), (2, "С", "S")],
            )
        export_training_dataset(db, self.root / "celsius.jsonl")
        row = json.loads((self.root / "celsius.jsonl").read_text())
        self.assertEqual((row["cyrillic"], row["zamanalif"]), ("37 °С; 140°С; С.", "37 °C; 140°C; S."))

    def test_roman_projection_matches_export_and_retains_original_surfaces(self):
        db = _write_db(self.root / "romans.sqlite", [{
            "id": "roman", "text": "ХІХ, ІІ; І!", "tokens": [
                token("ХІХ", "U"), token("ІІ", "U"), token("І", "U"),
            ],
        }])
        reindex(db, [])
        with closing(connect(db)) as connection:
            for source, target in (("ХІХ", "XIX"), ("ІІ", "II"), ("І", "I")):
                row = search(connection, "cyrillic", "exact", source)[0]
                self.assertEqual(search(connection, "zamanalif", "exact", target)[0]["form_id"], row["form_id"])
                hit = occurrences(connection, row["form_id"])[0]
                self.assertEqual((hit["surface"], hit["zamanalif"]), (source, target))
        export_training_dataset(db, self.root / "roman.jsonl")
        row = json.loads((self.root / "roman.jsonl").read_text())
        self.assertEqual((row["cyrillic"], row["zamanalif"]), ("ХІХ, ІІ; І!", "XIX, II; I!"))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = _write_db(self.root / "corpus.sqlite", [
            dict(id="repair", text="Гаее, гаее идарә!", tokens=[token("Гаее"), token("гаее"), token("идарә")]),
            dict(id="merge", text="Га ебе, риза.", tokens=[token("Га"), token("ебе"), token("риза", homonym=True)]),
            dict(id="split", text="Гаебе, риза.", tokens=[token("Гаебе"), token("риза", homonym=True)]),
        ])
        for word, value in (
            ("гаее", "ğaye"), ("гаебе", "ğayebe"), ("га", "ğa"), ("ебе", "yebe"), ("идарә", "idarä"),
        ):
            save_reviewed_word(self.db, word, value, "N")
        with closing(sqlite3.connect(self.db)) as connection, connection:
            ensure_contextual_review_schema(connection)
            connection.executemany("insert into contextual_reviews values (?, ?, 'риза', 'riza', 'RL', 'now')",
                                   [("merge", 2), ("split", 1)])
            connection.execute("insert into reviewed_word_variants values ('идарә', 0, 'ıdarä', '[{}]', 'now')")
        reindex(self.db, [])

    def test_literal_search_case_and_context(self):
        with closing(connect(self.db)) as connection:
            rows = search(connection, "cyrillic", "contains", "ГА")
            group = next(row for row in rows if row["cyrillic"] == "гаее")
            self.assertEqual(group["occurrences"], 2)
            hits = occurrences(connection, group["form_id"])
            self.assertEqual([hit["zamanalif"] for hit in hits], ["Ğaye", "ğaye"])
            self.assertEqual([hit["token_index"] for hit in hits], [0, 1])
            self.assertEqual(search(connection, "zamanalif", "exact", "Idarä")[0]["cyrillic"], "идарә")
            self.assertEqual(search(connection, "zamanalif", "exact", "İdarä"), [])
            self.assertEqual(search(connection, "cyrillic", "contains", 'га"'), [])

    def test_preview_apply_split_merge_and_single_token_repairs(self):
        batch = [
            dict(kind="source", sample_id="repair", start=0, value="Гаебе"),
            dict(kind="source", sample_id="repair", start=1, value="гаебе"),
            dict(kind="source", sample_id="merge", start=0, end=2, value="Гаебе", tokens=[token("Гаебе")]),
            dict(kind="source", sample_id="split", start=0, value="Га ебе", tokens=[token("Га"), token("ебе")]),
        ]
        preview = correct(self.db, batch)
        self.assertEqual((preview["occurrences"], preview["sentences"]), (5, 3))
        result = correct(self.db, batch, approved=preview)
        with closing(sqlite3.connect(result["backup"])) as backup:
            self.assertEqual(backup.execute("select text from samples where id = 'repair'").fetchone()[0],
                             "Гаее, гаее идарә!")
        with closing(connect(self.db)) as connection:
            entries = connection.execute("select sample_id, token_index from contextual_reviews order by sample_id").fetchall()
            self.assertEqual([tuple(row) for row in entries], [("merge", 1), ("split", 2)])
            self.assertEqual(search(connection, "cyrillic", "exact", "гаее"), [])
            self.assertEqual(connection.execute("pragma foreign_key_check").fetchall(), [])
        output = self.root / "training.jsonl"
        summary = export_training_dataset(self.db, output)
        self.assertEqual(summary.exported_count, 3)
        self.assertIn("Ğayebe, riza.", output.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(CorpusAuditError, "stale"):
            correct(self.db, batch, approved=preview)

    def test_bulk_and_policy_variant_preserve_mapping_and_update_index(self):
        batch = [
            dict(kind="bulk", field="zamanalif", mode="exact", query="ğaye", target="dictionary",
                 find="aye", replace="äye"),
            dict(kind="dictionary", word="идарә", variant=0, value="idärä"),
        ]
        preview = correct(self.db, batch)
        self.assertEqual(preview["words"], 2)
        correct(self.db, batch, approved=preview)
        with closing(connect(self.db)) as connection:
            self.assertEqual(search(connection, "zamanalif", "exact", "ğäye")[0]["occurrences"], 2)
            self.assertEqual(search(connection, "zamanalif", "exact", "idärä")[0]["cyrillic"], "идарә")
            mapping = connection.execute("select policies_json from reviewed_word_variants").fetchone()[0]
            self.assertEqual(json.loads(mapping), [{}])

    def test_bulk_source_replacement_uses_literal_cyrillic(self):
        batch = [dict(kind="bulk", field="cyrillic", mode="exact", query="гаее", target="source",
                      find="ее", replace="ебе")]
        preview = correct(self.db, batch)
        self.assertEqual(preview["occurrences"], 2)
        self.assertEqual(preview["changes"][0]["after"]["text"], "Гаебе, гаебе идарә!")
        correct(self.db, batch, approved=preview)

    def test_donor_correction_refreshes_family_and_compound_reviews(self):
        donor, child, compound = "вәкиленең", "вәкилдән", "вәкилдән-га"
        save_reviewed_word(self.db, donor, "wäkileneñ", "N")
        save_reviewed_word(self.db, child, "wäkildän", "N")
        save_reviewed_word(self.db, compound, "wäkildän-ğa", "U")

        def signature(word, value):
            return dict(normalized_word=word, zamanalif_dsl=value, origin="N", variants=[])

        derivation = [donor, "вәкил", "n", APERTIUM_TAT_REVISION]
        parts = [
            dict(word=child, status="inherited_review", dsl="wäkildän", origin="N", derivation=derivation,
                 component_review=signature(child, "wäkildän"), source_reviews=[
                     dict(word=child, signature=signature(child, "wäkildän")),
                     dict(word=donor, signature=signature(donor, "wäkileneñ")),
                 ]),
            dict(word="га", status="direct_review", dsl="ğa", origin="N",
                 component_review=signature("га", "ğa"),
                 source_reviews=[dict(word="га", signature=signature("га", "ğa"))]),
        ]
        with closing(connect(self.db)) as connection, connection:
            connection.execute("insert into reviewed_word_derivations values (?, ?, ?, ?, ?, 'now')",
                               (child, *derivation))
            connection.execute("insert into reviewed_word_compound_derivations values (?, ?, ?, 'now')",
                               (compound, json.dumps(parts), APERTIUM_TAT_REVISION))
            connection.execute("insert into samples values ('derived', null, ?)", (compound,))
            connection.execute("""
                insert into preannotation_state(sample_id, status, tatar, tokens_json, updated_at)
                values ('derived', 'annotated', 1, ?, 'now')
            """, (json.dumps([token(compound)]),))
        reindex(self.db, [])
        batch = [dict(kind="dictionary", word=donor, value="väkileneñ")]
        preview = correct(self.db, batch)
        self.assertEqual({item["kind"] for item in preview["changes"]},
                         {"dictionary", "family", "compound", "compound evidence"})
        correct(self.db, batch, approved=preview)
        with closing(connect(self.db)) as connection:
            row = search(connection, "cyrillic", "exact", compound)[0]
            self.assertEqual(row["zamanalif"], "väkildän-ğa")

    def test_failed_batch_and_external_changes(self):
        batch = [
            dict(kind="dictionary", word="гаее", value="ğäye"),
            dict(kind="source", sample_id="repair", start=0, value="Мәгълүмат"),
        ]
        with self.assertRaisesRegex(CorpusAuditError, "unresolved"):
            correct(self.db, batch)
        with closing(connect(self.db)) as connection, connection:
            self.assertEqual(search(connection, "zamanalif", "exact", "ğaye")[0]["occurrences"], 2)
            connection.execute("update samples set text = text where id = 'repair'")
            with self.assertRaisesRegex(CorpusAuditError, "reindex"):
                search(connection, "cyrillic", "contains", "га")

    def test_new_dictionary_approval_supplies_repair_conversion(self):
        batch = [
            dict(kind="source", sample_id="repair", start=0, value="Мәгълүмат"),
            dict(kind="dictionary", word="мәгълүмат", origin="N", value="mäğlümat"),
        ]
        preview = correct(self.db, batch)
        correct(self.db, batch, approved=preview)
        with closing(connect(self.db)) as connection:
            row = search(connection, "cyrillic", "exact", "мәгълүмат")[0]
            self.assertEqual(row["zamanalif"], "mäğlümat")
