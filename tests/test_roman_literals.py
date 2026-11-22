import unittest

from tatar_preannotator.roman_literals import roman_literal_view


class RomanLiteralTests(unittest.TestCase):
    def test_canonical_mixed_script_numerals_use_ascii_roman_letters(self):
        cases = {
            "ХІХ": "XIX", "ХХІ": "XXI", "ІІ": "II", "ХХIХ": "XXIX",
            "ХХІІ": "XXII", "ХІ": "XI", "ХVІІІ": "XVIII", "ІХ": "IX",
            "ХV": "XV", "ІІІ": "III", "ХХІХ": "XXIX", "ХVIII": "XVIII",
            "ХIX": "XIX", "ХVII": "XVII", "XVІІ": "XVII", "ХIII": "XIII",
            "ХХIII": "XXIII", "ХX": "XX", "ХІІ": "XII", "ХІІІ": "XIII",
            "ХХI": "XXI", "ХXII": "XXII", "ХХII": "XXII", "ІX": "IX",
            "ХIХ": "XIX",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(roman_literal_view(source), expected)

    def test_projection_preserves_punctuation_spacing_and_offsets(self):
        source = "«ХІХ»–ХХІ;  (ІІІ) / Горки-ІІ.\nХVIII"
        result = roman_literal_view(source)
        self.assertEqual(result, "«XIX»–XXI;  (III) / Горки-II.\nXVIII")
        self.assertEqual(len(result), len(source))
        self.assertEqual(roman_literal_view(result), result)

    def test_ordinary_words_initials_and_chemical_formulae_are_untouched(self):
        source = "Халык, СХІ, НСІ, М.Х., И.И., һ.б., ХХ, Х, І., СМ, ХІХләр."
        self.assertEqual(roman_literal_view(source), source)

    def test_isolated_i_and_fractional_markers_retain_formatting(self):
        source = "І төркем; І = U/R; «ІІ1/2»лы, ІІ1/2. Сузык И."
        expected = "I төркем; I = U/R; «II1/2»лы, II1/2. Сузык И."
        view = roman_literal_view(source)
        self.assertEqual(view, expected)
        self.assertEqual(len(view), len(source))
        self.assertEqual(roman_literal_view(view), view)

    def test_literal_i_does_not_change_initials_words_or_other_fractions(self):
        source = "І. Саубанова; А.І.; Іван, і, І1/2, ІІ1/2лы, ІІ1/3."
        self.assertEqual(roman_literal_view(source), source)

    def test_malformed_numerals_are_not_partially_normalized(self):
        for source in ("ІІІІ", "ХХХХI", "ХVX", "ІC", "abcХІХ", "ХІХabc"):
            with self.subTest(source=source):
                self.assertEqual(roman_literal_view(source), source)
