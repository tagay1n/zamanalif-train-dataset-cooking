"""Offset-preserving literal conversions shared by export and corpus views."""
from __future__ import annotations

import re

from .roman_literals import roman_literal_view


# A degree sign distinguishes the unit from ordinary С -> S. Keep geographic
# С.Ш./С.ш. (north latitude) outside this rule, even with spaces after the dot.
_CELSIUS_RE = re.compile(r"°\s*С(?!\w|\.\s*[Шш]\b)")
_METRE_RE = re.compile(r"(?<=\d)[ \t]+м(?![\w.])")
_REVIEWED_CONJUNCTION_RE = re.compile(r"(?<!\w)ћәм(?!\w)")
_REVIEWED_CYRILLIC_TOKENS = {
    "Қәгазь": "Кәгазь",
    "Қыйммәтле": "Кыйммәтле",
    "Қырым": "Кырым",
    "руҳланган": "рухланган",
    "ОҚ": "ОК",
}
_REVIEWED_CYRILLIC_TOKEN_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(map(re.escape, _REVIEWED_CYRILLIC_TOKENS)) + r")(?!\w)"
)
_HYDROGEN_CHLORIDE_RE = re.compile(r"(?<=хлороводород )НСІ(?!\w)")
_REVIEWED_DELIMITED_FORMULAS = {
    "$СО_2$": "$CO_2$",
    "$СО$": "$CO$",
    "$Н_2СО_3$": "$H_2CO_3$",
    "$СаСО_3$": "$CaCO_3$",
    "$М_1$": "$M_1$",
    "$М_2$": "$M_2$",
    "$x, y, 3xy, -5х²$": "$x, y, 3xy, -5x²$",
    "$у = kx + b$": "$y = kx + b$",
    "$у = kx$": "$y = kx$",
    "$х^2$": "$x^2$",
}
_REVIEWED_DELIMITED_FORMULA_RE = re.compile(
    "|".join(map(re.escape, _REVIEWED_DELIMITED_FORMULAS))
)
_REVIEWED_SYMBOL_PHRASES = {
    "А баганасында йөк ташу датасы": "A баганасында йөк ташу датасы",
    "В баганасында – йөк алып кителгән": "B баганасында – йөк алып кителгән",
    "С баганасында – йөк барып ирешкән": "C баганасында – йөк барып ирешкән",
    "Е баганасында – йөкне илтү өчен": "E баганасында – йөкне илтү өчен",
    "магнит индукциясе В векторының": "магнит индукциясе B векторының",
    "10 кеше х 2 500 сум": "10 кеше x 2 500 сум",
    # Address context distinguishes house-number suffixes from other uses.
    "Шоссейная ур.., 1-43а йорт": "Шоссейная ур.., 1-43a йорт",
    "25А.й. (авыл клубы бинасы)": "25A.y. (авыл клубы бинасы)",
    "Җәлил урамы, 15а нче йорт": "Җәлил урамы, 15a нче йорт",
    "д.15а тел.": "д.15a тел.",
    "Пер.Советский, 44А,с.Алькеево": "Пер.Советский, 44A,с.Алькеево",
    "Совет тыкрыгы, 44А Әлкәй": "Совет тыкрыгы, 44A Әлкәй",
    "д.47а с. Средний Багряж": "д.47a с. Средний Багряж",
    "Үзәк урам, 47а йорт": "Үзәк урам, 47a йорт",
    "Коммунистлар ур., 27В йорт": "Коммунистлар ур., 27V йорт",
    "Урыссу ур., 1-15а йортлар": "Урыссу ур., 1-15a йортлар",
    "Үзәк урамы, 26в (авыл клубы бинасы)": "Үзәк урамы, 26v (авыл клубы бинасы)",
    "Тукай ур., 19,19а йорт": "Тукай ур., 19,19a йорт",
    "6,6а,66 адресы": "6,6a,66 адресы",
    "Кәримов урамы, 1в йорт": "Кәримов урамы, 1v йорт",
    "Зеленая ур., (52А-56, 8-22)": "Зеленая ур., (52A-56, 8-22)",
    "Нариманов ур., 47/а йорт": "Нариманов ур., 47/a йорт",
    "Ф.И.О.": "F.İ.O.",
    "ш.т.п.": "ş.t.p.",
    "[лакуна]": "[lakuna]",
    "16-2а кәгазьләр": "16-2a кәгазьләр",
    "(б. 5–4а)": "(б. 5–4a)",
    "(-ы/-е; -сы/-се)": "(-ı/-e; -sı/-se)",
    "-ай/-әй дифтонгы": "-ay/-äy дифтонгы",
    "Тирән тел арты қ, ғ, х тартыклары": "Тирән тел арты q, ğ, x тартыклары",
    "-сығыз кушымчасы": "-sığız кушымчасы",
    r"\]ө\] авазларының": r"\]ö\] авазларының",
    "ММВ-7-6/529": "MMV-7-6/529",
    "ФС77-60244": "FS77-60244",
    "П/103": "P/103",
    "ММВ-7-8 / 164 @": "MMV-7-8 / 164 @",
    "УЛ-1821": "UL-1821",
    "УП-21": "UP-21",
    "ПГ-5": "PG-5",
    "М-7 «Волга»": "M-7 «Волга»",
    "Р-239 автомобиль юлына": "R-239 автомобиль юлына",
    "ДТ-54": "DT-54",
    "Іб-техник категорияле": "Ib-техник категорияле",
    "Ж1 «Шәхси торак төзелеше зонасы»": "J1 «Шәхси торак төзелеше зонасы»",
    "Ж1 зонасыннан": "J1 зонасыннан",
    "Д1- эшлекле": "D1- эшлекле",
    "Д1 эшлекле": "D1 эшлекле",
    "П4 класслы": "P4 класслы",
    "П2.2": "P2.2",
    "Т-2 формасы": "T-2 формасы",
    "А1- агымдагы": "A1- агымдагы",
    "В- узган елда": "V- узган елда",
    "В1- агымдагы": "V1- агымдагы",
    # Reviewed list items whose source punctuation differs from the usual forms.
    "төгәлләүне, б) барлык элеватор": "төгәлләүне, b) барлык элеватор",
    "бетерүне, в) элеваторлар һәм складларны": "бетерүне, v) элеваторлар һәм складларны",
    "көчәйтүне, г) икмәк кабул итү пунктларына": "көчәйтүне, g) икмәк кабул итү пунктларына",
    "керүе, з) итнең исләнүе": "керүе, z) итнең исләнүе",
    "— в)беренче чиратта кар чыгаруны": "— v)беренче чиратта кар чыгаруны",
    "сезгә а) география буенча": "сезгә a) география буенча",
    "тәэмин итә. в) күчемсез мөлкәт объектларын": "тәэмин итә. v) күчемсез мөлкәт объектларын",
    "һәм б) агымдагы хисапка": "һәм b) агымдагы хисапка",
    "| Б) медицина белгече": "| B) медицина белгече",
    "| В) тарихчы": "| V) тарихчы",
    "«г», «з» пунктчаларында": "«g», «z» пунктчаларында",
    "«к», «л» һәм «н» (2017": "«k», «l» һәм «n» (2017",
    # Reviewed abbreviations, number suffixes, and symbols.
    "графа 1х (графа 2 + графа 5 + графа 8) х графа 9": "графа 1x (графа 2 + графа 5 + графа 8) x графа 9",
    "2023год һәм табалмадым": "2023god һәм табалмадым",
    "кеше/кв. метрдан": "кеше/kv. метрдан",
    "100 м.оз. артмаган": "100 m.oz. артмаган",
    "200 м.оз. артмаган": "200 m.oz. артмаган",
    "1 м² мәйданына": "1 m² мәйданына",
    "Р≤0,6 МПаның": "P≤0,6 МПаның",
    "б.э. IV йөзендә": "b.e. IV йөзендә",
    "б.э. IV йөзләрендә": "b.e. IV йөзләрендә",
    "(IIБ-III дәрәҗәдә": "(IIB-III дәрәҗәдә",
    "(IIБ-III стадиядәге": "(IIB-III стадиядәге",
    "2 нче А тибындагы липоидоз": "2 нче A тибындагы липоидоз",
    r"($C^{\circ}$ларда)": r"($C^{\circ}$larda)",
    "$V_{мбт}$": "$V_{mbt}$",
    # Reviewed document, address, and assessment references.
    "(А.3.1 төркеме": "(A.3.1 төркеме",
    "278/пр номерлы «Йөкләмәләрнең": "278/pr номерлы «Йөкләмәләрнең",
    "№ 5-ФК статистик": "№ 5-FK статистик",
    "400/Бк/к номерлы": "400/Bk/k номерлы",
    "15/Бк/к номерлы карарына": "15/Bk/k номерлы карарына",
    "158/Бк/к номерлы": "158/Bk/k номерлы",
    "№ 7-17/т карарының": "№ 7-17/t карарының",
    "39й. адресы": "39y. адресы",
    "Лермонтов ур., з/у 64Е адресы": "Лермонтов ур., z/u 64E адресы",
    "Родниковая урамы, З/у 11Б": "Родниковая урамы, Z/u 11B",
    "Родниковая урамы, З/у 15": "Родниковая урамы, Z/u 15",
    "Язылган текстның эчтәлеге К1 Чыганак": "Язылган текстның эчтәлеге K1 Чыганак",
    "1.5 п.да күрсәтелгән": "1.5 p.da күрсәтелгән",
    "3.5. п. теркәлгән": "3.5. p. теркәлгән",
    "Д219мм Гнддан": "D219mm Гнддан",
    "Раифа т.п.,": "Раифа t.p.,",
    "02-08-01-2023/Прдп56-23-20920011": "02-08-01-2023/Prdp56-23-20920011",
    "Ш. Қамал": "Ш. Камал",
}
_REVIEWED_SYMBOL_PHRASE_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(map(re.escape, _REVIEWED_SYMBOL_PHRASES)) + r")(?!\w)"
)
_REVIEWED_LIST_LABEL_RE = re.compile(
    r'(?m)(?:^|(?<=[;:]))[ \t]*[абвгдежзирһ](?=\)(?:\s|$))'
    r'|(?<=[«“"])[абвгдежзирһ](?=[»”"]\s+(?:кече\s+)?пункт)'
    r'|^[ \t]*(?:[-–—][ \t]+|")[абвгдежзирһ](?=\)[ \t])'
    r'|(?<=[;:])[ \t]*[абвгдежзирһ](?=[ \t]+[–—][ \t])'
)
_REVIEWED_LIST_LABELS = str.maketrans("абвгдежзирһ", "abvgdejzirh")
_FIGURE_PANEL_REFERENCE_RE = re.compile(
    r"(?<!\w)(рәс\.[ \t]*\d+)((?:,[ \t]*[абвгдежзирһ])+)(?=[ \t]*\))"
)
_TABLE_ROW_NUMBER_RE = re.compile(r"(?:(?<=№)|(?<=\bN)|(?<=\bNo))[ \t]+п/п(?!\w)")
_QUOTED_GRADE_SUFFIX_RE = re.compile(r"(?<=[“«][2-5][”»])ле(?=[ \t]+куела)")
_ORDER_NUMBER_SUFFIX_RE = re.compile(r"(?<=\d)[/-]пр(?=[ \t]+номерлы[ \t]+боер)")
_TARIFF_DECISION_NUMBER_RE = re.compile(
    r"(?<!\w)(№[ \t]*\d+-\d+/)(кс|тэ)(?=[ \t]+карары)"
)
_TARIFF_DECISION_SUFFIXES = {"кс": "ks", "тэ": "te"}
_DECREE_NUMBER_PREFIX_RE = re.compile(r"(?<!\w)(?:УП|ПУ)(?=-\d+(?:[ \t]+номерлы)?[ \t]+[Уу]каз)")
_DECREE_NUMBER_PREFIXES = str.maketrans("УП", "UP")
_AND_OTHERS_RE = re.compile(r"(?<!\w)һ\.[ \t]*б\.(?!\w)")
_AND_OTHERS_LETTERS = str.maketrans("һб", "hb")
_RELIGIOUS_HONORIFIC_RE = re.compile(r"(?<!\w)с\.[ \t]*г\.[ \t]*в\.(?!\w)")
_RELIGIOUS_HONORIFIC_LETTERS = str.maketrans("сгв", "sğw")
_AND_SIMILAR_RE = re.compile(r"(?<!\w)һ\.[ \t]*ш\.[ \t]*о\.(?!\w)")
_AND_SIMILAR_LETTERS = str.maketrans("һшо", "hşo")
_PAGE_ABBREVIATION_RE = re.compile(r"(?<=\d)[ \t]+б\.(?!\w)|(?<=\()б\.(?=[ \t]+\d)")
_BIBLIOGRAPHIC_REFERENCE_RE = re.compile(
    r"\[(?:\d+\s*,|[шШ]унда ук\s*,)\s*б\.[ \t]+\d+(?:[–-]\d+)?\]"
    r"|\[\d+\s*;\s*\d+(?:[–-]\d+)?б\.\]"
    r"|\(т\.[ \t]+\d+[ \t]*,[ \t]*б\.[ \t]+\d+(?:[–-]\d+)?\)"
)
_BIBLIOGRAPHIC_REFERENCE_LETTERS = str.maketrans("бт", "bt")
_YEAR_ABBREVIATION_RE = re.compile(
    r"(?<!\d)[12]\d{3}[ \t]*г\.(?!\w)|(?<!\d)[12]\d{3}[ \t]+г(?=\))"
)


