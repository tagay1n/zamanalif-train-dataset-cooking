from __future__ import annotations

import unittest

from zamanalif_selector.segment import split_sentence_records, split_sentences


class SentenceSegmentationTests(unittest.TestCase):
    def test_basic_punctuation_split(self) -> None:
        text = "Ел башында вакыт турында карар чыкты. Юл кырыенда билге бар."

        sentences = split_sentences(text, min_chars=1)

        self.assertEqual(
            sentences,
            [
                "Ел башында вакыт турында карар чыкты.",
                "Юл кырыенда билге бар.",
            ],
        )

    def test_abbreviation_does_not_split_sentence(self) -> None:
        text = "Бу һ.б. мисаллар өчен кирәк. Икенче җөмлә дә бар."

        sentences = split_sentences(text, min_chars=1)

        self.assertEqual(len(sentences), 2)
        self.assertEqual(sentences[0], "Бу һ.б. мисаллар өчен кирәк.")

    def test_decimal_number_does_not_split_sentence(self) -> None:
        text = "Бу күрсәткеч 3.14 дәрәҗәсендә калды. Аннары яңа ел башланды."

        sentences = split_sentences(text, min_chars=1)

        self.assertEqual(len(sentences), 2)
        self.assertIn("3.14", sentences[0])

    def test_closing_quote_stays_with_sentence(self) -> None:
        text = "Ул: «Юл ачык!» диде. Аннары китте."

        sentences = split_sentences(text, min_chars=1)

        self.assertEqual(sentences[0], "Ул: «Юл ачык!» диде.")

    def test_newline_can_split_without_terminal_punctuation(self) -> None:
        text = "Ел башында вакыт турында карар\nЮл кырыенда билге бар."

        sentences = split_sentences(text, min_chars=1)

        self.assertEqual(len(sentences), 2)
        self.assertEqual(sentences[0], "Ел башында вакыт турында карар")

    def test_records_include_offsets_and_diagnostics(self) -> None:
        result = split_sentence_records("Кыска. Ел башында вакыт турында карар чыкты.", min_chars=10)

        self.assertEqual(result.diagnostics["accepted"], 1)
        self.assertEqual(result.diagnostics["rejected:too_short"], 1)
        self.assertEqual(result.sentences[0].sentence, "Ел башында вакыт турында карар чыкты.")
        self.assertGreaterEqual(result.sentences[0].start_char, 0)
        self.assertGreater(result.sentences[0].end_char, result.sentences[0].start_char)


if __name__ == "__main__":
    unittest.main()
