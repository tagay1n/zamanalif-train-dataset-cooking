from __future__ import annotations

from collections import Counter
import unittest

from zamanalif_selector.features import (
    count_tatar_specific_letters,
    extract_features,
    has_conditional_letter,
    has_min_tatar_specific_letters,
    has_mixed_harmony_word,
    sentence_quality,
    vowel_harmony_class,
    word_frequencies,
)


class FeatureExtractionTests(unittest.TestCase):
    def test_conditional_word_and_context_features(self) -> None:
        extracted = extract_features("ел", {"ел": 3})

        self.assertEqual(extracted.features["cond_letter:е"], 1)
        self.assertEqual(extracted.features["cond_word:ел"], 1)
        self.assertEqual(extracted.features["cond_ctx:^*е*л"], 1)
        self.assertEqual(extracted.features["cond_bigram_right:ел"], 1)
        self.assertEqual(extracted.features["cond_position:е:initial"], 1)
        self.assertEqual(extracted.features["ambig:initial_e"], 1)

    def test_examples_emit_expected_conditional_words(self) -> None:
        text = "вакыт күрә дәүләт юл ел"
        frequencies = word_frequencies([text, text])
        extracted = extract_features(text, frequencies)

        for word in ["вакыт", "күрә", "дәүләт", "юл", "ел"]:
            self.assertGreater(extracted.features[f"cond_word:{word}"], 0)

    def test_frequency_thresholds_apply_only_to_conditional_words(self) -> None:
        extracted = extract_features(
            "вакыт сирәк",
            {"вакыт": 1, "сирәк": 3},
            min_word_frequency=2,
            max_word_frequency=10,
        )

        self.assertEqual(extracted.features["cond_word:вакыт"], 0)
        self.assertEqual(extracted.features["word:вакыт"], 1)

    def test_k_and_g_front_back_vowel_patterns(self) -> None:
        extracted = extract_features("кала килә гасыр гөл", Counter())

        self.assertEqual(extracted.features["ambig:k_before_back_vowel"], 1)
        self.assertEqual(extracted.features["ambig:k_before_front_vowel"], 1)
        self.assertEqual(extracted.features["ambig:g_before_back_vowel"], 1)
        self.assertEqual(extracted.features["ambig:g_before_front_vowel"], 1)

    def test_deterministic_letters_are_tracked(self) -> None:
        extracted = extract_features("ә ө җ ң һ", Counter())

        for letter in "әөҗңһ":
            self.assertEqual(extracted.features[f"deterministic_letter:{letter}"], 1)

    def test_vowel_harmony_classification(self) -> None:
        self.assertEqual(vowel_harmony_class("күрә"), "front_only")
        self.assertEqual(vowel_harmony_class("бара"), "back_only")
        self.assertEqual(vowel_harmony_class("гадел"), "mixed_front_back")
        self.assertEqual(vowel_harmony_class("ртм"), "no_vowels")

    def test_mixed_harmony_word_uses_frequency_thresholds(self) -> None:
        extracted = extract_features(
            "гадел гадел сирәк",
            {"гадел": 2, "сирәк": 1},
            min_word_frequency=2,
            max_word_frequency=2,
        )

        self.assertEqual(extracted.features["vowel_harmony:mixed_front_back"], 2)
        self.assertEqual(extracted.features["vowel_harmony_word:гадел"], 2)
        self.assertEqual(extracted.features["vowel_harmony_word:сирәк"], 0)

    def test_mixed_harmony_conditional_ambiguity_features(self) -> None:
        extracted = extract_features("гадел каяә евро цехта", Counter())

        self.assertGreater(extracted.features["ambig:mixed_harmony_conditional_word"], 0)
        self.assertEqual(extracted.features["ambig:mixed_harmony_g"], 1)
        self.assertEqual(extracted.features["ambig:mixed_harmony_k"], 1)
        self.assertEqual(extracted.features["ambig:mixed_harmony_e"], 3)
        self.assertEqual(extracted.features["ambig:mixed_harmony_ya"], 1)
        self.assertEqual(extracted.features["ambig:mixed_harmony_v"], 1)
        self.assertEqual(extracted.features["ambig:mixed_harmony_ts"], 1)

    def test_prepare_prefilter_helpers(self) -> None:
        self.assertTrue(has_conditional_letter("Ел башында вакыт бар."))
        self.assertTrue(has_mixed_harmony_word("Гадел сүз әйтелде."))
        self.assertFalse(has_conditional_letter("Әни һаман җырлый."))
        self.assertFalse(has_mixed_harmony_word("Әни һаман җырлый."))

    def test_tatar_specific_letter_filter_blocks_russian_sentence(self) -> None:
        sentence = "В случае обнаружения технической ошибки заявитель представляет документы."

        self.assertEqual(count_tatar_specific_letters(sentence), 0)
        self.assertFalse(has_min_tatar_specific_letters(sentence))

    def test_tatar_specific_letter_filter_accepts_two_occurrences(self) -> None:
        sentence = "Әни шәһәргә бара."

        self.assertGreaterEqual(count_tatar_specific_letters(sentence), 2)
        self.assertTrue(has_min_tatar_specific_letters(sentence))

    def test_tatar_specific_letter_filter_accepts_repeated_same_letter(self) -> None:
        self.assertEqual(count_tatar_specific_letters("Әә."), 2)
        self.assertTrue(has_min_tatar_specific_letters("Әә."))

    def test_tatar_specific_letter_filter_rejects_one_occurrence(self) -> None:
        self.assertEqual(count_tatar_specific_letters("Әни бара."), 1)
        self.assertFalse(has_min_tatar_specific_letters("Әни бара."))

    def test_tatar_specific_letter_filter_rejects_tatar_without_specific_letters(self) -> None:
        self.assertEqual(count_tatar_specific_letters("Бу ел юл бар."), 0)
        self.assertFalse(has_min_tatar_specific_letters("Бу ел юл бар."))

    def test_sentence_quality_marks_markdown_and_low_density_artifacts(self) -> None:
        quality = sentence_quality(
            "## ВЕРХОВНЫЙ БАШ КОМАНДУЮЩИЙ ПРИКАЗЫ ### гаскәрләренә шәһәргә."
        )

        self.assertTrue(quality.is_artifact)
        self.assertIn("markdown", quality.artifact_reasons)

    def test_sentence_quality_accepts_clean_tatar_sentence(self) -> None:
        quality = sentence_quality("Әни шәһәргә бара һәм яңа сүзләр өйрәнә.")

        self.assertFalse(quality.is_artifact)
        self.assertGreater(quality.tatar_specific_per_word, 0.12)


if __name__ == "__main__":
    unittest.main()
