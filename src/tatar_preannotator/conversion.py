from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Iterable, Mapping


ZAMANALIF_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "äÄöÖüÜñÑıİğĞşŞçÇ"
    "-'’—"
)
IDENTIFIER_RE = re.compile(r"[A-Z][A-Z0-9_]*")
OPTION_RE = re.compile(r"[a-z][a-z0-9_]*")


class DslError(ValueError):
    """Raised when conversion DSL is malformed or cannot be resolved."""


@dataclass(frozen=True)
class Literal:
    """A deterministic piece of converted Zamanalif text."""

    text: str


@dataclass(frozen=True)
class Choice:
    """A policy-controlled choice between accepted conversion conventions."""

    rule_id: str
    options: tuple[tuple[str, str], ...]


Segment = Literal | Choice


@dataclass(frozen=True)
class ConversionResult:
    """Structured conversion output containing literals and policy choices."""

    segments: tuple[Segment, ...]

    @property
    def has_choices(self) -> bool:
        return any(isinstance(segment, Choice) for segment in self.segments)

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return tuple(
            segment.rule_id for segment in self.segments if isinstance(segment, Choice)
        )

    def to_dsl(self) -> str:
        return serialize_dsl(self)

    def resolve(self, policy: Mapping[str, str] | None = None) -> str:
        return resolve_result(self, policy)


@dataclass(frozen=True)
class RuleDefinition:
    """Allowed options and preferred default for one convention choice."""

    rule_id: str
    options: tuple[tuple[str, str], ...]
    default_option: str
    allow_custom_option_text: bool = False


IYA_RULE = RuleDefinition(
    rule_id="IYA",
    options=(("compact", "ä"), ("explicit", "yä")),
    default_option="explicit",
)
ARABIC_INITIAL_GA_RULE = RuleDefinition(
    rule_id="ARABIC_INITIAL_GA",
    options=(("plain", "a"), ("front", "ä")),
    default_option="plain",
    allow_custom_option_text=True,
)
IE_GLIDE_RULE = RuleDefinition(
    rule_id="IE_GLIDE",
    options=(("plain", "e"), ("glide", "ye")),
    default_option="plain",
)
RUS_SIGN_GLIDE_RULE = RuleDefinition(
    rule_id="RUS_SIGN_GLIDE",
    options=(("omit", ""), ("preserve", "'")),
    default_option="omit",
)
RUS_SOFT_SIGN_RULE = RuleDefinition(
    rule_id="RUS_SOFT_SIGN",
    options=(("omit", ""), ("preserve", "'")),
    default_option="preserve",
)
RUS_JOTATED_SOFTENING_RULE = RuleDefinition(
    rule_id="RUS_JOTATED_SOFTENING",
    options=(("glide", "y"), ("apostrophe", "'")),
    default_option="glide",
)
RL_FINAL_KA_RULE = RuleDefinition(
    rule_id="RL_FINAL_KA",
    options=(("suffix", "q"), ("stem", "k")),
    default_option="suffix",
)
NATIVE_UW_RULE = RuleDefinition(
    rule_id="NATIVE_UW",
    options=(("plain", ""), ("glide", "w")),
    default_option="glide",
)

