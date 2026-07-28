from __future__ import annotations

import unittest

from tatar_preannotator.morphology import MorphIdentity, _unambiguous_identity


class MorphologyParserTests(unittest.TestCase):
    def test_parses_one_lemma_and_part_of_speech(self) -> None:
        self.assertEqual(
            _unambiguous_identity("^барабыз/бар<v><iv><pres><p1><pl>$"),
            MorphIdentity("бар", "v"),
        )

    def test_accepts_multiple_readings_with_same_identity(self) -> None:
        self.assertEqual(
            _unambiguous_identity(
                "^алма/алма<n><nom>/алма<n><acc>$"
            ),
            MorphIdentity("алма", "n"),
        )

    def test_rejects_competing_identities_unknowns_and_error_forms(self) -> None:
        self.assertIsNone(
            _unambiguous_identity("^алдым/ал<n><p1>/алд<n><px1sg>$")
        )
        self.assertIsNone(_unambiguous_identity("^билмим/*билмим$"))
        self.assertIsNone(
            _unambiguous_identity("^хаталы/хата<n><err_orth>$")
        )
