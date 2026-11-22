import unittest

from tatar_preannotator.literal_projection import literal_source_view


class LiteralProjectionTests(unittest.TestCase):
    def test_celsius_units_preserve_spacing_offsets_and_latin_sources(self):
        for source, expected in (
            ("37 °С; 140°С.", "37 °C; 140°C."),
            ("−10 ° С; 20°\u00a0С", "−10 ° C; 20°\u00a0C"),
            ("37 °C; 140°C.", "37 °C; 140°C."),
            ("ХІХ: 37 °С", "XIX: 37 °C"),
        ):
            with self.subTest(source=source):
                self.assertEqual(literal_source_view(source), expected)
                self.assertEqual(len(source), len(expected))
                self.assertEqual(literal_source_view(expected), expected)

    def test_geography_words_initials_and_other_symbols_are_unchanged(self):
        for source in (
            "55°С.Ш.", "55° С.ш.", "55°С. Ш.", "55°СШ",
            "С. Сәгать", "°Сүз", "°С_", "°С2", "°с", "CuO; H_2SO_4",
        ):
            with self.subTest(source=source):
                self.assertEqual(literal_source_view(source), source)
