from __future__ import annotations

from collections.abc import Mapping
import unicodedata

from .conversion import (
    CYRILLIC_LETTER_REFERENCES,
    Choice,
    RULES,
    ZAMANALIF_APOSTROPHE,
    parse_dsl,
)
from .word_export import (
    CYRILLIC_RE,
    _char_conversion,
    _guess_hyphen_part_label,
    _surname_sequence_conversion,
    conversion_branches,
    convert_for_annotation,
    normalize_word,
)


WORD_SPELLING_EXCEPTIONS = {
    "герцог": {"gersog"},
    "актер": {"aktyor"},
    "зачет-ларга": {"zaçyot-larğa"},
    "галимовалардан": {"ğalimovalardan"},
    "галимовка": {"ğalimovqa"},
    "галимҗанованың": {"ğalimcanovanıñ"},
    "гаяр": {"ğayar"},
    "гаярь": {"ğayär"},
    "гаярьләренең": {"ğayärläreneñ"},
    "гаят": {"ğayät"},
    "гаять": {"ğayät"},
    "гаятьтән": {"ğayättän"},
    "корьән": {"qorʼän"},
    "корьәнгә": {"qorʼängä"},
    "корьәндә": {"qorʼändä"},
    "тукаевага": {"tuqayevağa"},
    "тукаевка": {"tuqayevqa"},
    "тукаевларның": {"tuqayevlarnıñ"},
    "хәкыйкәт": {"xäqıyqät"},
    "хәкыйкәтнең": {"xäqıyqätneñ"},
    "әүвәл": {"äüwäl"},
    "рабигыль-әүвәл": {"rabiğıl-äüwäl"},
    "рабигыльәүвәл": {"rabiğıläüwäl"},
    "фәүкылгадә": {"fäüqılğadä"},
    "фәүкыльгадә": {"fäüqılğadä"},
    "тәсаувыр": {"täsauwır"},
    "тәсаувыф": {"täsauwıf"},
    "мəгарифен": {"mäğarifen"},
    "мөстəкыйль": {"möstäqıyl"},
    "игтибарлыкларын": {"iğtibarlıqların"},
    "игьтибар": {"iğtibar"},
    "игьтибарны": {"iğtibarnı"},
    "диккатъ": {"diqqat"},
    "диккать": {"diqqat"},
    "еглавы": {"yığlawı"},
    "еглаучыларны": {"yığlawçılarnı"},
    "жаек": {"jayıq"},
    "жаек-идел": {"jayıq-idel"},
    "жаекка": {"jayıqqa"},
    "җаекка": {"cayıqqa"},
    "җаектагы": {"cayıqtağı"},
    "галим-шәркыятче": {"ğalim-şärqıyätçe"},
    "шәркыять": {"şärqıyät"},
    "шәркыятьче": {"şärqıyätçe"},
    "шәркыятьчесе": {"şärqıyätçese"},
    # Project 59 reviews that use iye in the written -иев/-иева endings.
    "гарәфиева": {"ğaräfiyeva"},
    "гый-мадиева": {"ğıy-madiyeva"},
    "гыйльмиева": {"ğıylmiyeva"},
    "корбангалиева": {"qorbanğaliyeva"},
    "миннегалиев": {"minneğaliyev"},
    "миңневолиев": {"miñnewoliyev"},
    "миңневэлиева": {"miñneweliyeva"},
    "муллагалиева": {"mullağaliyeva"},
    "р.ганиева": {"r.ğaniyeva"},
    "сәфәргалиев": {"säfärğaliyev"},
    "туктиев": {"tuqtiyev"},
    "ф.ә.ганиева": {"f.ä.ğaniyeva"},
    "шагалиев": {"şağaliyev"},
    "шәйхелгалиев": {"şäyxelğaliyev"},
    "әхмәтвәлиева": {"äxmätwäliyeva"},
    "әхмәтгалиев": {"äxmätğaliyev"},
    # Project 59 surname reviews with an explicit glide in -еев/-еева.
    "галеев": {"ğaleyev"},
    "камбеева": {"qambeyeva"},
    "кыдыкеева": {"qıdıkeyeva"},
    "миргалеев": {"mirğaleyev"},
    # Reviewed Project 59 surnames retaining v before the Tatar dative.
    "ганиевка": {"ğaniyevqa"},
    "енгалычевка": {"yınğalıçevqa"},
    "искәндәровка": {"iskändärovqa"},
    "кәримовка": {"kärimovqa"},
    "лаисовка": {"laisovqa"},
    "минзариповка": {"minzaripovqa"},
    "миннехановка": {"minnexanovqa"},
    "миңнегуловка": {"miñneğulovqa"},
    "мөхетдиновка": {"möxetdinovqa"},
    "нургалиевка": {"nurğaliyevqa"},
    "рафиковка": {"rafiqovqa"},
    "ризвановка": {"rizwanovqa"},
    "ситдыйковка": {"sitdıyqovqa"},
    "солтанбековка": {"soltanbekovqa"},
    "сәлаховка": {"sälaxovqa"},
    "терегуловка": {"teregulovqa"},
    "фәрвазетдиновка": {"färwazetdinovqa"},
    "фәрраховка": {"färraxovqa"},
    "фәхртдиновка": {"fäxrtdinovqa"},
    "харисовка": {"xarisovqa"},
    "хисамовка": {"xisamovqa"},
    "хәбировка": {"xäbirovqa"},
    "хәйретдиновка": {"xäyretdinovqa"},
    "хәлимовка": {"xälimovqa"},
    "хәсәновка": {"xäsänovqa"},
    "шаһиевка": {"şahiyevqa"},
    "шәймиевка": {"şäymiyevqa"},
    "шәймәрдәновка": {"şäymärdänovqa"},
    "шәяхмәтовка": {"şäyäxmätovqa"},
    "юлашевка": {"yulaşevqa"},
    "җамалетдиновка": {"camaletdinovqa"},
    "җиһановка": {"cihanovqa"},
    "җәлиловка": {"cälilovqa"},
    "әминовка": {"äminovqa"},
    "әфләтуновка": {"äflätunovqa"},
    "әхметовка": {"äxmetovqa"},
    # Reviewed surname forms with v before other Tatar endings.
    "галиеваның": {"ğaliyevanıñ"},
    "галиевне": {"ğaliyevne"},
    "галиәкбәрованың": {"ğaliäkbärovanıñ"},
    "галәветдиновага": {"ğaläwetdinovağa"},
    "ганиевлар": {"ğaniyevlar"},
    "гариповага": {"ğaripovağa"},
    "гариповларга": {"ğaripovlarğa"},
    "гомәркәевнең": {"ğomärkäyevneñ"},
    "гыйльмиевнең": {"ğıylmiyevneñ"},
    "гыймазетдиновлар": {"ğıymazetdinovlar"},
    "гыйниятовның": {"ğıyniyätovnıñ"},
    "зәкуановның": {"zäquanovnıñ"},
    "ильясовларның": {"ilyasovlarnıñ"},
    "исмәгыйлеваның": {"ismäğıylevanıñ"},
    "камалетдиновны": {"qamaletdinovnı"},
    "каһировларның": {"qahirovlarnıñ"},
    "корбангалиевның": {"qorbanğaliyevnıñ"},
    "кудашеваның": {"qudaşevanıñ"},
    "мингалимовның": {"minğalimovnıñ"},
    "миңнегалиевага": {"miñneğaliyevağa"},
    "мәүлиевлар": {"mäwliyevlar"},
    "мөхәммәдьяровлар": {"möxämmädyarovlar"},
    "мөхәммәдьяровның": {"möxämmädyarovnıñ"},
    "нургалиевнең": {"nurğaliyevneñ"},
    "нургалиевның": {"nurğaliyevnıñ"},
    "рәкыйповның": {"räqıypovnıñ"},
    "рәхимкулованың": {"räximqulovanıñ"},
    "солтангалиевнең": {"soltanğaliyevneñ"},
    "х.халиковның": {"x.xaliqovnıñ"},
    "шәяхмәтовны": {"şäyäxmätovnı"},
    "әгъләмовның": {"äğlämovnıñ"},
    # Project 59 number compounds with separately reviewed yıl/yul components.
    "дүртъюллык": {"dürtyullıq"},
    "жедееллыкта": {"jedeyıllıqta"},
    "икееллыгы": {"ikeyıllığı"},
    "икеюллыклары": {"ikeyullıqları"},
    "икеюллыкны": {"ikeyullıqnı"},
    "йөзъеллыкның": {"yözyıllıqnıñ"},
    "сигезьюллык": {"sigezyullıq"},
    "унъеллыгына": {"unyıllığına"},
}
OCCURRENCE_SPELLING_EXCEPTIONS = {
    ("sent_001132", 28, "плюcлы"): {"plyuslı"},
    # References to the poet in Tatar prose use the reviewed Tuqay stem.
    ("sent_001343", 16, "Тукаев"): {"tuqayev"},
    ("sent_002768", 14, "Тукаев"): {"tuqayev"},
    ("sent_004378", 4, "Тукаев"): {"tuqayev"},
    ("sent_005831", 3, "Тукаев"): {"tuqayev"},
    ("sent_007962", 16, "Тукаев"): {"tuqayev"},
    ("sent_008149", 31, "Тукаевның"): {"tuqayevnıñ"},
    ("sent_008752", 19, "Тукаев"): {"tuqayev"},
    ("sent_009860", 26, "Тукаев"): {"tuqayev"},
    ("sent_009860", 37, "Тукаев"): {"tuqayev"},
    ("sent_010223", 35, "Тукаев"): {"tuqayev"},
    ("sent_011918", 11, "Тукаев"): {"tuqayev"},
    ("sent_012259", 19, "Тукаев"): {"tuqayev"},
    ("sent_013125", 27, "Тукаев"): {"tuqayev"},
    ("sent_013858", 19, "Тукаев"): {"tuqayev"},
    ("sent_024066", 14, "Тукаев"): {"tuqayev"},
    ("sent_024066", 32, "Тукаев"): {"tuqayev"},
    ("sent_028693", 10, "Тукаев"): {"tuqayev"},
}
REVIEWED_STEM_SPELLINGS = {
    "кыяфәт": ("qıyäfät", "qıyafät"),
    "калып-кыяфәт": ("qalıpqıyäfät", "qalıpqıyafät"),
    "шикаят": ("şiqayät", "şiqayat"),
    "тәрәкъкыят": ("täräqqıyät", "täräqqıyat"),
    "тәрәккыят": ("täräqqıyät", "täräkqıyat"),
    "җәмгыят": ("cämğıyät", "cämğıyat"),
    "жәмгыят": ("jämğıyät", "jämğıyat"),
    "жҗәмгыят": ("jcämğıyät", "jcämğıyat"),
    "хуҗалыкҗәмгыят": ("xucalıqcämğıyät", "xucalıqcämğıyat"),
    "риваят": ("riwayät", "riwayat"),
    "җинаять": ("cinayät", "cinayat"),
    "жинаять": ("jinayät", "jinayat"),
    "җиңаять": ("ciñayät", "ciñayat"),
}
UNRESOLVED_VARIANTS = {
    "г": ("g", "ğ"),
    "к": ("k", "q"),
    "е": ("e", "ye", "yı"),
    "ю": ("yu", "yü"),
    "я": ("ya", "yä", "a", "ä"),
}
_TURKIC_LOWER = str.maketrans({"I": "ı", "İ": "i"})