RULES: Mapping[str, RuleDefinition] = MappingProxyType(
    {
        IYA_RULE.rule_id: IYA_RULE,
        ARABIC_INITIAL_GA_RULE.rule_id: ARABIC_INITIAL_GA_RULE,
        IE_GLIDE_RULE.rule_id: IE_GLIDE_RULE,
        RUS_SIGN_GLIDE_RULE.rule_id: RUS_SIGN_GLIDE_RULE,
        RUS_SOFT_SIGN_RULE.rule_id: RUS_SOFT_SIGN_RULE,
        RUS_JOTATED_SOFTENING_RULE.rule_id: RUS_JOTATED_SOFTENING_RULE,
        RL_FINAL_KA_RULE.rule_id: RL_FINAL_KA_RULE,
        NATIVE_UW_RULE.rule_id: NATIVE_UW_RULE,
    }
)
PREFERRED_POLICY: Mapping[str, str] = MappingProxyType(
    {
        "IYA": "explicit",
        "ARABIC_INITIAL_GA": "plain",
        "IE_GLIDE": "plain",
        "RUS_SIGN_GLIDE": "omit",
        "RUS_SOFT_SIGN": "preserve",
        "RUS_JOTATED_SOFTENING": "glide",
        "RL_FINAL_KA": "suffix",
        "NATIVE_UW": "glide",
    }
)
PDF_COMPACT_POLICY: Mapping[str, str] = MappingProxyType(
    {
        "IYA": "compact",
        "ARABIC_INITIAL_GA": "plain",
        "IE_GLIDE": "plain",
        "RUS_SIGN_GLIDE": "omit",
        "RUS_SOFT_SIGN": "omit",
        "RUS_JOTATED_SOFTENING": "glide",
        "RL_FINAL_KA": "suffix",
        "NATIVE_UW": "plain",
    }
)


def result_with_iya_choices(source: str, compact_zamanalif: str) -> ConversionResult:
    """Annotate aligned Cyrillic ``ия`` / compact ``iä`` occurrences with IYA choices.

    The legacy converter already handles lexical exceptions. A choice is emitted only
    when every source ``ия`` occurrence aligns with a compact ``iä`` occurrence. If
    alignment is uncertain, the plain result is retained for human review rather than
    inventing a policy choice.
    """
    source_count = source.casefold().count("ия")
    output_count = compact_zamanalif.casefold().count("iä")
    if source_count == 0 or source_count != output_count:
        return ConversionResult((Literal(compact_zamanalif),))

    segments: list[Segment] = []
    start = 0
    for match in re.finditer("iä", compact_zamanalif, flags=re.IGNORECASE):
        _append_literal(segments, compact_zamanalif[start : match.start() + 1])
        segments.append(Choice(IYA_RULE.rule_id, IYA_RULE.options))
        start = match.end()
    _append_literal(segments, compact_zamanalif[start:])
    return ConversionResult(tuple(segments))


def parse_dsl(value: str) -> ConversionResult:
    """Parse and validate canonical inline conversion DSL."""
    if not isinstance(value, str) or not value:
        raise DslError("conversion DSL must be a non-empty string")

    segments: list[Segment] = []
    position = 0
    while position < len(value):
        opening = value.find("{{", position)
        stray_closing = value.find("}}", position)
        if stray_closing != -1 and (opening == -1 or stray_closing < opening):
            raise DslError(f"unexpected closing delimiter at position {stray_closing}")
        if opening == -1:
            literal = value[position:]
            _validate_zamanalif(literal, "literal")
            _append_literal(segments, literal)
            position = len(value)
            break

        literal = value[position:opening]
        _validate_zamanalif(literal, "literal")
        _append_literal(segments, literal)
        closing = value.find("}}", opening + 2)
        if closing == -1:
            raise DslError(f"unclosed choice at position {opening}")
        body = value[opening + 2 : closing]
        if "{{" in body:
            raise DslError("nested choices are not allowed")
        segments.append(_parse_choice(body))
        position = closing + 2

    if not segments:
        raise DslError("conversion DSL produced no segments")
    return ConversionResult(tuple(segments))


def serialize_dsl(result: ConversionResult) -> str:
    """Serialize structured conversion output to canonical inline DSL."""
    parts: list[str] = []
    for segment in result.segments:
        if isinstance(segment, Literal):
            _validate_zamanalif(segment.text, "literal")
            parts.append(segment.text)
            continue
        rule = _validated_choice(segment)
        options = "|".join(f"{option_id}={text}" for option_id, text in rule.options)
        parts.append(f"{{{{{rule.rule_id}|{options}}}}}")
    value = "".join(parts)
    if not value:
        raise DslError("conversion DSL must not be empty")
    return value


