from __future__ import annotations

from html import escape
from typing import Iterable


RULE_GUIDANCE: dict[str, tuple[str, tuple[tuple[str, tuple[str, ...]], ...]]] = {
    "YA": (
        "Choose the contextual Zamanalif form of я: ya, yä, a, or ä.",
        (("мордва-ерзя", ("mordva-erzya", "mordva-erzä")),),
    ),
    "E_GLIDE": (
        "Choose whether е is plain e or has an explicit y glide.",
        (("проект", ("proekt", "proyekt")),),
    ),
    "MOSTAQIL": (
        "Choose the attested spelling of the мөстәкыйль stem.",
        (("мөстәкыйль", ("möstäqil", "möstäqıyl")),),
    ),
    "FIGYL_STEM": (
        "Choose the attested spelling of the фигыль stem.",
        (("фигыль", ("fiğıl", "fiğel")),),
    ),
    "SHIGYR_STEM": (
        "Choose the attested spelling of the шигырь stem.",
        (("шигырь", ("şiğır", "şiğer")),),
    ),
    "IJTIMAGIY_STEM": (
        "Choose the attested spelling of the иҗтимагый stem.",
        (("иҗтимагый", ("ictimağıy", "ictimaği")),),
    ),
    "KAGAZ_STEM": (
        "Choose the attested spelling of the кәгазь/кәгазъ stem.",
        (("кәгазъдә", ("käğazdä", "qäğäzdä")),),
    ),
    "MASHGUL_STEM": (
        "Choose the attested spelling and harmony of the мәшгуль stem.",
        (("мәшгуль", ("mäşğul", "mäşğül")),),
    ),
    "HAMZA": (
        "Choose whether an Arabic/Persian hamza is omitted or represented by ʼ.",
        (("коръән", ("qorän", "qorʼän")),),
    ),
    "RUS_SOFT_SIGN_O": (
        "Choose how a Russian soft sign before о is represented.",
        (("батальон", ("batalon", "batalʼon", "batalʼyon")),),
    ),
}

UNKNOWN_GUIDANCE = {
    "unknown_origin": (
        "Gemini could not determine the word's origin. Review the complete written "
        "form carefully: it may be a hyphenated compound, an abbreviation or "
        "fragment, a Tatar-specific word, or a word with context-dependent letters. "
        "Expand nothing, and skip the task when the word is not recognizable enough "
        "to correct reliably."
    ),
    "u_hyphenated": (
        "The origin of this hyphenated compound is unresolved. Check the complete "
        "written form and every component when correcting the proposed spelling."
    ),
    "u_abbrev_fragment": (
        "The item may be an abbreviation, initialism, or fragment. Expand nothing: "
        "correct only the written form that is shown."
    ),
    "u_tatar_specific": (
        "The word contains Tatar-specific Cyrillic letters but its origin is unresolved. "
        "Use the whole word, not one letter alone, to judge the proposed spelling."
    ),
    "u_conditional_plain": (
        "The word has a conditional conversion letter and unresolved origin. Check "
        "the proposed Zamanalif spelling carefully."
    ),
    "u_other": (
        "Gemini could not determine this word's origin. Correct the proposed spelling "
        "only when the word is recognizable; otherwise skip the task."
    ),
}

CATCHALL_GUIDANCE = (
    (
        "в",
        "usually w in native/Tatar words and v in Russian or Russian-international words",
        ("вакыт → waqıt", "совет → sovet"),
    ),
    (
        "к",
        "often q in back-vowel native words, k in front-vowel words and many loanwords",
        ("халык → xalıq", "кеше → keşe", "комитет → komitet"),
    ),
    (
        "ц",
        "defaults to ts; edit the plain suggestion only for a verified lexical exception",
        ("цирк → tsirk", "позиция → pozitsiyä"),
    ),
    (
        "г",
        "often ğ in back-vowel native words, g in front-vowel words and many loanwords",
        ("туган → tuğan", "гөл → göl", "грант → grant"),
    ),
    (
        "е",
        "may be ye, yı, or e depending on position and word type",
        ("егет → yeget", "ел → yıl", "проект → proyekt"),
    ),
    (
        "я",
        "may be ya, yä, a, or ä depending on context",
        ("яңа → yaña", "сәяхәт → säyäxät", "әкият → äkiät"),
    ),
    (
        "ю",
        "may be yu, yü, or another contextual form",
        ("юл → yul", "юкә → yükä", "бию → biü"),
    ),
    (
        "Russian consonant + я/ю/ё",
        "defaults to an explicit y glide; edit the plain suggestion for a verified exception",
        ("бюро → byuro", "шофёр → şofyor", "щётка → şçyotka"),
    ),
    (
        "у / ү",
        "may interact with nearby vowels or produce a w-like glide",
        ("уку → uqu", "күрү → kürü", "саклау → saqlaw"),
    ),
)