Fragment = tuple[str, int]
Path = tuple[tuple[Fragment, ...], ...]
Offsets = set[tuple[int, int]]


def _spelling(value: str) -> str:
    folded = unicodedata.normalize("NFC", value).translate(_TURKIC_LOWER).lower()
    if folded.isalnum():
        return folded
    return "".join(
        char for char in folded
        if char.isalnum() or unicodedata.category(char).startswith("M")
    )


def is_approved_spelling(sample_id: str, token_index: int, source: str, target: str) -> bool:
    word_forms = WORD_SPELLING_EXCEPTIONS.get(normalize_word(source), ())
    occurrence_forms = OCCURRENCE_SPELLING_EXCEPTIONS.get((sample_id, token_index, source), ())
    if not word_forms and not occurrence_forms:
        return False
    spelling = _spelling(target)
    # Separator layout is checked separately from letter correspondence.
    return any(
        spelling == _spelling(form)
        for forms in (word_forms, occurrence_forms)
        for form in forms
    )


def _match_path(path: Path, target: str, offsets: Offsets, budget: int) -> Offsets:
    for alternatives in path:
        offsets = {
            (offset + len(fragment), used + cost)
            for offset, used in offsets
            for fragment, cost in alternatives
            if used + cost <= budget and target.startswith(fragment, offset)
        }
        if not offsets:
            break
    return offsets