def resolve_dsl(value: str, policy: Mapping[str, str] | None = None) -> str:
    """Parse DSL and resolve all choices using a policy or registered defaults."""
    return resolve_result(parse_dsl(value), policy)


def resolve_result(
    result: ConversionResult,
    policy: Mapping[str, str] | None = None,
) -> str:
    """Resolve structured conversion output to plain Zamanalif."""
    selected_policy = dict(policy or {})
    unknown_policy_rules = sorted(set(selected_policy) - set(RULES))
    if unknown_policy_rules:
        raise DslError(f"unknown policy rules: {', '.join(unknown_policy_rules)}")

    parts: list[str] = []
    for segment in result.segments:
        if isinstance(segment, Literal):
            _validate_zamanalif(segment.text, "literal")
            parts.append(segment.text)
            continue
        choice = _validated_choice(segment)
        definition = RULES[choice.rule_id]
        option_id = selected_policy.get(choice.rule_id, definition.default_option)
        options = dict(choice.options)
        if option_id not in options:
            raise DslError(f"unknown option {option_id!r} for rule {choice.rule_id}")
        parts.append(options[option_id])
    value = "".join(parts)
    _validate_zamanalif(value, "resolved conversion")
    if not value:
        raise DslError("resolved conversion must not be empty")
    return value


def _parse_choice(body: str) -> Choice:
    fields = body.split("|")
    if len(fields) < 3:
        raise DslError("a choice requires a rule id and at least two options")
    rule_id = fields[0]
    if not IDENTIFIER_RE.fullmatch(rule_id):
        raise DslError(f"invalid rule id: {rule_id!r}")
    if rule_id not in RULES:
        raise DslError(f"unknown rule id: {rule_id}")

    options: list[tuple[str, str]] = []
    seen: set[str] = set()
    for field in fields[1:]:
        if "=" not in field:
            raise DslError(f"invalid option in rule {rule_id}: {field!r}")
        option_id, text = field.split("=", 1)
        if not OPTION_RE.fullmatch(option_id):
            raise DslError(f"invalid option id in rule {rule_id}: {option_id!r}")
        if option_id in seen:
            raise DslError(f"duplicate option {option_id!r} in rule {rule_id}")
        _validate_zamanalif(text, f"option {rule_id}.{option_id}")
        seen.add(option_id)
        options.append((option_id, text))
    return _validated_choice(Choice(rule_id, tuple(options)))


def _validated_choice(choice: Choice) -> Choice:
    if choice.rule_id not in RULES:
        raise DslError(f"unknown rule id: {choice.rule_id}")
    definition = RULES[choice.rule_id]
    if definition.allow_custom_option_text:
        expected_option_ids = tuple(option_id for option_id, _ in definition.options)
        actual_option_ids = tuple(option_id for option_id, _ in choice.options)
        if actual_option_ids != expected_option_ids:
            raise DslError(
                f"options for {choice.rule_id} must be "
                + ", ".join(expected_option_ids)
            )
        return choice
    if choice.options != definition.options:
        raise DslError(
            f"options for {choice.rule_id} must be "
            + ", ".join(f"{key}={value}" for key, value in definition.options)
        )
    return choice


def _validate_zamanalif(value: str, context: str) -> None:
    invalid = sorted(set(value) - ZAMANALIF_CHARACTERS)
    if invalid:
        rendered = " ".join(repr(char) for char in invalid)
        raise DslError(f"invalid characters in {context}: {rendered}")


def _append_literal(segments: list[Segment], text: str) -> None:
    if not text:
        return
    if segments and isinstance(segments[-1], Literal):
        previous = segments[-1]
        segments[-1] = Literal(previous.text + text)
    else:
        segments.append(Literal(text))


def rule_ids(result: ConversionResult | Iterable[Segment]) -> tuple[str, ...]:
    """Return rule IDs in occurrence order for reports and review UI."""
    segments = result.segments if isinstance(result, ConversionResult) else tuple(result)
    return tuple(segment.rule_id for segment in segments if isinstance(segment, Choice))
