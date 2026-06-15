from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tatar_preannotator.cli import main
from tatar_preannotator.word_export import (
    contains_conditional_letter,
    contains_rl_review_letter,
    convert_for_annotation,
    export_labelstudio_tasks_from_db,
    load_exported_words,
    mark_exported_words,
    normalize_word,
    vowel_harmony_class,
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
            [
                "вакытында",
                "дөрес",
                "позиция",
                "проект",
                "сер",
                "сүз",
                "турында",
                "яңа",
                "әйттем",
            ],
        )
        self.assertEqual(result.tasks[0]["data"]["auto_zamanalif"], "waqıtında")
        self.assertEqual(result.tasks[2]["data"]["auto_zamanalif"], "pozitsiä")
        self.assertEqual(result.tasks[3]["data"]["auto_zamanalif"], "proekt")
        self.assertEqual(result.tasks[7]["data"]["auto_zamanalif"], "yaña")
        self.assertEqual(result.report["mixed_harmony_n_word_skipped_count"], 1)
        self.assertEqual(result.report["u_exported_word_count"], 1)

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
        self.assertEqual(result.tasks[1]["data"]["auto_zamanalif"], "proekt")

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
        self.assertEqual(by_word["сыр"]["auto_zamanalif"], "sıyr")
        self.assertEqual(by_word["роль"]["auto_zamanalif"], "rol'")
        self.assertEqual(by_word["шофёр"]["auto_zamanalif"], "şofyor")
        self.assertIn("<b>ы</b> -> <b>ıy</b>", by_word["сыр"]["hints_html"])
        self.assertIn("<b>ь</b> -> <b>&#x27;</b>", by_word["роль"]["hints_html"])
        self.assertIn("<b>ё</b> -> <b>yo</b>", by_word["шофёр"]["hints_html"])

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
        self.assertEqual(convert_for_annotation("проект", "RL"), "proekt")
        self.assertEqual(convert_for_annotation("яңа", "N"), "yaña")

    def test_ya_conversion_context_rules(self) -> None:
        self.assertEqual(convert_for_annotation("әдәбият", "N"), "ädäbiät")
        self.assertEqual(convert_for_annotation("позиция", "RL"), "pozitsiä")
        self.assertEqual(convert_for_annotation("фамилия", "N"), "familiä")
        self.assertEqual(convert_for_annotation("яңалиф", "N"), "yañalif")
        self.assertEqual(convert_for_annotation("яшь", "N"), "yäş")
        self.assertEqual(convert_for_annotation("яки", "RL"), "yäki")
        self.assertEqual(convert_for_annotation("дөнья", "N"), "dönya")
        self.assertEqual(convert_for_annotation("көньяк", "N"), "könyaq")
        self.assertEqual(convert_for_annotation("һәръяклап", "N"), "häryaqlap")
        self.assertEqual(convert_for_annotation("ладья", "RL"), "ladya")

    def test_month_names_follow_pdf_reference_spellings(self) -> None:
        self.assertEqual(convert_for_annotation("гыйнвар", "N"), "ğinwar")
        self.assertEqual(convert_for_annotation("февраль", "RL"), "fevral")
        self.assertEqual(convert_for_annotation("июнь", "N"), "iyün")
        self.assertEqual(convert_for_annotation("июль", "N"), "iyül")
        self.assertEqual(convert_for_annotation("октябрь", "RL"), "oktäbr")
        self.assertEqual(convert_for_annotation("октябрендә", "RL"), "oktäbrendä")
        self.assertEqual(convert_for_annotation("сентябрь", "RL"), "sentäbr")
        self.assertEqual(convert_for_annotation("сентябреннән", "RL"), "sentäbrennän")
        self.assertEqual(convert_for_annotation("ноябрь", "N"), "noyäbr")
        self.assertEqual(convert_for_annotation("декабрь", "N"), "dekäbr")

    def test_e_conversion_uses_pdf_context_rules(self) -> None:
        self.assertEqual(convert_for_annotation("электр", "RL"), "elektr")
        self.assertEqual(convert_for_annotation("телефон", "RL"), "telefon")
        self.assertEqual(convert_for_annotation("билет", "RL"), "bilet")
        self.assertEqual(convert_for_annotation("нуриев", "RL"), "nuriev")
        self.assertEqual(convert_for_annotation("объект", "RL"), "obyekt")
        self.assertEqual(convert_for_annotation("гәрәев", "RL"), "gäräyev")
        self.assertEqual(convert_for_annotation("егет", "N"), "yeget")
        self.assertEqual(convert_for_annotation("ел", "N"), "yıl")
        self.assertEqual(convert_for_annotation("е", "N"), "yı")
        self.assertEqual(convert_for_annotation("килүе", "N"), "kilüe")
        self.assertEqual(convert_for_annotation("пьеса", "N"), "pyesa")

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
        self.assertEqual(convert_for_annotation("вәлиев", "RL"), "wäliev")
        self.assertEqual(convert_for_annotation("вәлиева", "RL"), "wälieva")
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

    def test_native_k_g_use_local_vowel_context(self) -> None:
        self.assertEqual(convert_for_annotation("китап", "N"), "kitap")
        self.assertEqual(convert_for_annotation("мәктәп", "N"), "mäktäp")
        self.assertEqual(convert_for_annotation("актүш", "N"), "aqtüş")
        self.assertEqual(convert_for_annotation("бакыр", "N"), "baqır")
        self.assertEqual(convert_for_annotation("гасыр", "N"), "ğasır")
        self.assertEqual(convert_for_annotation("гөл", "N"), "göl")

    def test_loanword_g_stems_use_plain_g(self) -> None:
        self.assertEqual(convert_for_annotation("гараж", "RL"), "garaj")
        self.assertEqual(convert_for_annotation("газет", "RL"), "gazet")
        self.assertEqual(convert_for_annotation("график", "RL"), "grafik")
        self.assertEqual(convert_for_annotation("дифтонг", "RL"), "diftong")
        self.assertEqual(convert_for_annotation("джунгли", "RL"), "djungli")
        self.assertEqual(convert_for_annotation("географик", "RL"), "geografik")

    def test_suffix_g_uses_gh_for_native_and_inflected_loanwords(self) -> None:
        self.assertEqual(convert_for_annotation("законга", "RL"), "zakonğa")
        self.assertEqual(convert_for_annotation("принципларга", "RL"), "prinsiplarğa")
        self.assertEqual(convert_for_annotation("аббревиатурадагы", "RL"), "abbreviaturadağı")
        self.assertEqual(convert_for_annotation("әсәрләрдәге", "N"), "äsärlärdäğe")
        self.assertEqual(convert_for_annotation("куелган", "N"), "quyılğan")
        self.assertEqual(convert_for_annotation("белдергән", "N"), "belderğän")
        self.assertEqual(convert_for_annotation("килгән", "N"), "kilğän")
        self.assertEqual(convert_for_annotation("елга", "N"), "yılğa")

    def test_reviewed_q_words_override_context_rules(self) -> None:
        self.assertEqual(convert_for_annotation("принципка", "RL"), "prinsipqa")
        self.assertEqual(convert_for_annotation("объектка", "RL"), "obyektqa")
        self.assertEqual(convert_for_annotation("беркая", "N"), "berqaya")
        self.assertEqual(convert_for_annotation("кәдер", "N"), "qäder")
        self.assertEqual(convert_for_annotation("салихка", "N"), "salixqa")
        self.assertEqual(convert_for_annotation("закончалыклар", "RL"), "zakonçalıqlar")

    def test_reviewed_k_words_override_context_rules(self) -> None:
        self.assertEqual(convert_for_annotation("башка", "N"), "başka")
        self.assertEqual(convert_for_annotation("башкисәр", "N"), "başkisär")
        self.assertEqual(convert_for_annotation("ияк", "N"), "iäk")
        self.assertEqual(convert_for_annotation("камали", "N"), "kamali")
        self.assertEqual(convert_for_annotation("карават", "N"), "karawat")
        self.assertEqual(convert_for_annotation("каз", "N"), "kaz")
        self.assertEqual(convert_for_annotation("дөньякүләм", "N"), "dönyaküläm")

    def test_reviewed_apostrophe_and_sign_conversions(self) -> None:
        self.assertEqual(convert_for_annotation("д'артаньян", "RL"), "d'artanyan")
        self.assertEqual(convert_for_annotation("коръән", "N"), "qor'än")
        self.assertEqual(convert_for_annotation("таэмин", "N"), "tä'min")
        self.assertEqual(convert_for_annotation("тәэсир", "N"), "tä'sir")
        self.assertEqual(convert_for_annotation("маэмай", "N"), "ma'may")
        self.assertEqual(convert_for_annotation("мәсьәлә", "N"), "mäs'älä")
        self.assertEqual(convert_for_annotation("мәсьүл", "N"), "mäs'ül")
        self.assertEqual(convert_for_annotation("роль", "RL"), "rol'")
        self.assertEqual(convert_for_annotation("культура", "RL"), "kul'tura")
        self.assertEqual(convert_for_annotation("коньяк", "RL"), "kon'yak")
        self.assertEqual(convert_for_annotation("секретарь", "RL"), "sekretar'")
        self.assertEqual(convert_for_annotation("тальян", "RL"), "tal'yan")

    def test_reviewed_silent_sign_conversions(self) -> None:
        self.assertEqual(convert_for_annotation("автомобиль", "RL"), "avtomobil")
        self.assertEqual(convert_for_annotation("компьютер", "RL"), "kompyuter")
        self.assertEqual(convert_for_annotation("нью-йорк", "RL"), "nyu-york")
        self.assertEqual(convert_for_annotation("кремль", "RL"), "kreml")
        self.assertEqual(convert_for_annotation("медаль", "RL"), "medal")
        self.assertEqual(convert_for_annotation("стиль", "RL"), "stil")

    def test_reviewed_yu_conversions(self) -> None:
        self.assertEqual(convert_for_annotation("берьюлы", "N"), "beryulı")
        self.assertEqual(convert_for_annotation("июнендә", "RL"), "iyünendä")
        self.assertEqual(convert_for_annotation("революция", "RL"), "revolyutsiä")
        self.assertEqual(convert_for_annotation("революциясе", "RL"), "revolyutsiäse")
        self.assertEqual(convert_for_annotation("тимерьюл", "N"), "timeryul")
        self.assertEqual(convert_for_annotation("тию", "N"), "tiyü")
        self.assertEqual(convert_for_annotation("юк", "N"), "yuq")
        self.assertEqual(convert_for_annotation("юхиди", "N"), "yuxidi")
        self.assertEqual(convert_for_annotation("ю", "N"), "yü")

    def test_reviewed_gh_lexical_conversions(self) -> None:
        self.assertEqual(convert_for_annotation("мәгънә", "N"), "mäğnä")
        self.assertEqual(convert_for_annotation("мәгънәгә", "N"), "mäğnägä")
        self.assertEqual(convert_for_annotation("җәмигъ", "N"), "cämiğ")
        self.assertEqual(convert_for_annotation("игтибарлы", "N"), "iğtibarlı")
        self.assertEqual(convert_for_annotation("сәгит", "N"), "säğit")
        self.assertEqual(convert_for_annotation("табиги", "N"), "tabiği")
        self.assertEqual(convert_for_annotation("гилемханов", "RL"), "ğilemxanov")
        self.assertEqual(convert_for_annotation("гәлимов", "RL"), "ğälimov")
        self.assertEqual(convert_for_annotation("гөмер", "N"), "ğömer")
        self.assertEqual(convert_for_annotation("шигъри", "N"), "şiğri")
        self.assertEqual(convert_for_annotation("аергыч", "N"), "ayırğıç")
        self.assertEqual(convert_for_annotation("эшләргә", "N"), "eşlärğä")
        self.assertEqual(convert_for_annotation("ишетелгән", "N"), "işetelğän")
        self.assertEqual(convert_for_annotation("кияргә", "N"), "kiärgä")

    def test_reviewed_y_lexical_conversions(self) -> None:
        self.assertEqual(convert_for_annotation("җәмгыяте", "N"), "cämğiäte")
        self.assertEqual(convert_for_annotation("мөстәкыйль", "N"), "möstäqil")
        self.assertEqual(convert_for_annotation("кагыйдә", "N"), "qağidä")
        self.assertEqual(convert_for_annotation("кагыйдәләр", "N"), "qağidälär")
        self.assertEqual(convert_for_annotation("кагыйдәләре", "N"), "qağidäläre")
        self.assertEqual(convert_for_annotation("кагыйдәсенә", "N"), "qağidäsenä")
        self.assertEqual(convert_for_annotation("кыямәт", "N"), "qiämät")
        self.assertEqual(convert_for_annotation("тәнкыйди", "N"), "tänqidi")
        self.assertEqual(convert_for_annotation("тәрәккый", "N"), "täräqqi")
        self.assertEqual(convert_for_annotation("вакыйга", "N"), "waqiğa")
        self.assertEqual(convert_for_annotation("хыянәт", "N"), "xıyänät")
        self.assertEqual(convert_for_annotation("гыйльми", "N"), "ğilmi")
        self.assertEqual(convert_for_annotation("шагыйрь", "N"), "şağir")

    def test_reviewed_u_lexical_conversions(self) -> None:
        self.assertEqual(convert_for_annotation("мияубикә", "N"), "miyawbikä")
        self.assertEqual(convert_for_annotation("мәгъсум", "N"), "mäğsüm")
        self.assertEqual(convert_for_annotation("мәшгуль", "N"), "mäşğül")
        self.assertEqual(convert_for_annotation("сорау", "N"), "soraw")

    def test_reviewed_ya_lexical_conversions(self) -> None:
        self.assertEqual(convert_for_annotation("мордва-ерзя", "RL"), "mordva-erzä")
        self.assertEqual(convert_for_annotation("ял", "N"), "yal")
        self.assertEqual(convert_for_annotation("яз", "N"), "yaz")

    def test_conditional_letter_hints_do_not_include_origin_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {"id": "1", "tatar": True, "tokens": [{"text": "юл", "label": "N"}]},
                    {"id": "2", "tatar": True, "tokens": [{"text": "яңа", "label": "N"}]},
                    {"id": "3", "tatar": True, "tokens": [{"text": "проект", "label": "RL"}]},
                    {"id": "4", "tatar": True, "tokens": [{"text": "вакыт", "label": "N"}]},
                    {"id": "5", "tatar": True, "tokens": [{"text": "гасыр", "label": "N"}]},
                    {"id": "6", "tatar": True, "tokens": [{"text": "күрү", "label": "N"}]},
                    {"id": "7", "tatar": True, "tokens": [{"text": "позиция", "label": "RL"}]},
                ],
            )

            result = export_labelstudio_tasks_from_db(db_path, sort_by="word")

        html_by_word = {
            task["data"]["cyrl_word"]: task["data"]["hints_html"] for task in result.tasks
        }
        self.assertIn("<b>ю</b> -> <b>yu</b>", html_by_word["юл"])
        self.assertIn("<b>я</b> -> <b>ya</b>", html_by_word["яңа"])
        self.assertIn("<b>е</b> -> <b>e</b>", html_by_word["проект"])
        self.assertIn("<b>в</b> -> <b>w</b>", html_by_word["вакыт"])
        self.assertIn("<b>г</b> -> <b>ğ</b>", html_by_word["гасыр"])
        self.assertIn("<b>к</b> -> <b>k</b>", html_by_word["күрү"])
        self.assertIn("<b>ц</b> -> <b>ts</b>", html_by_word["позиция"])
        for html in html_by_word.values():
            self.assertNotIn("because of", html)

    def test_sorting_frequency_limit_and_min_frequency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _write_annotation_db(
                Path(tmpdir) / "zamanalif.sqlite",
                [
                    {"id": "1", "tatar": True, "tokens": [{"text": "юл", "label": "N"}]},
                    {"id": "2", "tatar": True, "tokens": [{"text": "юл", "label": "N"}]},
                    {"id": "3", "tatar": True, "tokens": [{"text": "вакыт", "label": "N"}]},
                ],
            )

            limited = export_labelstudio_tasks_from_db(db_path, max_items=1)
            frequent = export_labelstudio_tasks_from_db(db_path, min_frequency=2)

        self.assertEqual([task["data"]["cyrl_word"] for task in limited.tasks], ["юл"])
        self.assertEqual([task["data"]["cyrl_word"] for task in frequent.tasks], ["юл"])

    def test_cli_writes_labelstudio_json_and_report(self) -> None:
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
            output_path = Path(tmpdir) / "out.json"

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "annotation-export",
                        "--db",
                        str(db_path),
                        "--output",
                        str(output_path),
                    ]
                )

            data = json.loads(output_path.read_text(encoding="utf-8"))
            report = json.loads(Path(str(output_path) + ".report.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(data[0]["data"]["cyrl_word"], "вакыт")
        self.assertEqual(set(data[0]["data"]), {"id", "cyrl_word", "auto_zamanalif", "hints_html"})
        self.assertEqual(report["exported_word_count"], 1)
        self.assertIn("annotation export complete", output.getvalue())

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
            ["вакыт", "турында"],
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
                            {"text": "яңа", "label": "N"},
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

        self.assertEqual([task["data"]["cyrl_word"] for task in result.tasks], ["яңа"])
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