def literal_source_view(text: str) -> str:
    """Return a derived conversion view without changing source offsets."""
    view = roman_literal_view(text)
    view = _CELSIUS_RE.sub(lambda match: match.group()[:-1] + "C", view)
    view = _METRE_RE.sub(lambda match: match.group()[:-1] + "m", view)
    view = _HYDROGEN_CHLORIDE_RE.sub("HCl", view)
    view = _REVIEWED_DELIMITED_FORMULA_RE.sub(
        lambda match: _REVIEWED_DELIMITED_FORMULAS[match.group()], view
    )
    view = _REVIEWED_SYMBOL_PHRASE_RE.sub(
        lambda match: _REVIEWED_SYMBOL_PHRASES[match.group()], view
    )
    view = _REVIEWED_LIST_LABEL_RE.sub(
        lambda match: match.group().translate(_REVIEWED_LIST_LABELS), view
    )
    view = _FIGURE_PANEL_REFERENCE_RE.sub(
        lambda match: match[1] + match[2].translate(_REVIEWED_LIST_LABELS), view
    )
    view = _TABLE_ROW_NUMBER_RE.sub(lambda match: match.group().replace("п", "p"), view)
    view = _QUOTED_GRADE_SUFFIX_RE.sub("le", view)
    view = _ORDER_NUMBER_SUFFIX_RE.sub(lambda match: match.group()[0] + "pr", view)
    view = _TARIFF_DECISION_NUMBER_RE.sub(
        lambda match: match[1] + _TARIFF_DECISION_SUFFIXES[match[2]], view
    )
    view = _DECREE_NUMBER_PREFIX_RE.sub(
        lambda match: match.group().translate(_DECREE_NUMBER_PREFIXES), view
    )
    view = _AND_OTHERS_RE.sub(
        lambda match: match.group().translate(_AND_OTHERS_LETTERS), view
    )
    view = _RELIGIOUS_HONORIFIC_RE.sub(
        lambda match: match.group().translate(_RELIGIOUS_HONORIFIC_LETTERS), view
    )
    view = _AND_SIMILAR_RE.sub(
        lambda match: match.group().translate(_AND_SIMILAR_LETTERS), view
    )
    view = _PAGE_ABBREVIATION_RE.sub(lambda match: match.group().replace("б", "b"), view)
    view = _BIBLIOGRAPHIC_REFERENCE_RE.sub(
        lambda match: match.group().translate(_BIBLIOGRAPHIC_REFERENCE_LETTERS), view
    )
    view = _YEAR_ABBREVIATION_RE.sub(lambda match: match.group().replace("г", "g"), view)
    # Reviewed source typo: retain the original token and every source offset.
    # The Latin h is then retained while the ordinary converter handles әм.
    view = _REVIEWED_CONJUNCTION_RE.sub("hәм", view)
    return _REVIEWED_CYRILLIC_TOKEN_RE.sub(
        lambda match: _REVIEWED_CYRILLIC_TOKENS[match.group()], view
    )
