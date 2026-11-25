import unittest

from tatar_preannotator.literal_projection import literal_source_view


class LiteralProjectionTests(unittest.TestCase):
    def test_reviewed_paper_spelling_projects_only_the_exact_token(self):
        source = "«Қәгазь», Қәгазьләр, Қыелышта, аҚәгазь."
        expected = "«Кәгазь», Қәгазьләр, Қыелышта, аҚәгазь."
        self.assertEqual(literal_source_view(source), expected)
        self.assertEqual(len(source), len(expected))

    def test_reviewed_conjunction_is_exact_and_preserves_offsets(self):
        source = "Миләүшә ћәм Рамиль; «ћәм». Шићапова, ћәмнең, аћәм, ћәм_2, Ћәм."
        expected = "Миләүшә hәм Рамиль; «hәм». Шићапова, ћәмнең, аћәм, ћәм_2, Ћәм."
        self.assertEqual(literal_source_view(source), expected)
        self.assertEqual(len(source), len(expected))
        self.assertEqual(literal_source_view(expected), expected)

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

    def test_metre_projection_applies_to_undotted_units_after_numbers(self):
        for source, expected in (
            ("550 м; 0,3\tм; (50 м)", "550 m; 0,3\tm; (50 m)"),
            ("100 м.оз.; 5 мм; м хәрефе", "100 м.оз.; 5 мм; м хәрефе"),
        ):
            with self.subTest(source=source):
                actual = literal_source_view(source)
                self.assertEqual(actual, expected)
                self.assertEqual(len(actual), len(source))

    def test_bibliographic_references_preserve_spacing_and_offsets(self):
        for source, expected in (
            ("[13, б. 114–115]", "[13, b. 114–115]"),
            ("[17; 113-114б.]", "[17; 113-114b.]"),
            ("[шунда ук, б. 322]", "[шунда ук, b. 322]"),
            ("(т. 1, б. 247)", "(t. 1, b. 247)"),
            ("[13, б. пункт] (т. 1, б. пункт)", "[13, б. пункт] (т. 1, б. пункт)"),
        ):
            with self.subTest(source=source):
                actual = literal_source_view(source)
                self.assertEqual(actual, expected)
                self.assertEqual(len(actual), len(source))
                self.assertEqual(literal_source_view(actual), actual)

    def test_dashed_and_quoted_list_labels_preserve_their_punctuation(self):
        for source, expected in (
            ("Исемлек:\n- а) беренче;\n— д) икенче.", "Исемлек:\n- a) беренче;\n— d) икенче."),
            ('Өстәргә:\n"е) адрес"', 'Өстәргә:\n"e) адрес"'),
            ("Әйберләр: а — чүкеч; б – аслык.", "Әйберләр: a — чүкеч; b – аслык."),
            ("Сүз эчендә а — кирәк", "Сүз эчендә а — кирәк"),
        ):
            with self.subTest(source=source):
                self.assertEqual(literal_source_view(source), expected)

    def test_reviewed_house_numbers_preserve_case_and_punctuation(self):
        for source, expected in (
            ("Коммунистлар ур., 27В йорт", "Коммунистлар ур., 27V йорт"),
            ("Дуслык ур., 25А.й. (авыл клубы бинасы)", "Дуслык ур., 25A.y. (авыл клубы бинасы)"),
            ("Нариманов ур., 47/а йорт", "Нариманов ур., 47/a йорт"),
            ("27В; 25А.й.; 47/а", "27В; 25А.й.; 47/а"),
        ):
            with self.subTest(source=source):
                actual = literal_source_view(source)
                self.assertEqual(actual, expected)
                self.assertEqual(len(actual), len(source))

    def test_table_row_number_markers_preserve_spacing_and_punctuation(self):
        source = "№ п/п\n№ № п/п\nN\tп/п\nп/п; AN п/п"
        expected = "№ p/p\n№ № p/p\nN\tp/p\nп/п; AN п/п"
        actual = literal_source_view(source)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(source))

    def test_reviewed_administrative_abbreviations_preserve_internal_dots(self):
        source = "Ф.И.О., ш.т.п.; ФИО, штп; сүзФ.И.О."
        expected = "F.İ.O., ş.t.p.; ФИО, штп; сүзФ.И.О."
        self.assertEqual(literal_source_view(source), expected)

    def test_figure_panel_references_project_only_panel_letters(self):
        source = "(рәс. 103, б); (рәс. 111, а, б); (111, а, б)"
        expected = "(рәс. 103, b); (рәс. 111, a, b); (111, а, б)"
        self.assertEqual(literal_source_view(source), expected)

    def test_reviewed_manuscript_notes_preserve_brackets_and_folio_numbers(self):
        source = "(16-2а кәгазьләр); [лакуна] (б. 5–4а); 5–4а; лакуна"
        expected = "(16-2a кәгазьләр); [lakuna] (b. 5–4a); 5–4а; лакуна"
        actual = literal_source_view(source)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(source))

    def test_reviewed_grammar_examples_preserve_notation(self):
        source = "(-ы/-е; -сы/-се), -ай/-әй дифтонгы; -ай/-әй"
        expected = "(-ı/-e; -sı/-se), -ay/-äy дифтонгы; -ай/-әй"
        actual = literal_source_view(source)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(source))

    def test_reviewed_dialect_examples_preserve_written_notation(self):
        source = r"Тирән тел арты қ, ғ, х тартыклары; -сығыз кушымчасы; \]ө\] авазларының"
        expected = r"Тирән тел арты q, ğ, x тартыклары; -sığız кушымчасы; \]ö\] авазларының"
        actual = literal_source_view(source)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(source))

    def test_order_number_suffix_preserves_identifier_separators(self):
        source = "344/пр номерлы боерыгы; 01-05/52-пр номерлы боерыгына; 344/пр номерлы карар"
        expected = "344/pr номерлы боерыгы; 01-05/52-pr номерлы боерыгына; 344/пр номерлы карар"
        self.assertEqual(literal_source_view(source), expected)

    def test_tariff_decision_numbers_preserve_marker_and_separators(self):
        source = "№ 10-171/кс карары; № 5-53/тэ карарына; 5-53/тэ; № 10-171/кс төзелеше"
        expected = "№ 10-171/ks карары; № 5-53/te карарына; 5-53/тэ; № 10-171/кс төзелеше"
        actual = literal_source_view(source)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(source))

    def test_decree_numbers_preserve_prefix_order(self):
        source = "ПУ-1129 номерлы Указы; № УП-572 Указы; ПУ-543 номерлы боерыгы"
        expected = "PU-1129 номерлы Указы; № UP-572 Указы; ПУ-543 номерлы боерыгы"
        self.assertEqual(literal_source_view(source), expected)

    def test_reviewed_document_identifiers_preserve_digits_and_separators(self):
        source = "№ММВ-7-6/529; ФС77-60244; П/103; П/1030"
        expected = "№MMV-7-6/529; FS77-60244; P/103; П/1030"
        actual = literal_source_view(source)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(source))

    def test_reviewed_administrative_references_keep_prefix_letters_and_spacing(self):
        source = "№ММВ-7-8 / 164 @; УЛ-1821; УП-21; ПГ-5; УП-210"
        expected = "№MMV-7-8 / 164 @; UL-1821; UP-21; PG-5; УП-210"
        self.assertEqual(literal_source_view(source), expected)

    def test_reviewed_transport_codes_preserve_hyphens_and_context(self):
        source = "М-7 «Волга», Р-239 автомобиль юлына; ДТ-54; Іб-техник категорияле; М-7"
        expected = "M-7 «Волга», R-239 автомобиль юлына; DT-54; Ib-техник категорияле; М-7"
        self.assertEqual(literal_source_view(source), expected)

    def test_reviewed_zoning_codes_preserve_category_text(self):
        source = "Ж1 «Шәхси торак төзелеше зонасы»; Д1- эшлекле; П4 класслы; П4"
        expected = "J1 «Шәхси торак төзелеше зонасы»; D1- эшлекле; P4 класслы; П4"
        self.assertEqual(literal_source_view(source), expected)

    def test_reviewed_legal_and_numeric_labels_preserve_source_notation(self):
        source = '«г», «з» пунктчаларында; No п/п; “5”ле куела; “5”ле саналган'
        expected = '«g», «z» пунктчаларында; No p/p; “5”le куела; “5”ле саналган'
        actual = literal_source_view(source)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(source))
