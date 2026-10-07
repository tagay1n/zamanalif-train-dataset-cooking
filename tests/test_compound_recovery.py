from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.morphology import APERTIUM_TAT_REVISION
from tatar_preannotator.training_export import (
    _convert_sentence, _NotReady, _SentenceRecord, parse_policy_overrides,
)
from tatar_preannotator.word_export import (
    ReviewedWord, ensure_review_state_schema, load_reviewed_words, save_reviewed_word,
)


class CompoundRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "words.sqlite"
        save_reviewed_word(self.db, "азык", "azığ", "N")
        self.source = ReviewedWord("азык", "azığ", "N")
        self.components = [
            {"word": "азык", "status": "direct_review", "dsl": "azığ",
             "component_review": asdict(self.source),
             "source_reviews": [{"word": "азык", "signature": asdict(self.source)}]},
            {"word": "төлек", "status": "deterministic", "dsl": "tölek", "source_reviews": []},
        ]
        self.store("азык-төлек", "azığ-tölek", self.components)

    def store(self, word, dsl, components):
        with sqlite3.connect(self.db) as conn:
            ensure_review_state_schema(conn)
            conn.execute("insert into reviewed_words values (?, ?, 'U', 'recovered')", (word, dsl))
            conn.execute("insert into reviewed_word_compound_derivations values (?, ?, ?, 'recovered')",
                         (word, json.dumps(components), APERTIUM_TAT_REVISION))

    def convert(self):
        policy, _ = parse_policy_overrides(())
        record = _SentenceRecord("sample", "Азык-төлек.", True,
                                 [{"text": "Азык-төлек", "label": "N"}])
        return _convert_sentence(record, load_reviewed_words(self.db), {}, policy, {})

    def test_derived_compound_preserves_human_correction_and_source_case(self):
        self.assertEqual(self.convert(), "Azığ-tölek.")
        self.assertEqual(load_reviewed_words(self.db)["азык-төлек"].origin, "U")

    def test_changed_human_component_invalidates_dependent_compound(self):
        save_reviewed_word(self.db, "азык", "azıq", "N")
        self.assertNotIn("азык-төлек", load_reviewed_words(self.db))
        self.assertEqual(self.convert(), _NotReady("mixed_harmony_word"))

    def test_new_human_review_overrides_previously_automatic_component(self):
        save_reviewed_word(self.db, "төлек", "töläk", "N")
        self.assertNotIn("азык-төлек", load_reviewed_words(self.db))

    def test_direct_whole_word_review_overrides_derivation(self):
        save_reviewed_word(self.db, "азык", "azıq", "N")
        save_reviewed_word(self.db, "азык-төлек", "azıq-töläk", "N")
        self.assertEqual(self.convert(), "Azıq-töläk.")
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("select count(*) from reviewed_word_compound_derivations").fetchone()[0], 0)

    def test_safe_family_component_rechecks_human_prefix_edit(self):
        save_reviewed_word(self.db, "яраксыз", "yaräqsız", "N")
        source = ReviewedWord("яраксыз", "yaräqsız", "N")
        components = [
            {"word": "кирәк", "status": "deterministic", "dsl": "kiräk", "source_reviews": []},
            {"word": "яраклары", "status": "existing_family_rule", "dsl": "yaräqları",
             "source_reviews": [{"word": "яраксыз", "signature": asdict(source)}],
             "family": {"source_word": "яраксыз", "lemma": "ярак"}},
        ]
        self.store("кирәк-яраклары", "kiräk-yaräqları", components)
        self.assertIn("кирәк-яраклары", load_reviewed_words(self.db))
        components[1]["family"]["lemma"] = "яраклар"
        with sqlite3.connect(self.db) as conn:
            conn.execute("update reviewed_word_compound_derivations set components_json=? where normalized_word='кирәк-яраклары'",
                         (json.dumps(components),))
        self.assertNotIn("кирәк-яраклары", load_reviewed_words(self.db))


if __name__ == "__main__":
    unittest.main()