def _canonical_path(dsl: str, policy: Mapping[str, str]) -> Path:
    if not dsl:
        return ()
    if "{{" not in dsl:
        return (((_spelling(dsl), 0),),)
    pieces = []
    for segment in parse_dsl(dsl).segments:
        if not isinstance(segment, Choice):
            options = (segment.text,)
        elif segment.rule_id == "YA":
            options = tuple(text for _, text in segment.options)
        else:
            option = policy.get(segment.rule_id, RULES[segment.rule_id].default_option)
            options = (dict(segment.options)[option],)
        pieces.append(tuple((_spelling(text), 0) for text in options))
    return tuple(pieces)


def _has_embedded_literal(word: str) -> bool:
    return bool(_spelling(CYRILLIC_RE.sub("", word).replace(ZAMANALIF_APOSTROPHE, "")))


def _mechanical_path(word: str, label: str) -> Path:
    pieces = []
    index = 0
    while index < len(word):
        char = word[index]
        surname = _surname_sequence_conversion(word, index)
        if surname is not None:
            fragment, consumed = surname
        elif label == "N" and char in "гк" and word[index + 1 : index + 2] == "ъ":
            fragment, consumed = ("ğ" if char == "г" else "q"), 2
        else:
            fragment = _char_conversion(char, word, index, label) if CYRILLIC_RE.fullmatch(char) else char
            consumed = 1
        options = [(fragment, 0)]
        if CYRILLIC_RE.fullmatch(fragment):
            options = [(form, 0) for form in UNRESOLVED_VARIANTS.get(char, ())]
        elif char == "я" and fragment in {"ya", "yä"}:
            options.append((fragment[1:], 0))
        elif char == "е" and fragment == "ye":
            options.append(("e", 0 if index and word[index - 1] == "и" else 1))
        pieces.append(tuple((_spelling(form), cost) for form, cost in options))
        index += consumed
    return tuple(pieces)