def render_project_instructions(
    project_key: str,
    project_title: str,
    rule_ids: Iterable[str],
) -> str:
    """Render Label Studio project instructions for one active export project."""
    sections = [
        _heading(project_title),
        _workflow(),
        _focus(project_key, rule_ids),
        _editing_rules(project_key),
    ]
    return "\n\n".join(sections) + "\n"


def _heading(project_title: str) -> str:
    return (
        "<section>\n"
        f"  <h2>{escape(project_title)}</h2>\n"
        "  <p>Review one Cyrillic word form at a time and approve its final "
        "Zamanalif spelling.</p>\n"
        "</section>"
    )


def _workflow() -> str:
    return """<section>
  <h3>What To Do</h3>
  <ol>
    <li>Read the original Cyrillic word.</li>
    <li>If the word can be either native or Russian depending on sentence context, check <b>Homonym</b> and submit.</li>
    <li>Otherwise, check and, if needed, edit the suggested Zamanalif spelling.</li>
    <li>Submit, or skip the task when you cannot decide reliably.</li>
  </ol>
</section>"""


def _focus(project_key: str, rule_ids: Iterable[str]) -> str:
    if project_key == "catchall":
        items = []
        for letter, explanation, examples in CATCHALL_GUIDANCE:
            rendered_examples = "; ".join(escape(example) for example in examples)
            items.append(
                f"<li><b>{escape(letter)}</b>: {escape(explanation)}. "
                f"Examples: {rendered_examples}.</li>"
            )
        return (
            "<section>\n"
            "  <h3>Conversion Focus</h3>\n"
            "  <p>Check the proposed spelling, especially these common conditional "
            "cases:</p>\n"
            "  <ul>\n    "
            + "\n    ".join(items)
            + "\n  </ul>\n"
            "  <p>For ordinary Russian soft or hard signs (ь/ъ), the suggestion "
            "preserves the sign as ʼ, for example федераль → federalʼ, культура → "
            "kulʼtura, and роль → rolʼ. Russian ье converts to ʼye and ъе to ye. "
            "Russian consonant + я/ю/ё uses an explicit <b>y</b> glide.</p>\n"
            "  <p>The specialized <b>RUS_SOFT_SIGN_O</b> rule remains a focused "
            "project; all other Russian-sign conventions are deterministic.</p>\n"
            "</section>"
        )

    unknown = UNKNOWN_GUIDANCE.get(project_key)
    if unknown is not None:
        return (
            "<section>\n"
            "  <h3>Conversion Focus</h3>\n"
            f"  <p>{escape(unknown)}</p>\n"
            "</section>"
        )

    ordered_rules = tuple(dict.fromkeys(rule_ids))
    if project_key == "hamza" and "HAMZA" not in ordered_rules:
        ordered_rules = ("HAMZA", *ordered_rules)
    if not ordered_rules:
        raise ValueError(f"project {project_key!r} has no instruction guidance")
    items: list[str] = []
    for rule_id in ordered_rules:
        try:
            explanation, examples = RULE_GUIDANCE[rule_id]
        except KeyError as exc:
            raise ValueError(f"missing instruction guidance for DSL rule {rule_id}") from exc
        rendered_examples = "; ".join(
            f"<b>{escape(cyrillic)}</b> → "
            + " / ".join(f"<b>{escape(variant)}</b>" for variant in variants)
            for cyrillic, variants in examples
        )
        items.append(
            f"<li><b>{escape(rule_id)}</b>: {escape(explanation)} "
            f"Examples: {rendered_examples}.</li>"
        )
    return (
        "<section>\n"
        "  <h3>Conversion Focus</h3>\n"
        "  <p>The alternatives below are attested conventions. Enter the complete "
        "word, not DSL syntax.</p>\n"
        "  <ul>\n    "
        + "\n    ".join(items)
        + "\n  </ul>\n</section>"
    )


def _editing_rules(project_key: str) -> str:
    rejection_rule = (
        ""
        if project_key in {"catchall", "unknown_origin"}
        else (
            "\n    <li>Replace an invalid variant line with <b>-</b>. Keep the "
            "line order and at least one word; rejecting alternatives must leave "
            "exactly one word.</li>"
        )
    )
    return f"""<section>
  <h3>Editing Rules</h3>
  <ul>
    <li>Write one complete final Zamanalif word form on each line.</li>{rejection_rule}
    <li>Do not enter DSL syntax, explanations, meanings, lemmas, or alternatives.</li>
    <li>Preserve Zamanalif letters exactly: <b>ä ö ü ñ ı ğ ş ç</b> and their uppercase forms.</li>
    <li>Use the apostrophe <b>ʼ</b> when the selected spelling requires one.</li>
    <li>Skip malformed or genuinely uncertain items.</li>
  </ul>
</section>"""
