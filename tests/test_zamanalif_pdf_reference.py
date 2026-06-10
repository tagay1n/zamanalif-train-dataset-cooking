from __future__ import annotations

import re
import unittest

from tatar_preannotator.word_export import convert_for_annotation


CYRILLIC_RE = re.compile(r"[А-Яа-яЁёӘәӨөҮүҖҗҢңҺһ]")
ALLOWED_ZAMANALIF = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "äÄöÖüÜñÑıİğĞşŞçÇ"
    "-—'’"
)


class ZamanalifPdfReferenceTests(unittest.TestCase):
    """Reference examples from the Zamanalif law PDF.

    These tests cover cases where the current auto-suggestion converter should
    be able to produce a direct answer without relying on human annotation.
    """

    def test_native_deterministic_vowels_and_consonants(self) -> None:
        cases = [
            ("бала", "N", "bala"),
            ("балалар", "N", "balalar"),
            ("әни", "N", "äni"),
            ("болыт", "N", "bolıt"),
            ("төтен", "N", "töten"),
            ("ылыс", "N", "ılıs"),
            ("эт", "N", "et"),
            ("бабай", "N", "babay"),
            ("җир", "N", "cir"),
            ("давыл", "N", "dawıl"),
            ("шәһәр", "N", "şähär"),
            ("зәңгәр", "N", "zäñgär"),
            ("хат", "N", "xat"),
            ("зур", "N", "zur"),
        ]

        self.assert_conversions(cases)

    def test_russian_loanword_pdf_examples_that_do_not_need_context(self) -> None:
        cases = [
            ("фото", "RL", "foto"),
            ("мотор", "RL", "motor"),
            ("шофёр", "RL", "şofyor"),
            ("сыр", "RL", "sıyr"),
            ("роль", "RL", "rol'"),
            ("борщ", "RL", "borşç"),
            ("цинк", "RL", "sink"),
            ("кварц", "RL", "kvars"),
            ("позиция", "RL", "pozitsiya"),
        ]

        self.assert_conversions(cases)

    def test_final_native_u_and_u_umlaut_are_not_automatically_w(self) -> None:
        cases = [
            ("су", "N", "su"),
            ("бу", "N", "bu"),
            ("үсү", "N", "üsü"),
        ]

        self.assert_conversions(cases)

    def test_i_before_ya_economy_examples(self) -> None:
        cases = [
            ("ия", "N", "iä"),
            ("ияк", "N", "iäk"),
        ]

        self.assert_conversions(cases)

    def test_outputs_use_clean_zamanalif_unicode(self) -> None:
        cases = [
            ("ылыс", "N", "ılıs"),
            ("җир", "N", "cir"),
            ("шәһәр", "N", "şähär"),
            ("зәңгәр", "N", "zäñgär"),
            ("шофёр", "RL", "şofyor"),
            ("борщ", "RL", "borşç"),
        ]

        for source, label, expected in cases:
            with self.subTest(source=source, label=label):
                converted = convert_for_annotation(source, label)
                self.assertEqual(converted, expected)
                self.assertFalse(CYRILLIC_RE.search(converted), converted)
                self.assertTrue(set(converted) <= ALLOWED_ZAMANALIF, converted)

    @unittest.skip("TODO: PDF says Russian-loan е can be plain e in these words.")
    def test_todo_russian_loanword_e_from_pdf(self) -> None:
        self.assert_conversions(
            [
                ("электр", "RL", "elektr"),
                ("телефон", "RL", "telefon"),
                ("билет", "RL", "bilet"),
            ]
        )

    @unittest.skip("TODO: current per-letter ц rule overproduces ts in пицца.")
    def test_todo_pizza_ts_is_written_once(self) -> None:
        self.assert_conversions([("пицца", "RL", "pitsa")])

    @unittest.skip("TODO: final -әү verbal-noun pattern needs a narrower w rule.")
    def test_todo_final_aw_aw_umlaut_verbal_noun(self) -> None:
        self.assert_conversions([("сөйләү", "N", "söyläw")])

    @unittest.skip("TODO: k/q dictionary decisions should not be tested as deterministic yet.")
    def test_todo_native_k_dictionary_review_examples(self) -> None:
        self.assert_conversions([("китап", "N", "kitap")])

    def assert_conversions(self, cases: list[tuple[str, str, str]]) -> None:
        for source, label, expected in cases:
            with self.subTest(source=source, label=label):
                converted = convert_for_annotation(source, label)
                self.assertEqual(converted, expected)
                self.assertFalse(CYRILLIC_RE.search(converted), converted)
                self.assertTrue(set(converted) <= ALLOWED_ZAMANALIF, converted)


if __name__ == "__main__":
    unittest.main()