def _component_paths(word: str, label: str, policy: Mapping[str, str]) -> tuple[Path, ...]:
    mechanical = _mechanical_path(word, label)
    if _has_embedded_literal(word):
        return (mechanical,)
    dsl = conversion_branches(word).suggestion(label)
    canonical = _canonical_path(dsl, policy)
    if not canonical:
        return (mechanical,)
    baseline = "".join(alternatives[0][0] for alternatives in mechanical if alternatives)
    if ("{{" in dsl and any(rule != "YA" for rule in parse_dsl(dsl).rule_ids)) or (
        baseline != _spelling(convert_for_annotation(word, label))
    ):
        # A lexical rewrite or policy controls this component's spelling.
        return (canonical,)
    return canonical, mechanical


def spelling_matches(
    source: str, target: str, policy: Mapping[str, str], *,
    legacy_e_glides: int = 0, reviewed_initial: bool = False,
) -> bool:
    """Match the rendered spelling to a complete native or loanword conversion."""
    word = normalize_word(source.translate(_TURKIC_LOWER))
    spelling = _spelling(target)
    if not word or word in CYRILLIC_LETTER_REFERENCES:
        return _spelling(source) == spelling

    matches = list(CYRILLIC_RE.finditer(source))
    first, last = matches[0], matches[-1]
    prefix, suffix = _spelling(source[:first.start()]), _spelling(source[last.end():])
    if len(spelling) < len(prefix) + len(suffix) or not spelling.startswith(prefix) or not spelling.endswith(suffix):
        return False
    core = spelling[len(prefix):len(spelling) - len(suffix)]

    branches = conversion_branches(word)
    embedded_literal = _has_embedded_literal(word)
    for label in ("N", "RL"):
        if not embedded_literal:
            canonical = _canonical_path(branches.suggestion(label), policy)
            if len(canonical) == 1 and (core, 0) in canonical[0]:
                return True
            if canonical and any(offset == len(core) for offset, _ in _match_path(canonical, core, {(0, 0)}, 0)):
                return True
        if reviewed_initial and len(word) == len(core) == 1:
            forms = _mechanical_path(word, label)[0]
            if any(form.startswith(core) for form, _ in forms):
                return True
        offsets: Offsets = {(0, 0)}
        for component in word.split("-"):
            part_label = _guess_hyphen_part_label(component, label)
            offsets = set().union(*(
                _match_path(path, core, offsets, legacy_e_glides)
                for path in _component_paths(component, part_label, policy)
            ))
            if not offsets:
                break
        if any(offset == len(core) for offset, _ in offsets):
            return True
    # Compare the complete word against the regular paths after substituting
    # the reference spelling for a reviewed source stem.
    for root, (reviewed, reference) in REVIEWED_STEM_SPELLINGS.items():
        if word.startswith(root) and core.startswith(reviewed):
            reference_target = prefix + reference + core[len(reviewed):] + suffix
            return spelling_matches(
                source, reference_target, policy,
                legacy_e_glides=legacy_e_glides, reviewed_initial=reviewed_initial,
            )
    return False
