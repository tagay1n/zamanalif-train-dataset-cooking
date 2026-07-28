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
from tatar_preannotator.conversion import resolve_dsl
from tatar_preannotator.word_export import (
    AnnotationExportError,
    classify_project,
    contains_conditional_letter,
    contains_rl_review_letter,
    conversion_branches,
    convert_for_annotation,
    convert_for_annotation_dsl,
    export_labelstudio_project_tasks_from_db,
    export_labelstudio_tasks_from_db,
    load_exported_words,
    load_reviewed_words,
    mark_exported_words,
    normalize_word,
    save_reviewed_word,
    validate_export_result,
    validate_split_export_result,
    vowel_harmony_class,
    write_split_outputs,
)


class PreannotatorWordExportTests(unittest.TestCase):
    def test_normalize_word_strips_punctuation_and_lowercases(self) -> None:
        self.assertEqual(normalize_word("«Вакытында!»"), "вакытында")
        self.assertEqual(normalize_word("..."), "")
        self.assertEqual(normalize_word("сүз-сүз"), "сүз-сүз")
        self.assertEqual(normalize_word("Шофёр"), "шофёр")

    def test_conditional_letter_detection(self) -> None:
        self.assertTrue(contains_conditional_letter("вакыт"))
        self.assertTrue(contains_conditional_letter("позиция"))
        self.assertFalse(contains_conditional_letter("шәһәр"))
        self.assertFalse(contains_conditional_letter("сыр"))
        self.assertTrue(contains_rl_review_letter("сыр"))
        self.assertTrue(contains_rl_review_letter("роль"))
        self.assertTrue(contains_rl_review_letter("шофёр"))

    def test_vowel_harmony_classification(self) -> None:
        self.assertEqual(vowel_harmony_class("күрә"), "front_only")
        self.assertEqual(vowel_harmony_class("бара"), "back_only")
        self.assertEqual(vowel_harmony_class("гадел"), "mixed_front_back")

    def test_export_filters_deduplicates_and_generates_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "Мин", "label": "N"},
                            {"text": "вакытында", "label": "N"},
                            {"text": "Вакытында", "label": "N"},
                            {"text": "яңа", "label": "N"},
                            {"text": "проект", "label": "RL"},
                            {"text": "турында", "label": "N"},
                            {"text": "әйттем", "label": "N"},
                        ],
                    },
                    {
                        "id": "sent_2",
                        "tatar": True,
                        "tokens": [
                            {"text": "Гадел", "label": "N"},
                            {"text": "сүз", "label": "U"},
                            {"text": "торак", "label": "U"},
                            {"text": "сер", "label": "N"},
                        ],
                    },
                    {
                        "id": "sent_3",
                        "tatar": True,
                        "tokens": [
                            {"text": "позиция", "label": "RL"},
                            {"text": "дөрес", "label": "N"},
                        ],
                    },
                    {
                        "id": "sent_4",
                        "tatar": False,
                        "tokens": [{"text": "вакыт", "label": "N"}],
                    },
                ],
            )

            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")

        words = [task["data"]["cyrl_word"] for task in result.tasks]
        self.assertEqual(
            words,
            ["вакытында", "проект", "торак"],
        )
        self.assertEqual(result.tasks[0]["data"]["auto_zamanalif"], "waqıtında")
        self.assertEqual(
            result.tasks[1]["data"]["auto_zamanalif"],
            "pro{{E_GLIDE|plain=e|glide=ye}}kt",
        )
        self.assertEqual(result.tasks[2]["data"]["auto_zamanalif"], "")
        self.assertEqual(result.tasks[1]["data"]["gemini_origin"], "RL")
        self.assertEqual(result.report["mixed_harmony_n_word_skipped_count"], 1)
        self.assertEqual(result.report["u_exported_word_count"], 1)
        self.assertGreater(result.report["origin_independent_word_count"], 0)
        self.assertGreater(result.report["origin_dependent_word_count"], 0)

        html = result.tasks[0]["data"]["hints_html"]
        self.assertIn("<b>в</b> -> <b>w</b>", html)
        self.assertIn("<b>к</b> -> <b>q</b>", html)
        self.assertIn("Gemini's origin prediction: <b>native</b>", html)
        self.assertIn("Frequency for <b><i>вакытында</i></b>: <b>2</b>", html)

    def test_mixed_harmony_rl_is_kept_and_rl_without_conditional_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "проект", "label": "RL"},
                            {"text": "банк", "label": "RL"},
                            {"text": "спорт", "label": "RL"},
                        ],
                    },
                ],
            )

            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")

        self.assertEqual([task["data"]["cyrl_word"] for task in result.tasks], ["банк", "проект"])
        self.assertEqual(
            result.tasks[1]["data"]["auto_zamanalif"],
            "pro{{E_GLIDE|plain=e|glide=ye}}kt",
        )

    def test_russian_loanword_review_letters_are_exported_for_rl_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "сыр", "label": "RL"},
                            {"text": "роль", "label": "RL"},
                            {"text": "шофёр", "label": "RL"},
                            {"text": "тын", "label": "N"},
                            {"text": "щетка", "label": "RL"},
                        ],
                    },
                ],
            )

            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")

        words = [task["data"]["cyrl_word"] for task in result.tasks]
        self.assertEqual(words, ["роль", "сыр", "шофёр", "щетка"])
        by_word = {task["data"]["cyrl_word"]: task["data"] for task in result.tasks}
        self.assertEqual(
            by_word["сыр"]["auto_zamanalif"],
            "s{{RL_Y|short=ı|long=ıy}}r",
        )
        self.assertEqual(
            by_word["роль"]["auto_zamanalif"],
            "rol{{RUS_SIGN|omit=|preserve=ʼ}}",
        )
        self.assertEqual(
            by_word["шофёр"]["auto_zamanalif"],
            "şof{{RUS_JOTATION|glide=y|apostrophe=ʼ|plain=}}or",
        )
        self.assertEqual(by_word["щетка"]["auto_zamanalif"], "şçetka")
        self.assertIn("<b>ы</b> -> <b>ıy</b>", by_word["сыр"]["hints_html"])
        self.assertIn("<b>ь</b> -> <b>ʼ</b>", by_word["роль"]["hints_html"])

    def test_branch_analysis_only_reviews_origin_dependent_conversion(self) -> None:
        independent = conversion_branches("белән")
        dependent = conversion_branches("авыл")
        unavailable = conversion_branches("к")

        self.assertEqual(independent.state, "origin_independent")
        self.assertEqual(independent.native_dsl, "belän")
        self.assertEqual(independent.loanword_dsl, "belän")
        self.assertEqual(dependent.state, "origin_dependent")
        self.assertEqual(dependent.native_dsl, "awıl")
        self.assertEqual(dependent.loanword_dsl, "avıl")
        self.assertEqual(unavailable.state, "unconvertible")
        self.assertEqual(unavailable.native_dsl, "")
        self.assertEqual(unavailable.loanword_dsl, "k")

    def test_include_unknown_and_include_rl_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "сүз", "label": "U"},
                            {"text": "проект", "label": "RL"},
                        ],
                    },
                ],
            )

            result = export_labelstudio_tasks_from_db(
                db_path,
                include_unknown=False,
                include_rl=False,
            )

        self.assertEqual(result.tasks, [])

    def test_converter_integration_and_clean_zamanalif_letters(self) -> None:
        self.assertEqual(convert_for_annotation("шәһәр", "N"), "şähär")
        self.assertEqual(convert_for_annotation("проект", "RL"), "proyekt")
        self.assertEqual(convert_for_annotation("яңа", "N"), "yaña")
        self.assertEqual(convert_for_annotation("канат", " RL"), "kanat")
        self.assertEqual(convert_for_annotation("саескан", "N"), "sayısqan")
        self.assertEqual(convert_for_annotation("тавышкиметкеч", "N"), "tawışkimetkeç")
        self.assertEqual(convert_for_annotation("бакырелан", "N"), "baqıryılan")
        self.assertEqual(convert_for_annotation("ю", "N"), "yü")
        self.assertEqual(
            convert_for_annotation_dsl("фамилия", "N"),
            "famili{{IYA|compact=ä|explicit=yä}}",
        )

    def test_homonym_word_is_deferred_even_if_another_occurrence_is_unmarked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [{"text": "сер", "label": "RL", "homonym": True}],
                    },
                    {
                        "id": "sent_2",
                        "tatar": True,
                        "tokens": [
                            {"text": "сер", "label": "N"},
                            {"text": "вакыт", "label": "N"},
                        ],
                    },
                ],
            )

            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")

        self.assertEqual([task["data"]["cyrl_word"] for task in result.tasks], ["вакыт"])
        self.assertEqual(result.report["homonym_words_deferred_count"], 1)
        self.assertEqual(result.report["homonym_occurrences_skipped_count"], 2)

    def test_word_resolution_clears_bad_homonym_and_sets_effective_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "һәм", "label": "N"},
                            {"text": "авыл", "label": "RL", "homonym": True},
                        ],
                    },
                    {
                        "id": "sent_2",
                        "tatar": True,
                        "tokens": [
                            {"text": "авыл", "label": "N"},
                        ],
                    },
                ],
            )
            with sqlite3.connect(db_path) as conn:
                save_word_resolution(conn, "авыл", "N")

            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")

        self.assertEqual([task["data"]["cyrl_word"] for task in result.tasks], ["авыл"])
        self.assertEqual(result.tasks[0]["data"]["gemini_origin"], "N")
        self.assertEqual(result.report["homonym_words_deferred_count"], 0)

    def test_contextual_homonym_resolution_remains_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "сер", "label": "RL", "homonym": True},
                            {"text": "вакыт", "label": "N"},
                        ],
                    },
                ],
            )
            with sqlite3.connect(db_path) as conn:
                save_word_resolution(conn, "сер", "contextual_homonym")

            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")

        self.assertEqual([task["data"]["cyrl_word"] for task in result.tasks], ["вакыт"])

    def test_reviewed_word_dictionary_persists_dsl_and_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "zamanalif.sqlite"

            save_reviewed_word(
                db_path,
                "орфография",
                "orfografi{{IYA|compact=ä|explicit=yä}}",
                "RL",
            )
            reviewed = load_reviewed_words(db_path)

        self.assertEqual(reviewed["орфография"].origin, "RL")
        self.assertEqual(
            reviewed["орфография"].zamanalif_dsl,
            "orfografi{{IYA|compact=ä|explicit=yä}}",
        )

    def test_reviewed_word_never_reappears_without_export_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [{"text": "авыл", "label": "N"}],
                    }
                ],
            )
            save_reviewed_word(db_path, "авыл", "awıl", "N")

            result = export_labelstudio_tasks_from_db(db_path)

        self.assertEqual(result.tasks, [])
        self.assertEqual(result.report["reviewed_words_skipped_count"], 1)

    def test_ya_conversion_context_rules(self) -> None:
        self.assertEqual(convert_for_annotation("әдәбият", "N"), "ädäbiät")
        self.assertEqual(convert_for_annotation("позиция", "RL"), "pozitsiä")
        self.assertEqual(convert_for_annotation("фамилия", "N"), "familiä")
        self.assertEqual(convert_for_annotation("як", "N"), "yaq")
        self.assertEqual(convert_for_annotation("ял", "N"), "yal")
        self.assertEqual(convert_for_annotation("ян", "N"), "yan")
        self.assertEqual(convert_for_annotation("яр", "N"), "yar")
        self.assertEqual(convert_for_annotation("ят", "N"), "yat")
        self.assertEqual(convert_for_annotation("ящик", "RL"), "yaşçik")
        self.assertEqual(convert_for_annotation("я", "N"), "yä")
        self.assertEqual(convert_for_annotation("яңалиф", "N"), "yañalif")
        self.assertEqual(convert_for_annotation("яшь", "N"), "yäş")
        self.assertEqual(convert_for_annotation("ярдәм", "N"), "yärdäm")
        self.assertEqual(convert_for_annotation("яшел", "N"), "yäşel")
        self.assertEqual(convert_for_annotation("яки", "RL"), "yäki")
        self.assertEqual(convert_for_annotation("дөнья", "N"), "dönya")
        self.assertEqual(convert_for_annotation("көньяк", "N"), "könyaq")
        self.assertEqual(convert_for_annotation("һәръяклап", "N"), "häryaqlap")
        self.assertEqual(
            convert_for_annotation_dsl("ладья", "RL"),
            "lad{{RUS_SIGN|omit=|preserve=ʼ}}ya",
        )

    def test_e_conversion_uses_pdf_context_rules(self) -> None:
        self.assertEqual(convert_for_annotation("электр", "RL"), "elektr")
        self.assertEqual(convert_for_annotation("телефон", "RL"), "telefon")
        self.assertEqual(convert_for_annotation("билет", "RL"), "bilet")
        self.assertEqual(convert_for_annotation("поездан", "RL"), "poyezdan")
        self.assertEqual(convert_for_annotation("проекты", "RL"), "proyektı")
        self.assertEqual(convert_for_annotation("крае", "RL"), "krayı")
        self.assertEqual(convert_for_annotation("бодуен", "RL"), "boduen")
        self.assertEqual(convert_for_annotation("нуриев", "RL"), "nuriev")
        self.assertEqual(convert_for_annotation("объект", "RL"), "obyekt")
        self.assertEqual(convert_for_annotation("съезда", "N"), "syezda")
        self.assertEqual(convert_for_annotation("йөзьеллык", "N"), "yözyıllıq")
        self.assertEqual(convert_for_annotation("меңьеллык", "N"), "meñyıllıq")
        self.assertEqual(convert_for_annotation("гәрәев", "RL"), "gäräyev")
        self.assertEqual(convert_for_annotation("егет", "N"), "yeget")
        self.assertEqual(convert_for_annotation("ел", "N"), "yıl")
        self.assertEqual(convert_for_annotation("ерак", "N"), "yıraq")
        self.assertEqual(convert_for_annotation("еш", "N"), "yış")
        self.assertEqual(convert_for_annotation("европа", "RL"), "yevropa")
        self.assertEqual(convert_for_annotation("европалы", "RL"), "yevropalı")
        self.assertEqual(convert_for_annotation("евразияле", "N"), "yewraziäle")
        self.assertEqual(convert_for_annotation("епископ", "RL"), "yepiskop")
        self.assertEqual(
            resolve_dsl(
                convert_for_annotation_dsl("епископаль", "RL"),
                {"RUS_SIGN": "omit"},
            ),
            "yepiskopal",
        )
        self.assertEqual(convert_for_annotation("ефәксыман", "N"), "yefäksıman")
        self.assertEqual(convert_for_annotation("е", "N"), "yı")
        self.assertEqual(convert_for_annotation("килүе", "N"), "kilüe")
        self.assertEqual(convert_for_annotation("пьеса", "N"), "pyesa")

    def test_e_glide_is_policy_dsl(self) -> None:
        cases = [
            ("тиеш", "N", "ti{{E_GLIDE|plain=e|glide=ye}}ş", "tieş", "tiyeş"),
            ("тиен", "N", "ti{{E_GLIDE|plain=e|glide=ye}}n", "tien", "tiyen"),
            ("мие", "N", "mi{{E_GLIDE|plain=e|glide=ye}}", "mie", "miye"),
            ("задание", "RL", "zadani{{E_GLIDE|plain=e|glide=ye}}", "zadanie", "zadaniye"),
            ("имение", "RL", "imeni{{E_GLIDE|plain=e|glide=ye}}", "imenie", "imeniye"),
        ]

        for word, label, expected_dsl, plain, glide in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, label)
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl, {"E_GLIDE": "plain"}), plain)
                self.assertEqual(resolve_dsl(dsl, {"E_GLIDE": "glide"}), glide)
                self.assertEqual(resolve_dsl(dsl), glide)

    def test_hard_iya_stems_use_hard_iya_policy_text(self) -> None:
        cases = [
            ("әдәбият", "ädäbi{{IYA|compact=a|explicit=ya}}t", "ädäbiyat", "ädäbiat"),
            ("әүлия", "äwli{{IYA|compact=a|explicit=ya}}", "äwliya", "äwlia"),
            (
                "әүлиялек",
                "äwli{{IYA|compact=a|explicit=ya}}lek",
                "äwliyalek",
                "äwlialek",
            ),
            ("риялы", "ri{{IYA|compact=a|explicit=ya}}lı", "riyalı", "rialı"),
        ]

        for word, expected_dsl, explicit, compact in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "N")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl), explicit)
                self.assertEqual(resolve_dsl(dsl, {"IYA": "compact"}), compact)

        self.assertEqual(
            convert_for_annotation_dsl("риясыз", "N"),
            "ri{{IYA|compact=ä|explicit=yä}}sız",
        )
        self.assertEqual(
            convert_for_annotation_dsl("риялану", "N"),
            "ri{{IYA|compact=ä|explicit=yä}}lanu",
        )

    def test_project_e_is_policy_dsl(self) -> None:
        cases = [
            ("проект", "pro{{E_GLIDE|plain=e|glide=ye}}kt", "proekt", "proyekt"),
            ("проекты", "pro{{E_GLIDE|plain=e|glide=ye}}ktı", "proektı", "proyektı"),
            ("проектын", "pro{{E_GLIDE|plain=e|glide=ye}}ktın", "proektın", "proyektın"),
            (
                "проектының",
                "pro{{E_GLIDE|plain=e|glide=ye}}ktınıñ",
                "proektınıñ",
                "proyektınıñ",
            ),
        ]

        for word, expected_dsl, plain, glide in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "RL")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl, {"E_GLIDE": "plain"}), plain)
                self.assertEqual(resolve_dsl(dsl), glide)

    def test_rl_y_is_policy_dsl(self) -> None:
        cases = [
            ("музыка", "muz{{RL_Y|short=ı|long=ıy}}ka", "muzıka", "muzıyka"),
            (
                "музыкаль",
                "muz{{RL_Y|short=ı|long=ıy}}kal{{RUS_SIGN|omit=|preserve=ʼ}}",
                "muzıkal",
                "muzıykalʼ",
            ),
            (
                "музыкасын",
                "muz{{RL_Y|short=ı|long=ıy}}kas{{RL_Y|short=ı|long=ıy}}n",
                "muzıkasın",
                "muzıykasıyn",
            ),
            (
                "музыкасына",
                "muz{{RL_Y|short=ı|long=ıy}}kas{{RL_Y|short=ı|long=ıy}}na",
                "muzıkasına",
                "muzıykasıyna",
            ),
            ("сыр", "s{{RL_Y|short=ı|long=ıy}}r", "sır", "sıyr"),
            ("посылка", "pos{{RL_Y|short=ı|long=ıy}}lka", "posılka", "posıylka"),
            ("вышка", "v{{RL_Y|short=ı|long=ıy}}şka", "vışka", "vıyşka"),
        ]

        for word, expected_dsl, short, long in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "RL")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(
                    resolve_dsl(
                        dsl,
                        {"RL_Y": "short", "RUS_SIGN": "omit"},
                    ),
                    short,
                )
                self.assertEqual(resolve_dsl(dsl), long)

    def test_mostaqil_is_policy_dsl(self) -> None:
        cases = [
            (
                "мөстәкыйль",
                "möstä{{MOSTAQIL|pdf=qil|antat=qıyl}}",
                "möstäqil",
                "möstäqıyl",
            ),
            (
                "мөстәкыйльлеге",
                "möstä{{MOSTAQIL|pdf=qil|antat=qıylʼ}}lege",
                "möstäqillege",
                "möstäqıylʼlege",
            ),
            (
                "мөстәкыйльлек",
                "möstä{{MOSTAQIL|pdf=qil|antat=qıylʼ}}lek",
                "möstäqillek",
                "möstäqıylʼlek",
            ),
        ]

        for word, expected_dsl, pdf, antat in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "N")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl, {"MOSTAQIL": "pdf"}), pdf)
                self.assertEqual(resolve_dsl(dsl), antat)

    def test_final_double_l_is_deterministic_single_l(self) -> None:
        cases = [
            ("металл", "metal"),
            ("металлга", "metalğa"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "N")
                self.assertEqual(dsl, expected)
                self.assertEqual(resolve_dsl(dsl), expected)

    def test_native_vowel_before_e_uses_y_glide_vowel_harmony(self) -> None:
        self.assertEqual(convert_for_annotation("аерым", "N"), "ayırım")
        self.assertEqual(convert_for_annotation("оешма", "N"), "oyışma")
        self.assertEqual(convert_for_annotation("куеп", "N"), "quyıp")
        self.assertEqual(convert_for_annotation("буенча", "N"), "buyınça")
        self.assertEqual(convert_for_annotation("кыен", "N"), "qıyın")
        self.assertEqual(convert_for_annotation("сыеп", "N"), "sıyıp")
        self.assertEqual(convert_for_annotation("җыенам", "N"), "cıyınam")
        self.assertEqual(convert_for_annotation("гәет", "N"), "gäyet")
        self.assertEqual(convert_for_annotation("бөек", "N"), "böyek")
        self.assertEqual(convert_for_annotation("төен-төйнә", "N"), "töyen-töynä")

    def test_surname_v_endings_are_converted_as_v(self) -> None:
        self.assertEqual(convert_for_annotation("мәһдиев", "N"), "mähdiev")
        self.assertEqual(convert_for_annotation("әлмиев", "N"), "älmiev")
        self.assertEqual(convert_for_annotation("әлмиевкә", "N"), "älmievkä")
        self.assertEqual(convert_for_annotation("сәлимев", "N"), "sälimev")
        self.assertEqual(convert_for_annotation("юлдашев", "N"), "yuldaşev")
        self.assertEqual(convert_for_annotation("юлдашева", "N"), "yuldaşeva")
        self.assertEqual(convert_for_annotation("булатов", "N"), "bulatov")
        self.assertEqual(convert_for_annotation("булатова", "N"), "bulatova")
        self.assertEqual(convert_for_annotation("вакыт", "N"), "waqıt")
        self.assertEqual(convert_for_annotation("актив", "RL"), "aktiv")

    def test_loanword_yerı_uses_short_i_by_default(self) -> None:
        self.assertEqual(convert_for_annotation("алфавиты", "RL"), "alfavitı")
        self.assertEqual(convert_for_annotation("классы", "RL"), "klassı")
        self.assertEqual(convert_for_annotation("руханый", "RL"), "ruxanıy")
        self.assertEqual(convert_for_annotation("сыйр", "RL"), "sıyr")
        self.assertEqual(convert_for_annotation("сыр", "RL"), "sıyr")
        self.assertEqual(convert_for_annotation("музыка", "RL"), "muzıyka")
        self.assertEqual(convert_for_annotation("посылка", "RL"), "posıylka")
        self.assertEqual(convert_for_annotation("вышка", "RL"), "vıyşka")

    def test_loanword_tatar_law_suffix_is_deterministic(self) -> None:
        cases = [
            ("граверлау", "RL", "graverlaw"),
            ("консервлау", "RL", "konservlaw"),
            ("страховкалау", "RL", "straxovkalaw"),
            ("боулинг", "RL", "bowling"),
            ("культура", "RL", "kulʼtura"),
        ]

        for word, label, expected in cases:
            with self.subTest(word=word, label=label):
                self.assertEqual(convert_for_annotation(word, label), expected)
                self.assertEqual(resolve_dsl(convert_for_annotation_dsl(word, label)), expected)

    def test_loanword_final_ets_is_deterministic(self) -> None:
        cases = [
            ("индеец", "RL", "indeyets"),
            ("леденец", "RL", "ledenets"),
            ("новобранец", "RL", "novobranets"),
            ("полководец", "RL", "polkovodets"),
            ("ранец", "RL", "ranets"),
        ]

        for word, label, expected in cases:
            with self.subTest(word=word, label=label):
                self.assertEqual(convert_for_annotation(word, label), expected)
                self.assertEqual(convert_for_annotation_dsl(word, label), expected)

    def test_native_hamza_lexical_cases(self) -> None:
        self.assertEqual(convert_for_annotation("маэмай", "N"), "maʼmay")
        self.assertEqual(convert_for_annotation_dsl("маэмай", "N"), "maʼmay")

    def test_native_k_g_use_local_vowel_context(self) -> None:
        self.assertEqual(convert_for_annotation("китап", "N"), "kitap")
        self.assertEqual(convert_for_annotation("мәктәп", "N"), "mäktäp")
        self.assertEqual(convert_for_annotation("икмәк", "N"), "ikmäk")
        self.assertEqual(convert_for_annotation("икмәге", "N"), "ikmäge")
        self.assertEqual(convert_for_annotation("актүш", "N"), "aqtüş")
        self.assertEqual(convert_for_annotation("бакыр", "N"), "baqır")
        self.assertEqual(convert_for_annotation("гасыр", "N"), "ğasır")
        self.assertEqual(convert_for_annotation("гөл", "N"), "göl")

    def test_native_k_g_use_kich_suffix_context(self) -> None:
        self.assertEqual(convert_for_annotation("ерткыч", "N"), "yırtqıç")
        self.assertEqual(convert_for_annotation("ачкыч", "N"), "açqıç")
        self.assertEqual(convert_for_annotation("күрсәткеч", "N"), "kürsätkeç")
        self.assertEqual(convert_for_annotation("хәлиткеч", "N"), "xälitkeç")

    def test_native_exlaq_stem_keeps_q_in_derivatives(self) -> None:
        cases = [
            ("әхлак", "äxlaq"),
            ("әхлаки", "äxlaqi"),
            ("әхлаксыз", "äxlaqsız"),
            ("әхлаксызлык", "äxlaqsızlıq"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_native_surname_stems_keep_origin_stem_and_surname_v(self) -> None:
        cases = [
            ("гилемханов", "ğilemxanov"),
            ("гилмиев", "ğilmiev"),
            ("гәлимов", "ğälimov"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_native_garep_stem_uses_arabic_origin_g(self) -> None:
        self.assertEqual(
            convert_for_annotation("гәрәпчә-татарча", "N"),
            "ğäräpçä-tatarça",
        )
        self.assertEqual(
            convert_for_annotation_dsl("гәрәпчә-татарча", "N"),
            "ğäräpçä-tatarça",
        )

    def test_loanword_g_stems_use_plain_g(self) -> None:
        self.assertEqual(convert_for_annotation("гараж", "RL"), "garaj")
        self.assertEqual(convert_for_annotation("газет", "RL"), "gazet")
        self.assertEqual(convert_for_annotation("график", "RL"), "grafik")
        self.assertEqual(convert_for_annotation("дифтонг", "RL"), "diftong")
        self.assertEqual(convert_for_annotation("джунгли", "RL"), "djungli")
        self.assertEqual(convert_for_annotation("географик", "RL"), "geografik")
        self.assertEqual(convert_for_annotation("интрига", "RL"), "intriga")

    def test_loanword_stems_with_tatar_suffixes_use_suffix_gk(self) -> None:
        cases = [
            ("авторлыгын", "avtorlığın"),
            ("графлык", "graflıq"),
            ("коллективтагы", "kollektivtağı"),
            ("маскировкаланмаган", "maskirovkalanmağan"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "RL"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "RL"), expected)

    def test_loanword_stems_with_native_mixed_suffixes(self) -> None:
        cases = [
            ("закон", "zakon"),
            ("законсыз", "zakonsız"),
            ("закончалыклар", "zakonçalıqlar"),
            ("закончалыклары", "zakonçalıqları"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "RL"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "RL"), expected)

    def test_hyphenated_loanwords_guess_native_tatar_parts(self) -> None:
        cases = [
            ("киловатт-сәгәт", "kilovatt-säğät"),
            ("фәнни-публицистик", "fänni-publitsistik"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "RL"), expected)
                self.assertEqual(resolve_dsl(convert_for_annotation_dsl(word, "RL")), expected)

    def test_loanword_final_ka_is_policy_dsl(self) -> None:
        cases = [
            ("кубка", "kub{{RL_FINAL_KA|suffix=q|stem=k}}a", "kubqa", "kubka"),
            ("булавка", "bulav{{RL_FINAL_KA|suffix=q|stem=k}}a", "bulavqa", "bulavka"),
            ("палатка", "palat{{RL_FINAL_KA|suffix=q|stem=k}}a", "palatqa", "palatka"),
            ("форсунка", "forsun{{RL_FINAL_KA|suffix=q|stem=k}}a", "forsunqa", "forsunka"),
            (
                "фотоплёнка",
                "fotopl{{RUS_JOTATION|glide=y|apostrophe=ʼ|plain=}}on{{RL_FINAL_KA|suffix=q|stem=k}}a",
                "fotoplyonqa",
                "fotoplʼonka",
            ),
        ]

        for word, expected_dsl, suffix, stem in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "RL")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl), suffix)
                self.assertEqual(
                    resolve_dsl(
                        dsl,
                        {
                            "RUS_JOTATION": "apostrophe",
                            "RL_FINAL_KA": "stem",
                        },
                    ),
                    stem,
                )

    def test_short_loanword_final_ka_policy_is_narrow(self) -> None:
        for word in ["маска", "папка", "рамка"]:
            with self.subTest(word=word):
                self.assertNotIn("RL_FINAL_KA", convert_for_annotation_dsl(word, "RL"))

    def test_conflicting_arabic_initial_ga_uses_plain_preferred_form(self) -> None:
        cases = [
            ("гади", "ğadi"),
            ("гадиләштерергә", "ğadiläşterergä"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "N")
                self.assertEqual(dsl, expected)
                self.assertEqual(resolve_dsl(dsl), expected)

    def test_coherent_arabic_initial_ga_fronting_is_deterministic(self) -> None:
        cases = [
            ("гадәт", "ğädät"),
            ("гаеп", "ğäyep"),
            ("гаярь", "ğäyär"),
            ("гаять", "ğäyät"),
            ("гаскәр", "ğäskär"),
            ("гамәлдә", "ğämäldä"),
            ("гарип", "ğärip"),
            ("гасыр", "ğasır"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_selected_giy_compaction_is_deterministic(self) -> None:
        cases = [
            ("гыйбадәт", "ğibädät"),
            ("гыйбадәтханә", "ğibädätxanä"),
            ("гыйбарә", "ğibärä"),
            ("гыйльми", "ğilmi"),
            ("зәгыйфь", "zäğif"),
            ("шагыйрь", "şağir"),
            ("кагыйдә", "qağidä"),
            ("табигый", "tabiği"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_non_compacting_giy_words_stay_plain(self) -> None:
        self.assertEqual(convert_for_annotation_dsl("гыйбрәтле", "N"), "ğıybrätle")
        self.assertEqual(convert_for_annotation_dsl("гыйсъян", "N"), "ğıysyan")
        self.assertEqual(convert_for_annotation_dsl("гыйшык", "N"), "ğıyşıq")

    def test_selected_arabic_final_at_fronting_is_deterministic(self) -> None:
        cases = [
            ("васыять", "wasıyät"),
            ("итагатьсез", "itağätsez"),
            ("канәгатьләндерергә", "qanäğätländerergä"),
            ("риваять", "riwayät"),
            ("сәгать", "säğät"),
            ("сәнгате", "sänğäte"),
            ("табигатьтән", "tabiğättän"),
            ("җәмәгать", "cämäğät"),
            ("мөрәҗәгать", "möräcäğät"),
            ("җинаятьчелек", "cinayätçelek"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_jamgiyat_stem_has_compact_pdf_iya_policy(self) -> None:
        cases = [
            ("җәмгыять", "cämği{{IYA|compact=ä|explicit=yä}}t", "cämğiyät", "cämğiät"),
            ("җәмгыяте", "cämği{{IYA|compact=ä|explicit=yä}}te", "cämğiyäte", "cämğiäte"),
            (
                "җәмгыятьтәге",
                "cämği{{IYA|compact=ä|explicit=yä}}ttäge",
                "cämğiyättäge",
                "cämğiättäge",
            ),
        ]

        for word, expected_dsl, explicit, compact in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "N")
                self.assertEqual(convert_for_annotation(word, "N"), explicit)
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl), explicit)
                self.assertEqual(resolve_dsl(dsl, {"IYA": "compact"}), compact)

    def test_selected_arabic_hamza_stems_are_deterministic(self) -> None:
        cases = [
            ("таэмин", "täʼmin"),
            ("тәэмин", "täʼmin"),
            ("тәэсир", "täʼsir"),
            ("тәэсирендә", "täʼsirendä"),
            ("тәэсиргә", "täʼsirgä"),
            ("тәэсирле", "täʼsirle"),
            ("тәэсирлелек", "täʼsirlelek"),
            ("тәэсирләнергә", "täʼsirlänergä"),
            ("тәэсирләнүчән", "täʼsirlänüçän"),
            ("тәэсирләнүчәнлек", "täʼsirlänüçänlek"),
            ("тәэсирсез", "täʼsirsez"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_native_ek_to_iyq_words_are_deterministic(self) -> None:
        cases = [
            ("аек", "ayıq"),
            ("боек", "boyıq"),
            ("каек", "qayıq"),
            ("каекчы", "qayıqçı"),
            ("кыек", "qıyıq"),
            ("лаек", "layıq"),
            ("лаеклы", "layıqlı"),
            ("мыек", "mıyıq"),
            ("оек", "oyıq"),
            ("оекбашлар", "oyıqbaşlar"),
            ("оешкан", "oyışqan"),
            ("оешканлык", "oyışqanlıq"),
            ("сыек", "sıyıq"),
            ("сыекланырга", "sıyıqlanırğa"),
            ("сыеклык", "sıyıqlıq"),
            ("сыекча", "sıyıqça"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_soyak_stem_keeps_k_deterministically(self) -> None:
        cases = [
            ("сөяк", "söyäk"),
            ("сөяккә", "söyäkkä"),
            ("сөякле", "söyäkle"),
            ("аксөякләр", "aqsöyäklär"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_toyak_stem_keeps_k_deterministically_in_compounds(self) -> None:
        cases = [
            ("вак-төяк", "waq-töyäk"),
            ("көньяк-көнчыгыш", "könyaq-könçığış"),
            ("карлы-яңгырлы", "qarlı-yañğırlı"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_front_ya_k_stems_keep_k_deterministically(self) -> None:
        cases = [
            ("гүяки", "güyäki"),
            ("өянке", "öyänke"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_har_hich_qay_stems_are_deterministic(self) -> None:
        cases = [
            ("һичкайда", "hiçqayda"),
            ("һичкая", "hiçqaya"),
            ("һәркайда", "härqayda"),
            ("һәркайсы", "härqaysı"),
            ("һәркайсында", "härqaysında"),
            ("һәркайчан", "härqayçan"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_ber_q_compounds_are_deterministic(self) -> None:
        cases = [
            ("беркайда", "berqayda"),
            ("беркайчан", "berqayçan"),
            ("беркая", "berqaya"),
            ("беркатлы", "berqatlı"),
            ("беркатлылык", "berqatlılıq"),
            ("берникадәр", "berniqadär"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_vazifa_arabic_stem_is_deterministic(self) -> None:
        cases = [
            ("вазифа", "wazıyfa"),
            ("вазифага", "wazıyfağa"),
            ("вазифалары", "wazıyfaları"),
            ("вазифаны", "wazıyfanı"),
            ("вазифасы", "wazıyfası"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_arabic_persian_k_to_q_stems_are_deterministic(self) -> None:
        cases = [
            ("дикъкать", "diqqat"),
            ("дикъкатьсез", "diqqatsez"),
            ("вәкаләт", "wäqalät"),
            ("инкарь", "inqar"),
            ("инкыйлаб", "inqıylab"),
            ("каракүл", "qarakül"),
            ("каракүлдән", "qaraküldän"),
            ("мәкаль", "mäqal"),
            ("мәкалә", "mäqalä"),
            ("мөкатдәс", "möqatdäs"),
            ("мәшәкать", "mäşäqat"),
            ("мәшәкатьле", "mäşäqatle"),
            ("мәшәкатьләр", "mäşäqatlär"),
            ("мәшәкатьләргә", "mäşäqatlärgä"),
            ("мәшәкатьләре", "mäşäqatläre"),
            ("нәкыш", "näqış"),
            ("сәркатип", "särqatip"),
            ("сәркатиплек", "särqatiplek"),
            ("тәкать", "täqat"),
            ("тәкатьле", "täqatle"),
            ("тәрәккый", "täräqqıy"),
            ("тәнкыйть", "tänqıyt"),
            ("тәнкыйтьләргә", "tänqıytlärgä"),
            ("тәнкыйтьче", "tänqıytçe"),
            ("тәшрик", "täşriq"),
            ("вакиф", "waqif"),
            ("фәкать", "fäqat"),
            ("фәкыйрь", "fäqıyr"),
            ("фәкыйрьлеккә", "fäqıyrlekkä"),
            ("фәкыйрьләнү", "fäqıyrlänü"),
            ("шәфкать", "şäfqat"),
            ("шәфкатьле", "şäfqatle"),
            ("шәфкатьлелек", "şäfqatlelek"),
            ("шәфкатьсез", "şäfqatsez"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_arabic_persian_ya_fronting_stems_are_deterministic(self) -> None:
        cases = [
            ("киная", "kinayä"),
            ("кинаяле", "kinayäle"),
            ("кинаяләп", "kinayäläp"),
            ("кыяфәт", "qıyäfät"),
            ("кыяфәте", "qıyäfäte"),
            ("кыяфәтле", "qıyäfätle"),
            ("кыяфәттә", "qıyäfättä"),
            ("хыянәт", "xıyänät"),
            ("хыянәтче", "xıyänätçe"),
            ("хыянәтчеләрчә", "xıyänätçelärçä"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_arabic_persian_gha_fronting_stems_are_deterministic(self) -> None:
        cases = [
            ("мөгаллим", "möğällim"),
            ("мөгамәлә", "möğämälä"),
            ("мөгамәләле", "möğämäläle"),
            ("пәйгамбәр", "päyğämbär"),
            ("пәйгамбәрлек", "päyğämbärlek"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_general_apostrophe_and_sign_conversions(self) -> None:
        self.assertEqual(convert_for_annotation("роль", "RL"), "rolʼ")
        self.assertEqual(convert_for_annotation("культура", "RL"), "kulʼtura")
        self.assertEqual(convert_for_annotation("секретарь", "RL"), "sekretarʼ")
        self.assertEqual(
            convert_for_annotation_dsl("роль", "RL"),
            "rol{{RUS_SIGN|omit=|preserve=ʼ}}",
        )
        self.assertEqual(
            convert_for_annotation_dsl("культура", "RL"),
            "kul{{RUS_SIGN|omit=|preserve=ʼ}}tura",
        )
        self.assertEqual(
            convert_for_annotation_dsl("секретарь", "RL"),
            "sekretar{{RUS_SIGN|omit=|preserve=ʼ}}",
        )
        self.assertEqual(
            convert_for_annotation_dsl("коньяк", "RL"),
            "kon{{RUS_SIGN|omit=|preserve=ʼ}}yak",
        )
        self.assertEqual(
            convert_for_annotation_dsl("тальян", "RL"),
            "tal{{RUS_SIGN|omit=|preserve=ʼ}}yan",
        )
        self.assertEqual(
            convert_for_annotation_dsl("объективлык", "RL"),
            "ob{{RUS_SIGN_E|glide=y|apostrophe=ʼ|apostrophe_glide=ʼy}}ektivlıq",
        )
        self.assertEqual(
            resolve_dsl(
                convert_for_annotation_dsl("объективлык", "RL"),
                {"RUS_SIGN_E": "apostrophe"},
            ),
            "obʼektivlıq",
        )
        self.assertEqual(
            resolve_dsl(
                convert_for_annotation_dsl("ателье", "RL"),
                {"RUS_SIGN_E": "apostrophe_glide"},
            ),
            "atelʼye",
        )
        self.assertEqual(
            convert_for_annotation_dsl("батальон", "RL"),
            "batal{{RUS_SOFT_SIGN_O|omit=|preserve=ʼ|apostrophe_y=ʼy}}on",
        )
        self.assertEqual(resolve_dsl(convert_for_annotation_dsl("батальон", "RL")), "batalʼon")
        self.assertEqual(
            resolve_dsl(
                convert_for_annotation_dsl("батальон", "RL"),
                {"RUS_SOFT_SIGN_O": "apostrophe_y"},
            ),
            "batalʼyon",
        )
        self.assertEqual(
            resolve_dsl(
                convert_for_annotation_dsl("почтальон", "RL"),
                {"RUS_SOFT_SIGN_O": "apostrophe_y"},
            ),
            "poçtalʼyon",
        )

    def test_russian_jotated_softening_is_policy_dsl(self) -> None:
        cases = [
            ("бюро", "b{{RUS_JOTATION|glide=y|apostrophe=ʼ|plain=}}uro", "byuro", "bʼuro"),
            ("вафля", "vafl{{RUS_JOTATION|glide=y|apostrophe=ʼ|plain=}}a", "vaflya", "vaflʼa"),
            ("шофёр", "şof{{RUS_JOTATION|glide=y|apostrophe=ʼ|plain=}}or", "şofyor", "şofʼor"),
        ]

        for word, expected_dsl, glide, apostrophe in cases:
            with self.subTest(word=word):
                dsl = convert_for_annotation_dsl(word, "RL")
                self.assertEqual(dsl, expected_dsl)
                self.assertEqual(resolve_dsl(dsl), glide)
                self.assertEqual(
                    resolve_dsl(dsl, {"RUS_JOTATION": "apostrophe"}),
                    apostrophe,
                )

    def test_russian_shch_yo_is_narrow_policy_dsl(self) -> None:
        dsl = convert_for_annotation_dsl("щётка", "RL")

        self.assertEqual(dsl, "şç{{RUS_JOTATION|glide=y|apostrophe=ʼ|plain=}}otka")
        self.assertEqual(resolve_dsl(dsl), "şçyotka")
        self.assertEqual(resolve_dsl(dsl, {"RUS_JOTATION": "apostrophe"}), "şçʼotka")
        self.assertEqual(resolve_dsl(dsl, {"RUS_JOTATION": "plain"}), "şçotka")

    def test_russian_jotated_softening_composes_with_iya(self) -> None:
        dsl = convert_for_annotation_dsl("бюрократия", "RL")

        self.assertEqual(
            dsl,
            "b{{RUS_JOTATION|glide=y|apostrophe=ʼ|plain=}}urokrati{{IYA|compact=ä|explicit=yä}}",
        )
        self.assertEqual(resolve_dsl(dsl), "byurokratiyä")
        self.assertEqual(
            resolve_dsl(
                dsl,
                {"RUS_JOTATION": "apostrophe", "IYA": "explicit"},
            ),
            "bʼurokratiyä",
        )

    def test_russian_jotated_softening_composes_with_iya_and_suffixes(self) -> None:
        dsl = convert_for_annotation_dsl("изоляцияләү", "RL")

        self.assertEqual(
            dsl,
            "izol{{RUS_JOTATION|glide=y|apostrophe=ʼ|plain=}}atsi{{IYA|compact=ä|explicit=yä}}läw",
        )
        self.assertEqual(resolve_dsl(dsl), "izolyatsiyäläw")
        self.assertEqual(
            resolve_dsl(
                dsl,
                {"RUS_JOTATION": "apostrophe", "IYA": "explicit"},
            ),
            "izolʼatsiyäläw",
        )

    def test_russian_bu_front_is_deterministic(self) -> None:
        dsl = convert_for_annotation_dsl("вестибюль", "RL")

        self.assertEqual(
            dsl,
            "vestibyul{{RUS_SIGN|omit=|preserve=ʼ}}",
        )
        self.assertEqual(resolve_dsl(dsl), "vestibyulʼ")

    def test_russian_soft_sign_composes_with_final_ka_policy(self) -> None:
        dsl = convert_for_annotation_dsl("геральдика", "RL")

        self.assertEqual(
            dsl,
            "geral{{RUS_SIGN|omit=|preserve=ʼ}}di{{RL_FINAL_KA|suffix=q|stem=k}}a",
        )
        self.assertEqual(resolve_dsl(dsl), "geralʼdiqa")
        self.assertEqual(
            resolve_dsl(dsl, {"RUS_SIGN": "preserve", "RL_FINAL_KA": "stem"}),
            "geralʼdika",
        )

    def test_russian_jotated_softening_composes_with_final_soft_sign(self) -> None:
        dsl = convert_for_annotation_dsl("князь", "RL")

        self.assertEqual(
            dsl,
            "kn{{RUS_JOTATION|glide=y|apostrophe=ʼ|plain=}}az{{RUS_SIGN|omit=|preserve=ʼ}}",
        )
        self.assertEqual(resolve_dsl(dsl), "knyazʼ")
        self.assertEqual(
            resolve_dsl(
                dsl,
                {"RUS_JOTATION": "apostrophe", "RUS_SIGN": "omit"},
            ),
            "knʼaz",
        )

    def test_native_miyaw_stem_is_deterministic(self) -> None:
        cases = [
            ("мияубикә", "miyawbikä"),
            ("мияулап", "miyawlap"),
            ("мияуларга", "miyawlarğa"),
            ("мияулау", "miyawlaw"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_miyaw_stem_rule_does_not_rewrite_other_iya_u_words(self) -> None:
        self.assertEqual(
            convert_for_annotation_dsl("кияү", "N"),
            "ki{{IYA|compact=ä|explicit=yä}}w",
        )
        self.assertEqual(
            convert_for_annotation_dsl("тәрбияви", "N"),
            "tärbi{{IYA|compact=ä|explicit=yä}}wi",
        )

    def test_reviewed_yu_conversions(self) -> None:
        self.assertEqual(convert_for_annotation("революция", "RL"), "revolyutsiä")
        self.assertEqual(convert_for_annotation("революциясе", "RL"), "revolyutsiäse")
        self.assertEqual(convert_for_annotation("тию", "N"), "tiyü")

    def test_loanword_stems_with_native_lau_suffix_use_w(self) -> None:
        cases = [
            ("аннотацияләү", "annotatsi{{IYA|compact=ä|explicit=yä}}läw"),
            ("реабилитацияләү", "reabilitatsi{{IYA|compact=ä|explicit=yä}}läw"),
            ("регистрацияләү", "registratsi{{IYA|compact=ä|explicit=yä}}läw"),
            ("колонизацияләү", "kolonizatsi{{IYA|compact=ä|explicit=yä}}läw"),
        ]

        for word, expected_dsl in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation_dsl(word, "RL"), expected_dsl)

    def test_native_yu_uses_local_vowel_context(self) -> None:
        cases = [
            ("ерагаю", "yırağayu"),
            ("югыйсә", "yuğisä"),
            ("юк", "yuq"),
            ("юк-бар", "yuq-bar"),
            ("юка", "yuqa"),
            ("юкә", "yükä"),
        ]

        for word, expected in cases:
            with self.subTest(word=word):
                self.assertEqual(convert_for_annotation(word, "N"), expected)
                self.assertEqual(convert_for_annotation_dsl(word, "N"), expected)

    def test_origin_dependent_hints_show_letter_decisions_without_branch_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {"id": "1", "tatar": True, "tokens": [{"text": "проект", "label": "RL"}]},
                    {"id": "2", "tatar": True, "tokens": [{"text": "вакыт", "label": "N"}]},
                    {"id": "3", "tatar": True, "tokens": [{"text": "гасыр", "label": "N"}]},
                ],
            )

            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")

        html_by_word = {
            task["data"]["cyrl_word"]: task["data"]["hints_html"] for task in result.tasks
        }
        self.assertIn("<b>е</b> -> <b>ye</b>", html_by_word["проект"])
        self.assertIn("<b>в</b> -> <b>w</b>", html_by_word["вакыт"])
        self.assertIn("<b>г</b> -> <b>ğ</b>", html_by_word["гасыр"])
        for html in html_by_word.values():
            self.assertNotIn("Native branch:", html)
            self.assertNotIn("Loanword branch:", html)
            self.assertNotIn("because of", html)

    def test_sorting_frequency_limit_and_min_frequency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {"id": "1", "tatar": True, "tokens": [{"text": "авыл", "label": "N"}]},
                    {"id": "2", "tatar": True, "tokens": [{"text": "авыл", "label": "N"}]},
                    {"id": "3", "tatar": True, "tokens": [{"text": "вакыт", "label": "N"}]},
                ],
            )

            limited = export_labelstudio_tasks_from_db(db_path, max_items=1)
            frequent = export_labelstudio_tasks_from_db(db_path, min_frequency=2)

        self.assertEqual([task["data"]["cyrl_word"] for task in limited.tasks], ["авыл"])
        self.assertEqual([task["data"]["cyrl_word"] for task in frequent.tasks], ["авыл"])

    def test_cli_rejects_removed_single_file_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [{"text": "вакыт", "label": "N"}],
                    }
                ],
            )
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "annotation-export",
                        "--db",
                        str(db_path),
                        "--output",
                        str(Path(tmpdir) / "out.json"),
                    ]
                )

        self.assertEqual(raised.exception.code, 2)

    def test_split_export_groups_by_priority_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "орфография", "label": "RL"},
                            {"text": "сыр", "label": "RL"},
                            {"text": "объективлык", "label": "RL"},
                            {"text": "вакыт", "label": "N"},
                        ],
                    }
                ],
            )

            result = export_labelstudio_project_tasks_from_db(db_path, sort_by="word")

        self.assertIn("iya", result.projects)
        self.assertIn("rl_y", result.projects)
        self.assertIn("rus_sign_e", result.projects)
        self.assertIn("catchall", result.projects)
        self.assertEqual(
            result.projects["iya"].tasks[0]["data"]["project_key"],
            "iya",
        )
        self.assertEqual(
            result.projects["iya"].tasks[0]["data"]["dsl_rules"],
            ["IYA"],
        )
        self.assertEqual(
            result.projects["catchall"].tasks[0]["data"]["cyrl_word"],
            "вакыт",
        )
        self.assertEqual(result.report["exported_word_count"], 4)

    def test_split_export_uses_complex_multi_rule_project(self) -> None:
        project = classify_project("бюрократия", "RL")

        self.assertEqual(project["key"], "complex_multi_rule")
        self.assertEqual(project["dsl_rules"], ["RUS_JOTATION", "IYA"])

    def test_split_export_routes_literal_and_policy_hamza_out_of_catchall(self) -> None:
        cases = {
            "маэмай": [],
            "тәэмин": [],
            "тәэсир": [],
            "коръән": ["HAMZA"],
        }

        for word, rules in cases.items():
            with self.subTest(word=word):
                project = classify_project(word, "N")
                self.assertEqual(project["key"], "hamza")
                self.assertEqual(project["title"], "Hamza review")
                self.assertEqual(project["dsl_rules"], rules)

    def test_russian_apostrophe_is_not_routed_as_hamza(self) -> None:
        project = classify_project("культура", "RL")

        self.assertEqual(project["key"], "rus_sign")
        self.assertEqual(project["dsl_rules"], ["RUS_SIGN"])

    def test_literal_hamza_project_writes_dedicated_output_and_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [{"text": "Тәэмин", "label": "N"}],
                    }
                ],
            )
            output_dir = Path(tmpdir) / "projects"

            result = export_labelstudio_project_tasks_from_db(db_path)
            write_split_outputs(result, output_dir)
            tasks = json.loads(
                (output_dir / "project_hamza_batch_001_of_001.json").read_text(
                    encoding="utf-8"
                )
            )
            instructions = (
                output_dir / "project_hamza_instructions.html"
            ).read_text(encoding="utf-8")

        self.assertEqual(set(result.projects), {"hamza"})
        self.assertEqual(tasks[0]["data"]["project_key"], "hamza")
        self.assertEqual(tasks[0]["data"]["auto_zamanalif"], "täʼmin")
        self.assertIn("Arabic/Persian hamza", instructions)

    def test_split_export_counts_distinct_rules_not_repeated_occurrences(self) -> None:
        project = classify_project("социаль-икътисадый", "RL")

        self.assertEqual(project["key"], "rus_sign")
        self.assertEqual(project["dsl_rules"], ["RUS_SIGN"])

    def test_split_export_routes_unknown_words_to_focused_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "УУГ", "label": "U"},
                            {"text": "торак-коммуналь", "label": "U"},
                            {"text": "күпфункцияле", "label": "U"},
                            {"text": "видеоязма", "label": "U"},
                            {"text": "альфонс", "label": "U"},
                            {"text": "вакыт", "label": "N"},
                        ],
                    }
                ],
            )

            result = export_labelstudio_project_tasks_from_db(db_path, sort_by="word")

        self.assertIn("u_abbrev_fragment", result.projects)
        self.assertIn("u_hyphenated", result.projects)
        self.assertIn("u_tatar_specific", result.projects)
        self.assertIn("u_conditional_plain", result.projects)
        self.assertIn("u_other", result.projects)
        self.assertEqual(
            result.projects["u_abbrev_fragment"].tasks[0]["data"]["cyrl_word"],
            "УУГ",
        )
        self.assertEqual(
            result.projects["u_abbrev_fragment"].tasks[0]["data"]["project_title"],
            "Unknown abbreviations and fragments",
        )
        self.assertEqual(
            result.projects["u_hyphenated"].tasks[0]["data"]["cyrl_word"],
            "торак-коммуналь",
        )
        self.assertEqual(
            result.projects["u_tatar_specific"].tasks[0]["data"]["cyrl_word"],
            "күпфункцияле",
        )
        self.assertEqual(
            result.projects["u_conditional_plain"].tasks[0]["data"]["cyrl_word"],
            "видеоязма",
        )
        self.assertEqual(
            result.projects["u_other"].tasks[0]["data"]["cyrl_word"],
            "альфонс",
        )
        self.assertEqual(
            result.projects["catchall"].tasks[0]["data"]["cyrl_word"],
            "вакыт",
        )

    def test_cli_writes_split_labelstudio_json_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "орфография", "label": "RL"},
                            {"text": "вакыт", "label": "N"},
                        ],
                    }
                ],
            )
            output_dir = Path(tmpdir) / "split"
            output_dir.mkdir()
            stale_batch = output_dir / "project_iya_batch_001_of_999.json"
            stale_batch.write_text("[]\n", encoding="utf-8")
            legacy_export = output_dir / "project_iya.json"
            legacy_export.write_text("[]\n", encoding="utf-8")
            unrelated = output_dir / "notes.txt"
            unrelated.write_text("keep me\n", encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "annotation-export",
                        "--db",
                        str(db_path),
                        "--output-dir",
                        str(output_dir),
                        "--sort-by",
                        "word",
                    ]
                )

            iya = json.loads(
                (output_dir / "project_iya_batch_001_of_001.json").read_text(encoding="utf-8")
            )
            catchall = json.loads(
                (output_dir / "project_catchall_batch_001_of_001.json").read_text(
                    encoding="utf-8"
                )
            )
            iya_instructions = (
                output_dir / "project_iya_instructions.html"
            ).read_text(encoding="utf-8")
            catchall_instructions = (
                output_dir / "project_catchall_instructions.html"
            ).read_text(encoding="utf-8")
            report_files_exist = any(
                (
                    (output_dir / "project_iya.report.json").exists(),
                    (output_dir / "project_catchall.report.json").exists(),
                    (output_dir / "summary_report.json").exists(),
                )
            )
            stale_exists = stale_batch.exists()
            legacy_exists = legacy_export.exists()
            unrelated_text = unrelated.read_text(encoding="utf-8")
            inactive_instructions_exist = (
                output_dir / "project_ts_instructions.html"
            ).exists()

        self.assertEqual(exit_code, 0)
        self.assertEqual(iya[0]["data"]["project_key"], "iya")
        self.assertEqual(iya[0]["data"]["batch_id"], "iya_batch_001")
        self.assertEqual(iya[0]["data"]["batch_index"], 1)
        self.assertEqual(iya[0]["data"]["batch_total"], 1)
        self.assertEqual(catchall[0]["data"]["project_key"], "catchall")
        self.assertFalse(report_files_exist)
        self.assertFalse(stale_exists)
        self.assertFalse(legacy_exists)
        self.assertEqual(unrelated_text, "keep me\n")
        self.assertIn("orfografiä", iya_instructions)
        self.assertIn("orfografiyä", iya_instructions)
        self.assertIn("вакыт → waqıt", catchall_instructions)
        self.assertFalse(inactive_instructions_exist)
        self.assertIn("annotation export complete", output.getvalue())

    def test_export_validation_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "вакыт", "label": "N"},
                            {"text": "проект", "label": "RL"},
                        ],
                    }
                ],
            )
            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")
            result.tasks[1]["data"]["id"] = result.tasks[0]["data"]["id"]

            with self.assertRaisesRegex(AnnotationExportError, "duplicate task id"):
                validate_export_result(result)

    def test_export_validation_allows_empty_u_suggestion_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "торак", "label": "U"},
                            {"text": "вакыт", "label": "N"},
                        ],
                    }
                ],
            )
            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")
            validate_export_result(result)
            known = next(
                task
                for task in result.tasks
                if task["data"]["gemini_origin"] == "N"
            )
            known["data"]["auto_zamanalif"] = ""

            with self.assertRaisesRegex(
                AnnotationExportError,
                "empty suggestion for origin N",
            ):
                validate_export_result(result)

    def test_split_validation_rejects_dsl_in_catchall(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [{"text": "вакыт", "label": "N"}],
                    }
                ],
            )
            result = export_labelstudio_project_tasks_from_db(db_path)
            result.projects["catchall"].tasks[0]["data"]["auto_zamanalif"] = (
                "waqı{{TS|s=s|ts=ts}}t"
            )

            with self.assertRaisesRegex(
                AnnotationExportError,
                "suggestion does not match canonical conversion",
            ):
                validate_split_export_result(result)

    def test_tracking_is_not_updated_when_split_write_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [{"text": "вакыт", "label": "N"}],
                    }
                ],
            )
            output_dir = Path(tmpdir) / "split"
            with patch(
                "tatar_preannotator.cli.write_split_outputs",
                side_effect=AnnotationExportError("invalid generated export"),
            ):
                exit_code = main(
                    [
                        "annotation-export",
                        "--db",
                        str(db_path),
                        "--output-dir",
                        str(output_dir),
                        "--track-exported",
                    ]
                )

            tracked = load_exported_words(db_path)

        self.assertEqual(exit_code, 1)
        self.assertEqual(tracked, set())

    def test_split_export_is_deterministic_when_state_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "орфография", "label": "RL"},
                            {"text": "вакыт", "label": "N"},
                        ],
                    }
                ],
            )
            output_dir = Path(tmpdir) / "split"
            first_exit = main(
                [
                    "annotation-export",
                    "--db",
                    str(db_path),
                    "--output-dir",
                    str(output_dir),
                ]
            )
            first_files = {
                path.name: path.read_bytes()
                for path in output_dir.iterdir()
                if path.is_file()
            }
            second_exit = main(
                [
                    "annotation-export",
                    "--db",
                    str(db_path),
                    "--output-dir",
                    str(output_dir),
                ]
            )
            second_files = {
                path.name: path.read_bytes()
                for path in output_dir.iterdir()
                if path.is_file()
            }

        self.assertEqual(first_exit, 0)
        self.assertEqual(second_exit, 0)
        self.assertEqual(first_files, second_files)

    def test_split_cli_batches_large_projects_at_1000_tasks(self) -> None:
        letters = "бдмнрст"
        words = [
            "вакыт"
            + letters[(index // 343) % 7]
            + letters[(index // 49) % 7]
            + letters[(index // 7) % 7]
            + letters[index % 7]
            for index in range(1001)
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [{"text": word, "label": "N"} for word in words],
                    }
                ],
            )
            output_dir = Path(tmpdir) / "split"

            exit_code = main(
                [
                    "annotation-export",
                    "--db",
                    str(db_path),
                    "--output-dir",
                    str(output_dir),
                    "--sort-by",
                    "word",
                ]
            )

            first_path = output_dir / "project_catchall_batch_001_of_002.json"
            second_path = output_dir / "project_catchall_batch_002_of_002.json"
            first = json.loads(first_path.read_text(encoding="utf-8"))
            second = json.loads(second_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(first), 1000)
        self.assertEqual(len(second), 1)
        self.assertEqual(first[0]["data"]["batch_id"], "catchall_batch_001")
        self.assertEqual(first[0]["data"]["batch_index"], 1)
        self.assertEqual(first[0]["data"]["batch_total"], 2)
        self.assertEqual(second[0]["data"]["batch_id"], "catchall_batch_002")
        self.assertEqual(second[0]["data"]["batch_index"], 2)
        self.assertEqual(second[0]["data"]["batch_total"], 2)

    def test_split_cli_tracking_marks_all_exported_words(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "орфография", "label": "RL"},
                            {"text": "вакыт", "label": "N"},
                        ],
                    }
                ],
            )
            output_dir = Path(tmpdir) / "split"

            first = main(
                [
                    "annotation-export",
                    "--db",
                    str(db_path),
                    "--output-dir",
                    str(output_dir),
                    "--track-exported",
                ]
            )
            second = export_labelstudio_project_tasks_from_db(
                db_path,
                already_exported=load_exported_words(db_path),
            )

        self.assertEqual(first, 0)
        self.assertEqual(second.exported_words, [])

    def test_labelstudio_round_trip_removes_reviewed_word_from_export(self) -> None:
        from tatar_preannotator.labelstudio_import import import_labelstudio_annotations

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = _write_annotation_db(
                root / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [{"text": "вакыт", "label": "N"}],
                    }
                ],
            )
            first = export_labelstudio_tasks_from_db(db_path)
            task = first.tasks[0]
            labelstudio_task = {
                **task,
                "data": {
                    **task["data"],
                    "project_key": "catchall",
                    "project_title": "Catchall word review",
                    "batch_id": "catchall_batch_001",
                    "batch_index": 1,
                    "batch_total": 1,
                },
                "annotations": [
                    {
                        "was_cancelled": False,
                        "result": [
                            {
                                "from_name": "reviewed_origin",
                                "type": "choices",
                                "value": {"choices": ["N"]},
                            },
                            {
                                "from_name": "corrected_zamanalif",
                                "type": "textarea",
                                "value": {"text": ["waqıt"]},
                            },
                        ],
                    }
                ],
            }
            annotation_path = root / "completed.json"
            annotation_path.write_text(
                json.dumps({"tasks": [labelstudio_task]}, ensure_ascii=False),
                encoding="utf-8",
            )

            summary = import_labelstudio_annotations(db_path, annotation_path)
            second = export_labelstudio_tasks_from_db(db_path)

        self.assertEqual(summary.imported_items, 1)
        self.assertEqual(second.exported_words, [])

    def test_exports_from_sqlite_annotation_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "вакыт", "label": "N"},
                            {"text": "турында", "label": "N"},
                        ],
                    }
                ],
            )

            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")

        self.assertEqual(
            [task["data"]["cyrl_word"] for task in result.tasks],
            ["вакыт"],
        )

    def test_sqlite_tracking_skips_previously_exported_words(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            selected_db = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {
                        "id": "sent_1",
                        "tatar": True,
                        "tokens": [
                            {"text": "вакыт", "label": "N"},
                            {"text": "авыл", "label": "N"},
                        ],
                    }
                ],
            )
            db_path = Path(tmpdir) / "state.sqlite"
            mark_exported_words(db_path, ["вакыт"])

            result = export_labelstudio_tasks_from_db(
                selected_db,
                sort_by="word",
                already_exported=load_exported_words(db_path),
            )

            with sqlite3.connect(db_path) as conn:
                count = conn.execute("select count(*) from exported_words").fetchone()[0]

        self.assertEqual([task["data"]["cyrl_word"] for task in result.tasks], ["авыл"])
        self.assertEqual(result.report["already_exported_skipped_count"], 1)
        self.assertEqual(count, 1)


def _write_annotation_db(path: Path, rows: list[dict]) -> Path:
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
        for row in rows:
            sample_id = row["id"]
            conn.execute(
                "insert into samples(id, source_id, text) values (?, ?, ?)",
                (sample_id, "src", row.get("text", "")),
            )
            conn.execute(
                """
                insert into preannotation_state(
                    sample_id, status, tatar, tokens_json, updated_at
                ) values (?, ?, ?, ?, ?)
                """,
                (
                    sample_id,
                    row.get("status", "annotated"),
                    1 if row.get("tatar") else 0,
                    json.dumps(row.get("tokens", []), ensure_ascii=False),
                    "2026-01-01T00:00:00+00:00",
                ),
            )
    return path


if __name__ == "__main__":
    unittest.main()
