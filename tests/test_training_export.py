from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tatar_preannotator.cli import main
from tatar_preannotator.conflict_resolver import save_word_resolution
from tatar_preannotator.contextual_review import ensure_contextual_review_schema
from tatar_preannotator.conversion import PREFERRED_POLICY
from tatar_preannotator.spelling_validation import (
    OCCURRENCE_SPELLING_EXCEPTIONS,
    WORD_SPELLING_EXCEPTIONS,
    is_approved_spelling,
    spelling_matches,
)
from tatar_preannotator.training_export import (
    TrainingExportError,
    _validate_resolved_sentence,
    export_training_dataset,
    parse_policy_overrides,
)
from tatar_preannotator.word_export import save_reviewed_word


class TrainingExportTests(unittest.TestCase):
    def test_tex_fraction_braces_are_distinct_from_unresolved_dsl(self) -> None:
        formula = r"$$ \frac{OD_{540}}{(0,01 \times \text{reaktsiyä waqıtı})} $$"
        _validate_resolved_sentence("formula", formula)
        with self.assertRaisesRegex(TrainingExportError, "unresolved DSL"):
            _validate_resolved_sentence("formula", formula + " {{E_GLIDE|unresolved}}")
        with self.assertRaisesRegex(TrainingExportError, "unresolved DSL"):
            _validate_resolved_sentence("text", "Qazan}}")

    def test_dotted_names_and_reviewed_sentences_keep_their_case(self) -> None:
        cases = (
            ("Ф.Ә.Ганиева", "ф.ә.ганиева", "f.ä.ğaniyeva", "F.Ä.Ğaniyeva"),
            ("Х.Бәди-гый", "х.бәди-гый", "x.bädi-ğıy", "X.Bädi-ğıy"),
            ("галәмин.Уәссаләтү", "галәмин.уәссаләтү", "ğalämin.uässalätü", "ğalämin.Uässalätü"),
            ("әҗмәгыйн.Бөтен", "әҗмәгыйн.бөтен", "äcmäğıyn.böten", "äcmäğıyn.Böten"),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [
                {"id": str(i), "text": source, "tokens": [{"text": source, "label": "N"}]}
                for i, (source, _, _, _) in enumerate(cases)
            ])
            for _, word, spelling, _ in cases:
                save_reviewed_word(db_path, word, spelling, "N")
            output = root / "training.jsonl"

            export_training_dataset(db_path, output)

            self.assertEqual(_read_jsonl(output), [
                {"id": str(i), "cyrillic": source, "zamanalif": target}
                for i, (source, _, _, target) in enumerate(cases)
            ])

    def test_reviewed_paper_spelling_exports_using_the_existing_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [{
                "id": "paper", "text": "Қәгазь.",
                "tokens": [{"text": "Қәгазь", "label": "N"}],
            }])
            save_reviewed_word(db_path, "кәгазь", "käğaz", "N")
            output = root / "training.jsonl"

            export_training_dataset(db_path, output)

            self.assertEqual(_read_jsonl(output), [{
                "id": "paper", "cyrillic": "Қәгазь.", "zamanalif": "Käğaz.",
            }])

    def test_reviewed_native_roots_export_with_exact_cinayat_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cases = (
                ("тоемчанлыгының", "toyımçanlığınıñ"),
                ("җинаятьләренә", "cinayätlärenä"),
            )
            db = _write_db(root / "db.sqlite", [
                {"id": source, "text": source + ".", "tokens": [{"text": source, "label": "N"}]}
                for source, _ in cases
            ])
            for source, target in cases:
                save_reviewed_word(db, source, target, "N")
            output = root / "training.jsonl"
            export_training_dataset(db, output)
            self.assertEqual(
                {(row["cyrillic"], row["zamanalif"]) for row in _read_jsonl(output)},
                {(source + ".", target + ".") for source, target in cases},
            )
            with sqlite3.connect(db) as connection:
                for source, _ in cases:
                    row = connection.execute(
                        "select s.text, p.tokens_json from samples s join preannotation_state p "
                        "on s.id=p.sample_id where s.id=?", (source,),
                    ).fetchone()
                    self.assertEqual(row[0], source + ".")
                    self.assertEqual(json.loads(row[1]), [{"text": source, "label": "N"}])
        self.assertTrue(is_approved_spelling("any", 0, "җинаятьләренә", "cinayätlärenä"))
        self.assertFalse(is_approved_spelling("any", 0, "җинаятьләренә", "cinayätlärendä"))
        self.assertFalse(is_approved_spelling("any", 0, "җинаятьләрендә", "cinayätlärendä"))

    def test_reviewed_shihapova_occurrence_retains_source_and_has_exact_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tokens = [{"text": "эш", "label": "N"} for _ in range(21)]
            tokens.append({"text": "Шићапова", "label": "N"})
            text = "эш " * 21 + "Шићапова."
            db = _write_db(root / "db.sqlite", [{
                "id": "sent_006932", "text": text, "tokens": tokens,
            }])
            with sqlite3.connect(db) as connection:
                ensure_contextual_review_schema(connection)
                connection.execute(
                    "insert into contextual_reviews values (?, ?, ?, ?, ?, ?)",
                    ("sent_006932", 21, "шићапова", "şihapova", "N", "now"),
                )
            output = root / "training.jsonl"
            export_training_dataset(db, output)
            row = _read_jsonl(output)[0]
            self.assertEqual((row["cyrillic"], row["zamanalif"]), (text, "eş " * 21 + "Şihapova."))
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute("select text from samples").fetchone()[0], text)
                self.assertEqual(json.loads(connection.execute(
                    "select tokens_json from preannotation_state"
                ).fetchone()[0]), tokens)
            for sample, index, source, target in (
                ("other", 21, "Шићапова", "Şihapova"),
                ("sent_006932", 20, "Шићапова", "Şihapova"),
                ("sent_006932", 21, "Шиһапова", "Şihapova"),
                ("sent_006932", 21, "Шићапова", "Şiapova"),
            ):
                self.assertFalse(is_approved_spelling(sample, index, source, target))

    def test_reviewed_kamaz_family_keeps_source_and_lowercase_endings(self) -> None:
        cases = [
            ("КамАЗ", "kamaz", "KamAZ"),
            ("КамАЗның", "kamaznıñ", "KamAZnıñ"),
            ("КамАЗлар", "kamazlar", "KamAZlar"),
            ("КамАЗны", "kamaznı", "KamAZnı"),
            ("КамАЗ-Капитал", "kamaz-kapital", "KamAZ-Kapital"),
            ("«КамАЗ»", "kamaz", "«KamAZ»"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db = _write_db(root / "db.sqlite", [
                {"id": source, "text": source + ".", "tokens": [{"text": source, "label": "RL"}]}
                for source, _, _ in cases
            ])
            for source, review, _ in cases:
                save_reviewed_word(db, source.strip("«»").lower(), review, "RL")
            with sqlite3.connect(db) as conn:
                before = {table: conn.execute(f"select * from {table} order by 1").fetchall()
                          for table in ("samples", "preannotation_state", "reviewed_words")}
            export_training_dataset(db, root / "train.jsonl")
            self.assertEqual(
                {row["id"]: (row["cyrillic"], row["zamanalif"]) for row in _read_jsonl(root / "train.jsonl")},
                {source: (source + ".", expected + ".") for source, _, expected in cases},
            )
            with sqlite3.connect(db) as conn:
                for table, rows in before.items():
                    self.assertEqual(conn.execute(f"select * from {table} order by 1").fetchall(), rows)

    def test_kamaz_casing_does_not_change_other_source_cases_or_letters(self) -> None:
        from tatar_preannotator.training_export import _apply_source_case

        for source, target, expected in (
            ("Камаз", "kamaz", "Kamaz"),
            ("КАМАЗ", "kamaz", "KAMAZ"),
            ("камаз", "kamaz", "kamaz"),
            ("МосКамАЗ", "moskamaz", "moskamaz"),
            ("КамАЗ", "qamaz", "qamaz"),
            ("КамАЗ", "KamAZ", "KamAZ"),
            ("КамАЗов", "kamazov", "KamAZov"),
        ):
            with self.subTest(source=source, target=target):
                self.assertEqual(_apply_source_case(source, target), expected)

    def test_uppercase_stems_keep_lowercase_suffixes_and_reviewed_letters(self) -> None:
        cases = [
            ("АКШның", "aqşnıñ", "N", "AQŞnıñ"),
            ("СССРның", "sssrnıñ", "RL", "SSSRnıñ"),
            ("КПССның", "kpssnıñ", "RL", "KPSSnıñ"),
            ("РСФСРның", "rsfsrnıñ", "RL", "RSFSRnıñ"),
            ("ВЛКСМның", "vlksmnıñ", "RL", "VLKSMnıñ"),
            ("ДНКасында", "dnkasında", "RL", "DNKasında"),
            ("МИДның", "midnıñ", "RL", "MİDnıñ"),
            ("ГКның", "ğqnıñ", "N", "ĞQnıñ"),
            ("АКШның-СССРның", "aqşnıñ-sssrnıñ", "N", "AQŞnıñ-SSSRnıñ"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db = _write_db(root / "db.sqlite", [
                {"id": source, "text": source + ".", "tokens": [{"text": source, "label": origin}]}
                for source, _, origin, _ in cases
            ])
            for source, review, origin, _ in cases:
                save_reviewed_word(db, source.lower(), review, origin)
            with sqlite3.connect(db) as conn:
                before = {table: conn.execute(f"select * from {table} order by 1").fetchall()
                          for table in ("samples", "preannotation_state", "reviewed_words")}
            export_training_dataset(db, root / "train.jsonl")
            self.assertEqual(
                {row["id"]: (row["cyrillic"], row["zamanalif"]) for row in _read_jsonl(root / "train.jsonl")},
                {source: (source + ".", expected + ".") for source, _, _, expected in cases},
            )
            with sqlite3.connect(db) as conn:
                for table, rows in before.items():
                    self.assertEqual(conn.execute(f"select * from {table} order by 1").fetchall(), rows)

    def test_uppercase_prefix_case_stays_within_the_reviewed_scope(self) -> None:
        from tatar_preannotator.training_export import _apply_source_case

        for source, target in (
            ("ИнтелСет", "intelset"), ("ЕГРЮЛның", "yegryulnıñ"),
            ("ЦКның", "tsknıñ"), ("АКШның", "abşnıñ"),
        ):
            with self.subTest(source=source):
                self.assertEqual(_apply_source_case(source, target), target)
        self.assertEqual(_apply_source_case("АКШның", "AQŞnıñ"), "AQŞnıñ")
        self.assertEqual(_apply_source_case("Акшның", "aqşnıñ"), "Aqşnıñ")
        self.assertEqual(_apply_source_case("Ц20", "ts20"), "Ts20")

    def test_reviewed_model_identifier_case_preserves_digits_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            text = "Ц20, «Ц20»; (Ц20), Ц201 — Ц-20; ц20, ЦИРК."
            words = ("Ц20", "«Ц20»", "(Ц20)", "Ц201", "Ц-20", "ц20", "ЦИРК")
            db = _write_db(root / "db.sqlite", [{
                "id": "models", "text": text,
                "tokens": [{"text": word, "label": "RL"} for word in words],
            }])
            export_training_dataset(db, root / "train.jsonl")
            self.assertEqual(_read_jsonl(root / "train.jsonl"), [{
                "id": "models", "cyrillic": text,
                "zamanalif": "Ts20, «Ts20»; (Ts20), TS201 — TS-20; ts20, TSİRK.",
            }])
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("select text from samples").fetchone()[0], text)
                self.assertEqual(
                    [token["text"] for token in json.loads(conn.execute(
                        "select tokens_json from preannotation_state"
                    ).fetchone()[0])], list(words),
                )

    def test_model_casing_gate_accepts_only_the_approved_case(self) -> None:
        from tatar_preannotator.training_export import _capitalization_matches

        self.assertTrue(_capitalization_matches("Ц20", "Ts20", "RL"))
        self.assertTrue(_capitalization_matches("«Ц20»", "«Ts20»", "RL"))
        for target in ("TS20", "ts20", "tS20"):
            with self.subTest(target=target):
                self.assertFalse(_capitalization_matches("Ц20", target, "RL"))
        self.assertTrue(_capitalization_matches("Ц201", "TS201", "RL"))
        self.assertFalse(_capitalization_matches("Ц201", "Ts201", "RL"))

    def test_celsius_export_matches_across_annotation_boundaries_and_source_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            text = "37 °С; 140°С; 37 °C. С."
            db = _write_db(root / "db.sqlite", [
                {"id": "whole", "text": text, "tokens": [
                    {"text": word, "label": "U"}
                    for word in ("°С", "140°С", "°C", "С")
                ]},
                {"id": "split", "text": text, "tokens": [
                    {"text": word, "label": "U"}
                    for word in ("С", "С", "С")
                ]},
                {"id": "gaps", "text": text, "tokens": [{"text": "С.", "label": "N"}]},
            ])
            with sqlite3.connect(db) as conn:
                before = conn.execute("select * from samples order by id").fetchall()
                before_tokens = conn.execute(
                    "select sample_id, tokens_json from preannotation_state order by sample_id"
                ).fetchall()
            summary = export_training_dataset(db, root / "train.jsonl")
            rows = _read_jsonl(root / "train.jsonl")
            self.assertEqual(summary.exported_count, 3)
            self.assertEqual({row["cyrillic"] for row in rows}, {text})
            self.assertEqual({row["zamanalif"] for row in rows}, {"37 °C; 140°C; 37 °C. S."})
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("select * from samples order by id").fetchall(), before)
                self.assertEqual(conn.execute(
                    "select sample_id, tokens_json from preannotation_state order by sample_id"
                ).fetchall(), before_tokens)

    def test_celsius_projection_uses_full_source_context_for_partial_tokens(self) -> None:
        from tatar_preannotator.training_export import _SentenceRecord, _convert_sentence

        for text in ("55°С.Ш.", "°Сүз"):
            with self.subTest(text=text):
                value = _convert_sentence(
                    _SentenceRecord("not_celsius", text, True, [{"text": "°С", "label": "N"}]),
                    {}, {}, {}, {},
                )
                self.assertEqual(value.text, text.replace("°С", "°S"))

    def test_target_validation_rejects_cyrillic_outside_the_tatar_alphabet(self) -> None:
        for char in ("Қ", "қ", "І", "і", "ғ", "ҡ", "ѵ", "ћ", "ӊ", "\u0483", "\ua640"):
            with self.subTest(char=char):
                with self.assertRaisesRegex(TrainingExportError, "unresolved Cyrillic"):
                    _validate_resolved_sentence("foreign", f"latin {char} target")

    def test_unicode_validation_retains_latin_math_and_standalone_sign_references(self) -> None:
        _validate_resolved_sentence("valid", "Ğälim, İIıi ñşçöü; α + β = Ω²; (ъ), (ь).")
        for value in ("aь", "ъa", "ь_", "ъ1", "ть"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TrainingExportError, "unresolved Cyrillic"):
                    _validate_resolved_sentence("not_reference", value)

    def test_foreign_cyrillic_rejection_preserves_existing_export_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db = _write_db(root / "db.sqlite", [{
                "id": "foreign", "text": "Қ", "tokens": [],
            }])
            output = root / "train.jsonl"
            manifest = Path(str(output) + ".manifest.json")
            output.write_text("previous dataset\n", encoding="utf-8")
            manifest.write_text("previous manifest\n", encoding="utf-8")
            with self.assertRaisesRegex(TrainingExportError, "unresolved Cyrillic"):
                export_training_dataset(db, output)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous dataset\n")
            self.assertEqual(manifest.read_text(encoding="utf-8"), "previous manifest\n")
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("select text from samples").fetchone()[0], "Қ")

    def test_isolated_i_literals_are_distinct_from_phonetic_tatar_i(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            text = "І һәм И; «ІІ1/2»лы."
            db = _write_db(root / "db.sqlite", [
                {"id": "tokens", "text": text, "tokens": [
                    {"text": "І", "label": "U"},
                    {"text": "һәм", "label": "N"},
                    {"text": "И", "label": "N"},
                    {"text": "ІІ1/2", "label": "U"},
                    {"text": "лы", "label": "N"},
                ]},
                {"id": "gaps", "text": text, "tokens": [
                    {"text": "һәм", "label": "N"},
                    {"text": "И", "label": "N"},
                    {"text": "лы", "label": "N"},
                ]},
                {"id": "attached", "text": "«ІІ1/2»лы.", "tokens": [
                    {"text": "«ІІ1/2»лы", "label": "N"},
                ]},
            ])
            summary = export_training_dataset(db, root / "train.jsonl")
            rows = {row["id"]: row for row in _read_jsonl(root / "train.jsonl")}
            self.assertEqual(summary.exported_count, 3)
            for sid in ("tokens", "gaps"):
                self.assertEqual(rows[sid]["cyrillic"], text)
                self.assertEqual(rows[sid]["zamanalif"], "I häm İ; «II1/2»lı.")
            self.assertEqual(rows["attached"]["zamanalif"], "«II1/2»lı.")

    def test_roman_literals_export_identically_across_annotation_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            text = "«ХІХ»–ХХІ; ІІ, ХХIХ!"
            db = _write_db(root / "db.sqlite", [
                {"id": "whole", "text": text, "tokens": [
                    {"text": word, "label": "U"}
                    for word in ("ХІХ", "ХХІ", "ІІ", "ХХIХ")
                ]},
                {"id": "split", "text": text, "tokens": [
                    {"text": word, "label": "U"}
                    for word in ("Х", "І", "Х", "ХХІ", "ІІ", "ХХIХ")
                ]},
                {"id": "gaps", "text": text, "tokens": []},
            ])
            with sqlite3.connect(db) as conn:
                before = conn.execute("select * from samples order by id").fetchall()
                before_tokens = conn.execute(
                    "select sample_id, tokens_json from preannotation_state order by sample_id"
                ).fetchall()
            summary = export_training_dataset(db, root / "train.jsonl")
            rows = _read_jsonl(root / "train.jsonl")
            self.assertEqual(summary.exported_count, 3)
            self.assertEqual({row["cyrillic"] for row in rows}, {text})
            self.assertEqual({row["zamanalif"] for row in rows}, {"«XIX»–XXI; II, XXIX!"})
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("select * from samples order by id").fetchall(), before)
                self.assertEqual(conn.execute(
                    "select sample_id, tokens_json from preannotation_state order by sample_id"
                ).fetchall(), before_tokens)

    def test_roman_suffix_in_reviewed_name_preserves_lexical_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db = _write_db(root / "db.sqlite", [{
                "id": "name", "text": "Горки-ІІ.",
                "tokens": [{"text": "Горки-ІІ", "label": "RL"}],
            }])
            save_reviewed_word(db, "горки", "gorki", "RL")
            export_training_dataset(db, root / "train.jsonl")
            self.assertEqual(_read_jsonl(root / "train.jsonl")[0]["zamanalif"], "Gorki-II.")

    def test_roman_projection_does_not_approve_an_unreviewed_word(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db = _write_db(root / "db.sqlite", [{
                "id": "pending", "text": "ХІХ мәгълүмат.", "tokens": [
                    {"text": "ХІХ", "label": "U"},
                    {"text": "мәгълүмат", "label": "N"},
                ],
            }])
            summary = export_training_dataset(db, root / "train.jsonl", allow_empty=True)
            self.assertEqual(summary.exported_count, 0)
            self.assertEqual(summary.skipped_count, 1)

    @patch.dict(WORD_SPELLING_EXCEPTIONS, {"сәгать": {"säğät"}})
    def test_existing_mixed_vowel_review_keeps_approved_vowels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db = _write_db(root / "db.sqlite", [{
                "id": "reviewed", "text": "Сәгать.",
                "tokens": [{"text": "Сәгать", "label": "N"}],
            }])
            save_reviewed_word(db, "сәгать", "säğät", "N")
            summary = export_training_dataset(db, root / "train.jsonl")
            self.assertEqual(summary.exported_count, 1)
            self.assertEqual(_read_jsonl(root / "train.jsonl")[0]["zamanalif"], "Säğät.")

    @patch.dict(WORD_SPELLING_EXCEPTIONS, {"идарә": {"idärä"}})
    def test_native_mixed_harmony_auto_converts_only_when_origin_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "automatic",
                        "text": "Елның тарихи ҮЗАРА.",
                        "tokens": [
                            {"text": "Елның", "label": "N"},
                            {"text": "тарихи", "label": "N"},
                            {"text": "ҮЗАРА", "label": "N"},
                        ],
                    },
                    {
                        "id": "blocked",
                        "text": "Тарихи мәгълүмат.",
                        "tokens": [
                            {"text": "Тарихи", "label": "N"},
                            {"text": "мәгълүмат", "label": "N"},
                        ],
                    },
                    {
                        "id": "reviewed",
                        "text": "Идарә.",
                        "tokens": [{"text": "Идарә", "label": "N"}],
                    },
                ],
            )
            save_reviewed_word(db_path, "идарә", "idarä", "N")
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    insert into reviewed_word_variants(
                        normalized_word, position, zamanalif, policies_json, updated_at
                    ) values ('идарә', 0, 'idärä', '[{}]', 'now')
                    """
                )
            summary = export_training_dataset(db_path, root / "train.jsonl")
            rows = _read_jsonl(root / "train.jsonl")
            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            [(row["id"], row["zamanalif"]) for row in rows],
            [("automatic", "Yılnıñ tarixi ÜZARA."), ("reviewed", "İdärä.")],
        )
        self.assertEqual(manifest["skipped_by_reason"], {"mixed_harmony_word": 1})
        self.assertEqual(summary.exported_count, 2)
        self.assertEqual(summary.skipped_count, 1)

    def test_unreviewed_origin_independent_hamza_exports_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [{"id": "hamza", "text": "Тәэсирендә мәсьәлә.", "tokens": [
                    {"text": "Тәэсирендә", "label": "N"},
                    {"text": "мәсьәлә", "label": "N"},
                ]}],
            )
            summary = export_training_dataset(db_path, root / "train.jsonl")
            self.assertEqual(summary.exported_count, 1)
            self.assertEqual(_read_jsonl(root / "train.jsonl")[0]["zamanalif"], "Täʼsirendä mäsʼälä.")

    def test_exports_reviewed_literal_cyrillic_sign_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [{"id": "signs", "text": "(ь) һәм (ъ).", "tokens": [
                    {"text": "ь", "label": "N"},
                    {"text": "һәм", "label": "N"},
                    {"text": "ъ", "label": "N"},
                ]}],
            )
            save_reviewed_word(db_path, "ь", "ь", "N")
            save_reviewed_word(db_path, "ъ", "ъ", "N")
            summary = export_training_dataset(db_path, root / "train.jsonl")
            self.assertEqual(summary.exported_count, 1)
            self.assertEqual(_read_jsonl(root / "train.jsonl")[0]["zamanalif"], "(ь) häm (ъ).")

    def test_internal_quotes_are_preserved_after_shared_word_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "clean",
                        "text": "Турындагы.",
                        "tokens": [{"text": "Турындагы", "label": "N"}],
                    },
                    {
                        "id": "curly",
                        "text": "Турында”гы.",
                        "tokens": [{"text": "Турында”гы", "label": "N"}],
                    },
                    {
                        "id": "guillemet",
                        "text": "Турында»гы.",
                        "tokens": [{"text": "Турында»гы", "label": "N"}],
                    },
                    {
                        "id": "reviewed_loanword",
                        "text": "Крайова»ның.",
                        "tokens": [{"text": "Крайова»ның", "label": "RL"}],
                    },
                ],
            )
            save_reviewed_word(db_path, "крайованың", "krayovanıñ", "RL")

            summary = export_training_dataset(db_path, root / "train.jsonl")
            rows = _read_jsonl(root / "train.jsonl")

        self.assertEqual(summary.exported_count, 4)
        self.assertEqual(
            [row["zamanalif"] for row in rows],
            ["Turındağı.", "Turında”ğı.", "Turında»ğı.", "Krayova»nıñ."],
        )

    def test_default_policy_exports_plain_jsonl_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_000001",
                        "text": "Проект һәм ШӘҺӘР.",
                        "tokens": [
                            {"text": "Проект", "label": "RL"},
                            {"text": "һәм", "label": "N"},
                            {"text": "ШӘҺӘР", "label": "N"},
                        ],
                    }
                ],
            )
            save_reviewed_word(
                db_path,
                "проект",
                "pro{{E_GLIDE|plain=e|glide=ye}}kt",
                "RL",
            )
            output = root / "train.jsonl"

            summary = export_training_dataset(db_path, output)
            rows = _read_jsonl(output)
            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            rows,
            [
                {
                    "id": "sent_000001",
                    "cyrillic": "Проект һәм ШӘҺӘР.",
                    "zamanalif": "Proyekt häm ŞÄHÄR.",
                }
            ],
        )
        self.assertNotIn("{{", rows[0]["zamanalif"])
        self.assertEqual(manifest["effective_policy"], dict(PREFERRED_POLICY))
        self.assertEqual(manifest["overrides"], {})
        self.assertEqual(manifest["counts"]["exported_sentences"], 1)

    def test_cli_choice_override_selects_plain_e(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Проект.",
                        "tokens": [{"text": "Проект", "label": "RL"}],
                    }
                ],
            )
            save_reviewed_word(
                db_path,
                "проект",
                "pro{{E_GLIDE|plain=e|glide=ye}}kt",
                "RL",
            )
            output = root / "compact.jsonl"
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "training-export",
                        "--db",
                        str(db_path),
                        "--output",
                        str(output),
                        "--choice",
                        "E_GLIDE=plain",
                    ]
                )
            row = _read_jsonl(output)[0]
            manifest = json.loads(
                Path(str(output) + ".manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(row["zamanalif"], "Proekt.")
        self.assertEqual(manifest["overrides"], {"E_GLIDE": "plain"})
        self.assertIn("training export complete", stdout.getvalue())

    @patch.dict(WORD_SPELLING_EXCEPTIONS, {"проект": {"pruyekt", "pruekt"}})
    def test_reviewed_variant_corrections_follow_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Проект.",
                        "tokens": [{"text": "Проект", "label": "RL"}],
                    }
                ],
            )
            save_reviewed_word(
                db_path,
                "проект",
                "pro{{E_GLIDE|plain=e|glide=ye}}kt",
                "RL",
            )
            with sqlite3.connect(db_path) as conn:
                conn.executemany(
                    """
                    insert into reviewed_word_variants(
                        normalized_word, position, zamanalif,
                        policies_json, updated_at
                    ) values ('проект', ?, ?, ?, 'now')
                    """,
                    [
                        (0, "pruyekt", '[{"E_GLIDE":"glide"}]'),
                        (1, "pruekt", '[{"E_GLIDE":"plain"}]'),
                    ],
                )

            export_training_dataset(db_path, root / "preferred.jsonl")
            export_training_dataset(
                db_path,
                root / "compact.jsonl",
                choice_overrides=["E_GLIDE=plain"],
            )
            preferred = _read_jsonl(root / "preferred.jsonl")
            compact = _read_jsonl(root / "compact.jsonl")

        self.assertEqual(preferred[0]["zamanalif"], "Pruyekt.")
        self.assertEqual(compact[0]["zamanalif"], "Pruekt.")

    def test_single_reviewed_variant_overrides_every_global_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Проект.",
                        "tokens": [{"text": "Проект", "label": "RL"}],
                    }
                ],
            )
            save_reviewed_word(db_path, "проект", "proyekt", "RL")
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    insert into reviewed_word_variants(
                        normalized_word, position, zamanalif,
                        policies_json, updated_at
                    ) values ('проект', 0, 'proyekt',
                              '[{"E_GLIDE":"glide"}]', 'now')
                    """
                )

            export_training_dataset(db_path, root / "preferred.jsonl")
            export_training_dataset(
                db_path,
                root / "plain.jsonl",
                choice_overrides=["E_GLIDE=plain"],
            )
            preferred = _read_jsonl(root / "preferred.jsonl")
            plain = _read_jsonl(root / "plain.jsonl")

        self.assertEqual(preferred[0]["zamanalif"], "Proyekt.")
        self.assertEqual(plain[0]["zamanalif"], "Proyekt.")

    def test_skips_unreviewed_homonym_and_mixed_harmony_sentences(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Мин вакыт беләм.",
                        "tokens": [
                            {"text": "Мин", "label": "N"},
                            {"text": "вакыт", "label": "N"},
                            {"text": "беләм", "label": "N"},
                        ],
                    },
                    {
                        "id": "sent_2",
                        "text": "Акты калды.",
                        "tokens": [
                            {"text": "Акты", "label": "RL", "homonym": True},
                            {"text": "калды", "label": "N"},
                        ],
                    },
                    {
                        "id": "sent_3",
                        "text": "Гадел килде.",
                        "tokens": [
                            {"text": "Гадел", "label": "N"},
                            {"text": "килде", "label": "N"},
                        ],
                    },
                    {
                        "id": "sent_4",
                        "text": "МИН шат.",
                        "tokens": [
                            {"text": "МИН", "label": "N"},
                            {"text": "шат", "label": "N"},
                        ],
                    },
                ],
            )
            output = root / "train.jsonl"

            summary = export_training_dataset(db_path, output)
            rows = _read_jsonl(output)
            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual([row["id"] for row in rows], ["sent_4"])
        self.assertEqual(rows[0]["zamanalif"], "MİN şat.")
        self.assertEqual(
            manifest["skipped_by_reason"],
            {
                "contextual_homonym": 1,
                "mixed_harmony_word": 1,
                "unreviewed_word": 1,
            },
        )
        self.assertEqual(summary.skipped_count, 3)

    def test_origin_independent_homonym_needs_no_contextual_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Сер.",
                        "tokens": [
                            {"text": "Сер", "label": "RL"},
                        ],
                    }
                ],
            )
            with sqlite3.connect(db_path) as conn:
                save_word_resolution(conn, "сер", "contextual_homonym")

            summary = export_training_dataset(db_path, root / "train.jsonl")
            rows = _read_jsonl(root / "train.jsonl")

        self.assertEqual(summary.exported_count, 1)
        self.assertEqual(rows[0]["zamanalif"], "Ser.")

    def test_non_tatar_sentences_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Русский текст.",
                        "tatar": False,
                        "tokens": [],
                    }
                ],
            )
            output = root / "train.jsonl"

            summary = export_training_dataset(db_path, output, allow_empty=True)
            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(output.read_text(encoding="utf-8"), "")
            self.assertEqual(manifest["counts"]["non_tatar_sentences_ignored"], 1)

    def test_origin_independent_conditional_words_need_no_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Бу юл.",
                        "tokens": [
                            {"text": "Бу", "label": "U"},
                            {"text": "юл", "label": "U"},
                        ],
                    }
                ],
            )
            output = root / "train.jsonl"

            export_training_dataset(db_path, output)
            rows = _read_jsonl(output)

        self.assertEqual(rows[0]["zamanalif"], "Bu yul.")

    def test_word_resolution_clears_bad_homonym_for_origin_independent_word(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Һәм килде.",
                        "tokens": [
                            {"text": "Һәм", "label": "RL", "homonym": True},
                            {"text": "килде", "label": "N"},
                        ],
                    }
                ],
            )
            with sqlite3.connect(db_path) as conn:
                save_word_resolution(conn, "һәм", "N")
            output = root / "train.jsonl"

            export_training_dataset(db_path, output)
            rows = _read_jsonl(output)

        self.assertEqual(rows[0]["zamanalif"], "Häm kilde.")

    def test_word_resolution_does_not_approve_origin_dependent_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Авыл килде.",
                        "tokens": [{"text": "Авыл", "label": "RL", "homonym": True}],
                    }
                ],
            )
            with sqlite3.connect(db_path) as conn:
                save_word_resolution(conn, "авыл", "N")
            output = root / "train.jsonl"

            with self.assertRaisesRegex(TrainingExportError, r"zero usable pairs \(1 awaiting review\)"):
                export_training_dataset(db_path, output)
            self.assertFalse(output.exists())
            self.assertFalse(Path(str(output) + ".manifest.json").exists())

            summary = export_training_dataset(db_path, output, allow_empty=True)
            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))
            output_text = output.read_text(encoding="utf-8")

        self.assertEqual(output_text, "")
        self.assertEqual(manifest["skipped_by_reason"], {"unreviewed_word": 1})

    def test_exact_contextual_review_releases_only_reviewed_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Акты.",
                        "tokens": [{"text": "Акты", "label": "RL", "homonym": True}],
                    },
                    {
                        "id": "sent_2",
                        "text": "Акты.",
                        "tokens": [{"text": "Акты", "label": "RL", "homonym": True}],
                    },
                ],
            )
            with sqlite3.connect(db_path) as conn:
                save_word_resolution(conn, "акты", "contextual_homonym")
                ensure_contextual_review_schema(conn)
                conn.execute(
                    """
                    insert into contextual_reviews(
                        sample_id, token_index, normalized_word,
                        zamanalif_dsl, origin, updated_at
                    ) values ('sent_1', 0, 'акты', 'aqtı', 'N', 'now')
                    """
                )

            summary = export_training_dataset(db_path, root / "train.jsonl")
            records = _read_jsonl(root / "train.jsonl")

        self.assertEqual(summary.exported_count, 1)
        self.assertEqual(summary.skipped_count, 1)
        self.assertEqual(records[0]["id"], "sent_1")
        self.assertEqual(records[0]["zamanalif"], "Aqtı.")

    def test_malformed_reviewed_dsl_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [])
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    insert into reviewed_words(normalized_word, zamanalif_dsl, origin, updated_at)
                    values ('орфография', 'x{{BROKEN}}', 'RL', 'now')
                    """
                )
            output = root / "train.jsonl"
            output.write_text("existing\n", encoding="utf-8")

            with self.assertRaisesRegex(TrainingExportError, "орфография"):
                export_training_dataset(db_path, output)

            self.assertEqual(output.read_text(encoding="utf-8"), "existing\n")
            self.assertFalse(Path(str(output) + ".manifest.json").exists())

    def test_token_alignment_error_fails_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Мин шат.",
                        "tokens": [{"text": "юк", "label": "N"}],
                    }
                ],
            )
            output = root / "train.jsonl"

            with self.assertRaisesRegex(TrainingExportError, "missing or out of order"):
                export_training_dataset(db_path, output)

        self.assertFalse(output.exists())
        self.assertFalse(Path(str(output) + ".manifest.json").exists())

    def test_quality_checks_report_added_and_dropped_words_and_invalid_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [
                {"id": "clean", "text": "Һәм — һ.б.", "tokens": [
                    {"text": "Һәм", "label": "N"}, {"text": "һ.б.", "label": "N"},
                ]},
                {"id": "extra", "text": "Мин Ш.", "tokens": [{"text": "Мин", "label": "N"}]},
                {"id": "missing", "text": "Шат.", "tokens": [{"text": "Шат", "label": "N"}]},
            ])
            save_reviewed_word(db_path, "мин", "min min", "N")
            save_reviewed_word(db_path, "шат", "-", "N")
            save_reviewed_word(db_path, "һ.б", "h.b", "N")
            output = root / "training.jsonl"
            manifest = root / "training.jsonl.manifest.json"
            output.write_text("previous dataset\n", encoding="utf-8")
            manifest.write_text("previous manifest\n", encoding="utf-8")
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(["training-export", "--db", str(db_path), "--output", str(output)])

            self.assertEqual(exit_code, 1)
            message = stdout.getvalue()
            self.assertIn("Word alignment failed for 2 sentence(s)", message)
            self.assertIn("extra: word alignment mismatch (Cyrillic=2, Zamanalif=3)", message)
            self.assertIn("missing: word alignment mismatch (Cyrillic=1, Zamanalif=0)", message)
            self.assertIn("Punctuation and whitespace failed for 2 sentence(s)", message)
            self.assertIn("extra: punctuation or whitespace mismatch", message)
            self.assertIn("missing: punctuation or whitespace mismatch", message)
            self.assertIn("Character validation failed for 2 sentence(s)", message)
            self.assertIn("Capitalization failed for 1 sentence(s)", message)
            self.assertIn("extra: unresolved Cyrillic", message)
            self.assertNotIn("clean:", message)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous dataset\n")
            self.assertEqual(manifest.read_text(encoding="utf-8"), "previous manifest\n")
            self.assertFalse(list(root.glob("*.tmp")))

    def test_added_word_cannot_offset_dropped_word_in_same_sentence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [{
                "id": "balanced", "text": "Мин шат.", "tokens": [
                    {"text": "Мин", "label": "N"}, {"text": "шат", "label": "N"},
                ],
            }])
            save_reviewed_word(db_path, "мин", "min min", "N")
            save_reviewed_word(db_path, "шат", "-", "N")
            output = root / "training.jsonl"

            with self.assertRaises(TrainingExportError) as failure:
                export_training_dataset(db_path, output)

            message = str(failure.exception)
            self.assertIn("balanced: word alignment mismatch (Cyrillic=2, Zamanalif=2)", message)
            self.assertIn("token 0 'Мин' -> 'Min min' (Cyrillic=1, Zamanalif=2)", message)
            self.assertIn("token 1 'шат' -> '-' (Cyrillic=1, Zamanalif=0)", message)
            self.assertFalse(output.exists())
            self.assertFalse((root / "training.jsonl.manifest.json").exists())

    def test_punctuation_gate_preserves_exact_whitespace_across_transliteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            text = "\t«Цирк»,\xa0  мәсьәлә!\r\n37°C — e\u0301  "
            db_path = _write_db(root / "zamanalif.sqlite", [{
                "id": "exact", "text": text, "tokens": [
                    {"text": "Цирк", "label": "N"}, {"text": "мәсьәлә", "label": "N"},
                ],
            }])
            output = root / "training.jsonl"

            export_training_dataset(db_path, output)

            self.assertEqual(_read_jsonl(output), [{
                "id": "exact", "cyrillic": text,
                "zamanalif": "\t«Tsirk»,\xa0  mäsʼälä!\r\n37°C — e\u0301  ",
            }])

    def test_punctuation_gate_accepts_omitted_soft_hyphens_inside_words(self) -> None:
        tokens = ["ка\u00adдәр\u00adге", "һәр\u00adвакыт"]
        text = f"{tokens[0]}, {tokens[1]}."
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [{
                "id": "soft-hyphens", "text": text,
                "tokens": [{"text": token, "label": "N"} for token in tokens],
            }])
            save_reviewed_word(db_path, tokens[0], "qadärge", "N")
            save_reviewed_word(db_path, tokens[1], "härwaqıt", "N")
            output = root / "training.jsonl"

            export_training_dataset(db_path, output)

            self.assertEqual(_read_jsonl(output), [{
                "id": "soft-hyphens", "cyrillic": text,
                "zamanalif": "qadärge, härwaqıt.",
            }])

    def test_punctuation_gate_rejects_lost_added_replaced_and_moved_marks(self) -> None:
        cases = {
            "lost_period": ("т.п.", None),
            "added_hyphen": ("миң", "mi-ñ"),
            "replaced_slash": ("ай/көн", "ay-kön"),
            "shifted_hyphen": ("кил-мә", "ki-lmä"),
            "shifted_space": ("мин- син", "min -sin"),
            "replaced_space": ("мин\xa0син", "min sin"),
            "lost_space": ("мин  син", "min sin"),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [
                {"id": sample_id, "text": text, "tokens": [{"text": text, "label": "N"}]}
                for sample_id, (text, _) in cases.items()
            ])
            for text, target in cases.values():
                if target is not None:
                    save_reviewed_word(db_path, text, target, "N")
            output = root / "training.jsonl"

            with self.assertRaises(TrainingExportError) as failure:
                export_training_dataset(db_path, output)

            message = str(failure.exception)
            self.assertIn(f"Punctuation and whitespace failed for {len(cases)} sentence(s)", message)
            for sample_id in cases:
                self.assertIn(f"{sample_id}: punctuation or whitespace mismatch at token 0", message)
            self.assertNotIn("Word alignment failed", message)
            self.assertFalse(output.exists())
            self.assertFalse((root / "training.jsonl.manifest.json").exists())

    def test_character_count_accepts_both_ya_lengths_and_known_exceptions(self) -> None:
        cases = [
            ("Цирк", "N", None, "Tsirk"),
            ("Як", "N", "yaq", "Yaq"),
            ("Ял", "N", "al", "Al"),
            ("Ерзя", "RL", "erz{{YA|ya=ya|ya_front=yä|a=a|ae=ä}}", "Erzä"),
            ("Игълан", "N", "iğlan", "İğlan"),
            ("Герцог", "RL", "gersog", "Gersog"),
            ("Эльмира", "RL", "elmira", "Elmira"),
            ("Актер", "RL", "aktyor", "Aktyor"),
            ("ВН-5507-МР", "RL", "vn-5507-mr", "VN-5507-MR"),
            ("Г", "N", "ğ", "Ğ"),
            ("Ю.", "RL", "y", "Y."),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [
                {"id": text, "text": text, "tokens": [{"text": text, "label": origin}]}
                for text, origin, _, _ in cases
            ])
            for text, origin, reviewed, _ in cases:
                if reviewed is not None:
                    save_reviewed_word(db_path, text.rstrip(".").lower(), reviewed, origin)
            output = root / "training.jsonl"

            export_training_dataset(db_path, output, choice_overrides=["YA=ae"])

            self.assertEqual(
                {row["id"]: row["zamanalif"] for row in _read_jsonl(output)},
                {text: expected for text, _, _, expected in cases},
            )

    def test_spelling_match_accepts_either_complete_branch_and_documented_sequences(self) -> None:
        accepted = (
            ("вакыт", "waqıt"), ("вакыт", "vakıt"), ("Ял", "Yal"), ("Ял", "Al"),
            ("пицца", "pitsa"), ("Нуриев", "Nuriev"), ("Игълан", "İğlan"),
            ("мәсьәлә", "mäsʼälä"), ("металл", "metal"), ("37°C", "37°C"),
            ("аяк-яшь", "ayaq-äş"),
        )
        rejected = (
            ("вакыт", "wakıt"), ("Китап", "Kipat"), ("Китап", "Kıtap"),
            ("яшь", "yäşa"), ("пицца", "pitstsa"), ("металл", "metall"),
        )
        for source, target in accepted:
            with self.subTest(source=source, target=target):
                self.assertTrue(spelling_matches(source, target, PREFERRED_POLICY))
        for source, target in rejected:
            with self.subTest(source=source, target=target):
                self.assertFalse(spelling_matches(source, target, PREFERRED_POLICY))
        self.assertFalse(spelling_matches("проект", "proekt", PREFERRED_POLICY))
        self.assertTrue(spelling_matches("проект", "proekt", PREFERRED_POLICY, legacy_e_glides=1))

    def test_character_gate_blocks_unlisted_spelling_and_keeps_other_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db = _write_db(root / "zamanalif.sqlite", [
                {"id": "approved", "text": "Китап.", "tokens": [{"text": "Китап", "label": "N"}]},
                {"id": "unapproved", "text": "Китап.", "tokens": [{"text": "Китап", "label": "N"}]},
                {"id": "punctuation", "text": "Мин.", "tokens": [{"text": "Мин", "label": "N"}]},
            ])
            save_reviewed_word(db, "китап", "kipat", "N")
            save_reviewed_word(db, "мин", "mi-n", "N")
            output = root / "training.jsonl"
            manifest = root / "training.jsonl.manifest.json"
            output.write_text("previous dataset", encoding="utf-8")
            manifest.write_text("previous manifest", encoding="utf-8")

            with patch.dict(OCCURRENCE_SPELLING_EXCEPTIONS, {
                ("approved", 0, "Китап"): {"kipat"},
                ("punctuation", 0, "Мин"): {"min"},
            }), self.assertRaises(TrainingExportError) as error:
                export_training_dataset(db, output)

            message = str(error.exception)
            self.assertIn("Character validation failed for 1 sentence(s)", message)
            self.assertIn("unapproved: letter correspondence mismatch at token 0 'Китап' -> 'Kipat'", message)
            self.assertIn("punctuation: punctuation or whitespace mismatch", message)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous dataset")
            self.assertEqual(manifest.read_text(encoding="utf-8"), "previous manifest")

    def test_character_count_rejects_changes_that_preserve_words_and_punctuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [
                {"id": "balanced", "text": "Мин шат.", "tokens": [
                    {"text": "Мин", "label": "N"}, {"text": "шат", "label": "N"},
                ]},
                {"id": "digits", "text": "мин144шат", "tokens": [{"text": "мин144шат", "label": "N"}]},
                {"id": "overlong", "text": "Ял", "tokens": [{"text": "Ял", "label": "N"}]},
                {"id": "short_review", "text": "Эльмира", "tokens": [{"text": "Эльмира", "label": "RL"}]},
            ])
            save_reviewed_word(db_path, "мин", "mi", "N")
            save_reviewed_word(db_path, "шат", "şaat", "N")
            save_reviewed_word(db_path, "ял", "yaal", "N")
            save_reviewed_word(db_path, "эльмира", "emira", "RL")
            output = root / "training.jsonl"

            with self.assertRaises(TrainingExportError) as failure:
                export_training_dataset(db_path, output)

            message = str(failure.exception)
            self.assertIn("Character validation failed for 4 sentence(s)", message)
            self.assertIn("'Мин' -> 'Mi' (expected 3, got 2)", message)
            self.assertIn("'мин144шат' -> 'minşat' (expected 9, got 6)", message)
            self.assertIn("'Ял' -> 'Yaal' (expected 2, 3, got 4)", message)
            self.assertIn("'Эльмира' -> 'Emira' (expected 6, 7, got 5)", message)
            self.assertNotIn("Word alignment failed", message)
            self.assertNotIn("Punctuation and whitespace failed", message)
            self.assertFalse(output.exists())
            self.assertFalse((root / "training.jsonl.manifest.json").exists())

    def test_capitalization_gate_accepts_title_all_caps_and_mixed_case(self) -> None:
        cases = [
            ("Цирк", None, "Tsirk"),
            ("ЦИРК", None, "TSİRK"),
            ("таЦа", "taTsa", "taTsa"),
            ("цяМ", "tsyaM", "tsyaM"),
            ("аЯта", "aAta", "aAta"),
            ("аЦФта", "aTSFta", "aTSFta"),
            ("Ил-ЦИРК", "il-tsirk", "İl-TSİRK"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [
                {"id": text, "text": text, "tokens": [{"text": text, "label": "N"}]}
                for text, _, _ in cases
            ])
            for text, reviewed, _ in cases:
                if reviewed is not None:
                    save_reviewed_word(db_path, text.lower(), reviewed, "N")
            output = root / "training.jsonl"

            export_training_dataset(db_path, output)

            self.assertEqual(
                {row["id"]: row["zamanalif"] for row in _read_jsonl(output)},
                {text: expected for text, _, expected in cases},
            )

    def test_capitalization_gate_rejects_lost_shifted_and_extra_capitals(self) -> None:
        cases = {
            "lost_middle": ("таЦа", None, "tatsa"),
            "shifted_middle": ("саЦа", "sAtsa", "sAtsa"),
            "overcapitalized_middle": ("баЦа", "baTSa", "baTSa"),
            "overcapitalized_title": ("Цирк", "tSirk", "TSirk"),
            "extra_capital": ("тарихи", "Tarixi", "Tarixi"),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [
                {"id": sample_id, "text": text, "tokens": [{"text": text, "label": "N"}]}
                for sample_id, (text, _, _) in cases.items()
            ])
            for text, reviewed, _ in cases.values():
                if reviewed is not None:
                    save_reviewed_word(db_path, text.lower(), reviewed, "N")
            output = root / "training.jsonl"

            with self.assertRaises(TrainingExportError) as failure:
                export_training_dataset(db_path, output)

            message = str(failure.exception)
            self.assertIn(f"Capitalization failed for {len(cases)} sentence(s)", message)
            for sample_id, (text, _, rendered) in cases.items():
                self.assertIn(
                    f"{sample_id}: capitalization mismatch at token 0 {text!r} -> {rendered!r}",
                    message,
                )
            self.assertNotIn("Character validation failed", message)
            self.assertFalse(output.exists())
            self.assertFalse((root / "training.jsonl.manifest.json").exists())

    def test_retains_exact_duplicates_and_preserves_digit_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            text = "вн-005507-мр (٢٣)."
            ids = ["first", "second", "third"]
            db_path = _write_db(root / "zamanalif.sqlite", [
                {"id": sample_id, "text": text, "tokens": [{"text": "вн-005507-мр", "label": "N"}]}
                for sample_id in ids
            ])
            save_reviewed_word(db_path, "вн-005507-мр", "wn-005507-mr", "N")
            output = root / "training.jsonl"

            summary = export_training_dataset(db_path, output)

            self.assertEqual(_read_jsonl(output), [
                {"id": sample_id, "cyrillic": text, "zamanalif": "wn-005507-mr (٢٣)."}
                for sample_id in ids
            ])
            counts = json.loads(summary.manifest_path.read_text(encoding="utf-8"))["counts"]
            self.assertEqual(counts["exported_sentences"], 3)
            self.assertEqual(counts["duplicate_pairs"], 2)

    def test_new_gates_report_failures_together_and_preserve_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [
                {"id": "changed", "text": "вн-005507-мр", "tokens": [{"text": "вн-005507-мр", "label": "N"}]},
                {"id": "moved", "text": "ми12н шат", "tokens": [
                    {"text": "ми12н", "label": "N"}, {"text": "шат", "label": "N"},
                ]},
                {"id": "blank", "text": " \t", "tokens": []},
                {"id": "punctuation_only", "text": "…", "tokens": []},
                *[
                    {"id": sample_id, "text": "Акты.", "tokens": [
                        {"text": "Акты", "label": "RL", "homonym": True},
                    ]}
                    for sample_id in ("left", "right")
                ],
            ])
            save_reviewed_word(db_path, "вн-005507-мр", "wn-005501-mr", "N")
            save_reviewed_word(db_path, "шат", "12şat", "N")
            with sqlite3.connect(db_path) as conn:
                save_word_resolution(conn, "акты", "contextual_homonym")
                ensure_contextual_review_schema(conn)
                conn.executemany(
                    """
                    insert into contextual_reviews(
                        sample_id, token_index, normalized_word, zamanalif_dsl, origin, updated_at
                    ) values (?, 0, 'акты', ?, ?, 'now')
                    """,
                    [("left", "aqtı", "N"), ("right", "aktı", "RL")],
                )
            output = root / "training.jsonl"
            manifest = root / "training.jsonl.manifest.json"
            output.write_text("previous dataset\n", encoding="utf-8")
            manifest.write_text("previous manifest\n", encoding="utf-8")

            with self.assertRaises(TrainingExportError) as failure:
                export_training_dataset(db_path, output, allow_empty=True)

            message = str(failure.exception)
            self.assertIn("Digit preservation failed for 2 sentence(s)", message)
            self.assertIn("changed: token 0: digit runs differ (Cyrillic=['005507'], Zamanalif=['005501'])", message)
            self.assertIn("moved: token 0: digit runs differ (Cyrillic=['12'], Zamanalif=[])", message)
            self.assertIn("Conflicting duplicates failed for 1 source group(s)", message)
            self.assertIn("left -> 'Aqtı.', right -> 'Aktı.'", message)
            self.assertIn("Usable output failed for 2 sentence(s)", message)
            self.assertIn("blank: each side needs a word or number", message)
            self.assertIn("punctuation_only: each side needs a word or number", message)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous dataset\n")
            self.assertEqual(manifest.read_text(encoding="utf-8"), "previous manifest\n")
            self.assertFalse(list(root.glob("*.tmp")))

    def test_cli_allows_empty_export_only_with_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(root / "zamanalif.sqlite", [])
            output = root / "training.jsonl"
            manifest = root / "training.jsonl.manifest.json"
            args = ["training-export", "--db", str(db_path), "--output", str(output)]
            stdout = StringIO()

            with redirect_stdout(stdout):
                self.assertEqual(main(args), 1)
                self.assertFalse(output.exists())
                self.assertFalse(manifest.exists())
                self.assertEqual(main([*args, "--allow-empty"]), 0)

            self.assertIn("zero usable pairs", stdout.getvalue())
            self.assertEqual(output.read_text(encoding="utf-8"), "")
            counts = json.loads(manifest.read_text(encoding="utf-8"))["counts"]
            self.assertEqual(counts["exported_sentences"], 0)

    def test_reviewed_abbreviation_restores_trailing_parenthesis_and_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "ВКП(б) тарихы.",
                        "tokens": [
                            {"text": "ВКП(б)", "label": "RL"},
                            {"text": "тарихы", "label": "N"},
                        ],
                    }
                ],
            )
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    insert into reviewed_words(
                        normalized_word, zamanalif_dsl, origin, updated_at
                    ) values ('вкп(б', 'vkp(b', 'RL', 'now')
                    """
                )
                conn.execute(
                    """
                    insert into reviewed_words(
                        normalized_word, zamanalif_dsl, origin, updated_at
                    ) values ('тарихы', 'tarixı', 'N', 'now')
                    """
                )

            export_training_dataset(db_path, root / "train.jsonl")
            records = _read_jsonl(root / "train.jsonl")

        self.assertEqual(records[0]["zamanalif"], "VKP(b) tarixı.")

    def test_omitted_cyrillic_token_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "text": "Мин шат.",
                        "tokens": [{"text": "Мин", "label": "N"}],
                    }
                ],
            )

            with self.assertRaisesRegex(TrainingExportError, "unresolved Cyrillic"):
                export_training_dataset(db_path, root / "train.jsonl")

    def test_policy_override_validation(self) -> None:
        effective, overrides = parse_policy_overrides(["E_GLIDE=plain"])
        self.assertEqual(effective, {**dict(PREFERRED_POLICY), "E_GLIDE": "plain"})
        self.assertEqual(overrides, {"E_GLIDE": "plain"})

        with self.assertRaisesRegex(TrainingExportError, "duplicate"):
            parse_policy_overrides(["E_GLIDE=plain", "E_GLIDE=glide"])
        with self.assertRaisesRegex(TrainingExportError, "unknown DSL rule"):
            parse_policy_overrides(["OTHER=value"])
        with self.assertRaisesRegex(TrainingExportError, "unknown option"):
            parse_policy_overrides(["E_GLIDE=other"])
        with self.assertRaisesRegex(TrainingExportError, "RULE=OPTION"):
            parse_policy_overrides(["E_GLIDE"])


def _write_db(path: Path, rows: list[dict]) -> Path:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            create table samples (
                id text primary key,
                source_id text,
                text text not null
            )
            """
        )
        conn.execute(
            """
            create table preannotation_state (
                sample_id text primary key references samples(id),
                status text not null,
                tatar integer,
                tokens_json text,
                attempts integer not null default 0,
                last_error text,
                updated_at text not null
            )
            """
        )
        conn.execute(
            """
            create table reviewed_words (
                normalized_word text primary key,
                zamanalif_dsl text not null,
                origin text not null check(origin in ('N', 'RL', 'U')),
                updated_at text not null
            )
            """
        )
        for row in rows:
            sample_id = row["id"]
            conn.execute(
                "insert into samples(id, source_id, text) values (?, 'src', ?)",
                (sample_id, row["text"]),
            )
            conn.execute(
                """
                insert into preannotation_state(
                    sample_id, status, tatar, tokens_json, updated_at
                ) values (?, 'annotated', ?, ?, 'now')
                """,
                (
                    sample_id,
                    1 if row.get("tatar", True) else 0,
                    json.dumps(row.get("tokens", []), ensure_ascii=False),
                ),
            )
    return path


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


if __name__ == "__main__":
    unittest.main()
