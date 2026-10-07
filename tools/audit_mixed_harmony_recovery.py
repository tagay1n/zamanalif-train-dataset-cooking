#!/usr/bin/env python3
"""Read-only mixed-harmony recovery evidence; never writes review state.

Run with the project's Python environment. JSON contains candidate spellings,
not new approvals. Expanded suffix evidence is an experimental audit rule.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

from tatar_preannotator import word_export as converter
from tatar_preannotator.conversion import RULES, parse_dsl, resolve_dsl
from tatar_preannotator.labelstudio_import import (
    _collapsed_family_member_variant, _family_member_variants,
    _family_member_zamanalif,
)
from tatar_preannotator.morphology import (
    ApertiumTatarAnalyzer, MorphIdentity, _escape_apertium_stream,
    _first_unescaped, _lexical_units, _split_unescaped, _unescape,
)
from tatar_preannotator.training_export import (
    _reviewed_variant_for_policy, _validate_resolved_sentence,
)
from tatar_preannotator.word_export import (
    CYRILLIC_RE, FAMILY_DIVERGENCE_RISK_LETTERS, ReviewedVariant, ReviewedWord,
    _sqlite_records_from_connection, annotation_variants, conversion_branches,
    is_compatible_family_member, normalize_word, vowel_harmony_class,
)

POLICY = {name: rule.default_option for name, rule in RULES.items()}
INFLECTION_FRAGMENTS = frozenset({
    "лар", "ләр", "нар", "нәр", "лары", "ләре", "ларын", "ләрен",
    "ларының", "ләренең", "га", "гә", "ка", "кә",
})


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plain(dsl):
    value = resolve_dsl(dsl, POLICY)
    _validate_resolved_sentence("audit", value)
    if not value:
        raise ValueError("empty audit output")
    return value


def reviewed_plain(review):
    return _reviewed_variant_for_policy(review, POLICY) or plain(review.zamanalif_dsl)


def trace_empty_conditionals(words):
    """Trace actual character calls, including the converter's compound paths.

    Instrumentation exists only inside this read-only audit process. It keeps
    each return value unchanged and restores the original function afterwards.
    Hard-sign pairs and surname sequences are consumed before character calls.
    """
    original = converter._char_conversion
    found = {}
    current = []

    def traced(char, context_word, index, label):
        value = original(char, context_word, index, label)
        if char in "гкяюе" and not value:
            current.append({"char": char, "context_word": context_word,
                            "index": index, "branch": label})
        return value

    try:
        converter._char_conversion = traced
        for word in words:
            current = []
            converter._convert_known_label(word, "N")
            if current:
                found[word] = current
    finally:
        converter._char_conversion = original
    return found


def analyze_alternatives(analyzer, words):
    """Retain multiple lemma/POS possibilities for diagnostics, not approvals."""
    ordered = sorted(set(words))
    result = subprocess.run(
        ["lt-proc", "-w", str(analyzer.data_dir / "tat.automorf.bin")],
        input="".join(_escape_apertium_stream(w) + "\n" for w in ordered),
        text=True, encoding="utf-8", capture_output=True, check=True, timeout=120,
    )
    lines = result.stdout.splitlines()
    if len(lines) != len(ordered):
        raise ValueError("Alternative morphology result count mismatch")
    details = {}
    for word, line in zip(ordered, lines, strict=True):
        units = _lexical_units(line)
        identities = set()
        if len(units) == 1:
            for analysis in _split_unescaped(units[0], "/")[1:]:
                if analysis.startswith("*") or "<err_orth>" in analysis:
                    continue
                start = _first_unescaped(analysis, "<")
                end = analysis.find(">", start + 1)
                if start > 0 and end > start:
                    identities.add(MorphIdentity(_unescape(analysis[:start]).lower(), analysis[start + 1:end]))
        status = ("multiple_lexical_units" if len(units) != 1 else
                  "unknown" if not identities else
                  "multiple_families" if len(identities) > 1 else "single_family")
        details[word] = {"status": status, "identities": [asdict(i) for i in sorted(identities)]}
    return details


def policy_compatible(source, candidate, origin):
    left = conversion_branches(source).suggestion(origin)
    right = conversion_branches(candidate).suggestion(origin)
    if not left or not right:
        return False
    policies = {p for v in annotation_variants(left) for p in v.policies}
    return all(p in policies for v in annotation_variants(right) for p in v.policies)


def suffix_evidence(source, candidate, lemma, origin):
    """Prove stable full-word prefixes and equal N/RL divergent DSL tails.

    This retains literal lemma matching. Stem alternations are not inferred.
    Both donor and candidate tails must agree between origin branches; a
    suffix with a conditional letter is not accepted merely because N exists.
    """
    if source == candidate or not lemma or not source.startswith(lemma) or not candidate.startswith(lemma):
        return None
    if not any(c in FAMILY_DIVERGENCE_RISK_LETTERS for c in lemma):
        return None
    length = 0
    for a, b in zip(source, candidate):
        if a != b:
            break
        length += 1
    prefix = candidate[:length]
    if length < len(lemma) or not all(CYRILLIC_RE.fullmatch(c) for c in source[length:] + candidate[length:]):
        return None
    prefix_branches = conversion_branches(prefix)
    if not prefix_branches.native_dsl or not prefix_branches.loanword_dsl:
        return None
    tails = {}
    for word in (source, candidate):
        branches = conversion_branches(word)
        if not branches.native_dsl.startswith(prefix_branches.native_dsl) or not branches.loanword_dsl.startswith(prefix_branches.loanword_dsl):
            return None
        native = branches.native_dsl[len(prefix_branches.native_dsl):]
        loan = branches.loanword_dsl[len(prefix_branches.loanword_dsl):]
        if native != loan:
            return None
        # Parsing separately ensures the boundary does not split a DSL choice.
        if native:
            parse_dsl(native)
        tails[word] = native
    if not policy_compatible(source, candidate, origin):
        return None
    return {"shared_cyrillic_prefix": prefix, "source_tail_dsl": tails[source],
            "candidate_tail_dsl": tails[candidate]}


def transfer(source, candidate, review):
    left = conversion_branches(source).suggestion(review.origin)
    right = conversion_branches(candidate).suggestion(review.origin)
    collapsed = _collapsed_family_member_variant(left, review.zamanalif_dsl, review.variants, right)
    if collapsed is not None:
        dsl, variants = collapsed.zamanalif, (collapsed,)
    else:
        dsl = _family_member_zamanalif(left, review.zamanalif_dsl, right)
        variants = _family_member_variants(left, review.variants, right)
    if not dsl or (review.variants and not variants):
        return None
    inherited = ReviewedWord(candidate, dsl, review.origin, variants)
    preferred = reviewed_plain(inherited)
    for variant in variants:
        _validate_resolved_sentence("audit", variant.zamanalif)
    return {"source_word": source, "source_reviewed_dsl": review.zamanalif_dsl,
            "source_variants": [asdict(v) for v in review.variants],
            "origin": review.origin, "dsl": dsl, "preferred": preferred,
            "variants": [asdict(v) for v in variants],
            "human_correction_transferred": preferred != plain(right)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/zamanalif.sqlite"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    db = args.database.resolve()
    if args.output.resolve() == db:
        raise ValueError("Audit output must not overwrite the database")
    before = fingerprint(db)
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    conn.execute("pragma query_only=on")
    conn.execute("begin")
    resolutions = dict(conn.execute("select normalized_word, decision from word_resolutions"))
    derived = {r[0]: tuple(r[1:]) for r in conn.execute(
        "select normalized_word, source_word, lemma, part_of_speech, analyzer_revision from reviewed_word_derivations")}
    variants = defaultdict(list)
    for word, value, policies in conn.execute(
        "select normalized_word, zamanalif, policies_json from reviewed_word_variants order by normalized_word, position"):
        variants[word].append(ReviewedVariant(value, tuple(tuple(p.items()) for p in json.loads(policies))))
    reviews = {w: ReviewedWord(w, dsl, origin, tuple(variants[w])) for w, dsl, origin in conn.execute(
        "select normalized_word, zamanalif_dsl, origin from reviewed_words")}
    freq, labels, homonyms = Counter(), defaultdict(set), set()
    contexts = defaultdict(list)
    print("Collecting annotated word forms", flush=True)
    for record in _sqlite_records_from_connection(conn):
        if not record["tatar"]:
            continue
        for index, token in enumerate(record["tokens"]):
            if not isinstance(token, dict) or not isinstance(token.get("text"), str):
                continue
            word = normalize_word(token["text"])
            if not word:
                continue
            label = token.get("label", "U")
            label = label if label in {"N", "RL", "U"} else "U"
            resolution = resolutions.get(word)
            if token.get("homonym") is True and resolution not in {"N", "RL", "U"}:
                homonyms.add(word)
            labels[word].add(resolution if resolution in {"N", "RL", "U"} else label)
            freq[word] += 1
            if len(contexts[word]) < 3:
                contexts[word].append({"sample_id": record["id"], "token_index": index, "text": record["text"]})
    blocked = homonyms | {w for w, v in resolutions.items() if v == "contextual_homonym"}
    excluded = sorted(w for w in freq if labels[w] == {"N"} and w not in blocked
                      and w not in reviews and vowel_harmony_class(w) == "mixed_front_back"
                      and conversion_branches(w).state != "origin_independent")
    print(f"Found {len(excluded)} excluded forms / {sum(freq[w] for w in excluded)} occurrences", flush=True)
    silent_omissions = trace_empty_conditionals(excluded)
    compounds = [w for w in excluded if "-" in w]
    components = sorted({p for w in compounds for p in w.split("-") if p})
    direct = {w: r for w, r in reviews.items() if w not in derived and w not in blocked and r.origin in {"N", "RL"}}
    inherited_needed = {w for w in components if w in derived}
    analyze_words = set(excluded) | set(components) | set(direct)
    analyze_words.update(w.replace("-", "") for w in compounds)
    for w in inherited_needed:
        analyze_words.update((w, derived[w][0]))
    analyzer = ApertiumTatarAnalyzer()
    print(f"Analyzing {len(analyze_words)} forms with pinned Apertium", flush=True)
    analyses = analyzer.analyze(sorted(analyze_words))
    alternative_words = {w for w in excluded if analyses.get(w) is None}
    alternative_words.update(w.replace("-", "") for w in compounds)
    alternatives = analyze_alternatives(analyzer, alternative_words)
    anchors = defaultdict(list)
    for word in direct:
        if analyses.get(word) is not None:
            anchors[analyses[word]].append(word)

    @lru_cache(maxsize=None)
    def family(word):
        identity = analyses.get(word)
        if identity is None:
            return {"status": "ambiguous_or_unknown_morphology"}
        donors = sorted(anchors.get(identity, []), key=lambda w: (w != identity.lemma, len(w), w))
        base = {"lemma": identity.lemma, "part_of_speech": identity.part_of_speech,
                "direct_donor_count": len(donors), "direct_donors": donors}
        if not donors:
            return {**base, "status": "no_direct_reviewed_family"}
        if len({direct[s].origin for s in donors}) != 1:
            return {**base, "status": "conflicting_human_origins"}
        results, failures = [], Counter()
        for source in donors:
            review = direct[source]
            strict = is_compatible_family_member(source, word, identity.lemma, review.origin)
            evidence = suffix_evidence(source, word, identity.lemma, review.origin)
            if not strict and evidence is None:
                failures["stem_or_nondeterministic_suffix"] += 1
                continue
            result = transfer(source, word, review)
            if result is None:
                failures["human_spelling_or_policy_not_transferable"] += 1
                continue
            results.append({**result, "tier": "existing_family_rule" if strict else "expanded_suffix_evidence",
                            "suffix_evidence": evidence})
        if not results:
            structure = ("candidate_does_not_start_with_lemma" if not word.startswith(identity.lemma)
                         else "donors_do_not_start_with_lemma" if not any(s.startswith(identity.lemma) for s in donors)
                         else "literal_stem_suffix_or_transfer_blocked")
            return {**base, "status": "no_transferable_donor", "stem_structure": structure,
                    "failure_counts": dict(failures)}
        signatures = {json.dumps({k: r[k] for k in ("origin", "dsl", "variants")}, sort_keys=True) for r in results}
        if len(signatures) != 1:
            return {**base, "status": "conflicting_transferred_spellings", "transfers": results}
        result = next((r for r in results if r["tier"] == "existing_family_rule"), results[0])
        return {**base, "status": result["tier"], "result": result,
                "agreeing_sources": [r["source_word"] for r in results], "failure_counts": dict(failures)}

    def component(word, allow_expanded=False):
        if word in blocked:
            return {"status": "contextual_homonym", "resolved": False}
        review = reviews.get(word)
        if review is not None:
            if word in derived:
                source, lemma, pos, revision = derived[word]
                identity = analyses.get(word)
                source_identity = analyses.get(source)
                source_review = reviews.get(source)
                if revision != analyzer.revision or source_review is None or identity is None or identity != source_identity or (identity.lemma, identity.part_of_speech) != (lemma, pos) or not is_compatible_family_member(source, word, lemma, source_review.origin):
                    return {"status": "invalid_inherited_review", "resolved": False}
            return {"status": "inherited_review" if word in derived else "direct_review", "resolved": True,
                    "preferred": reviewed_plain(review), "review": asdict(review), "derivation": derived.get(word)}
        branches = conversion_branches(word)
        if branches.state == "origin_independent":
            return {"status": "deterministic", "resolved": True, "dsl": branches.native_dsl, "preferred": plain(branches.native_dsl)}
        evidence = family(word)
        if evidence["status"] == "existing_family_rule" or (allow_expanded and evidence["status"] == "expanded_suffix_evidence"):
            return {"status": evidence["status"], "resolved": True, "preferred": evidence["result"]["preferred"], "family": evidence}
        return {"status": "unresolved", "resolved": False, "family": evidence}

    print("Checking direct review transfer, suffix evidence, and compounds", flush=True)
    families = {w: family(w) for w in excluded}
    component_details = {w: component(w, True) for w in components}
    compound_details = {}
    for word in compounds:
        parts = word.split("-")
        joined = word.replace("-", "")
        joined_identity = analyses.get(joined)
        flags = []
        if joined in reviews:
            flags.append("joined_spelling_has_review")
        if joined_identity is not None:
            flags.append("joined_spelling_has_morphology")
        elif alternatives[joined]["identities"]:
            flags.append("joined_spelling_has_ambiguous_morphology")
        if joined in freq:
            flags.append("joined_spelling_present_in_annotated_corpus")
        if any(len(p) < 2 for p in parts):
            flags.append("empty_or_single_letter_component")
        if any(p in INFLECTION_FRAGMENTS for p in parts):
            flags.append("possible_inflection_fragment")
        tiers = {}
        for tier in ("exact_components", "existing_family_components", "expanded_family_components"):
            entries = [component(p, tier == "expanded_family_components") for p in parts]
            allowed = {"direct_review", "inherited_review", "deterministic"}
            if tier != "exact_components":
                allowed.add("existing_family_rule")
            if tier == "expanded_family_components":
                allowed.add("expanded_suffix_evidence")
            if not flags and all(e["resolved"] and e["status"] in allowed for e in entries):
                value = "-".join(e["preferred"] for e in entries)
                _validate_resolved_sentence("audit", value)
                tiers[tier] = value
        compound_details[word] = {"frequency": freq[word], "parts": parts,
                                  "component_statuses": [component_details[p]["status"] for p in parts],
                                  "boundary_flags": flags, "joined_identity": asdict(joined_identity) if joined_identity else None,
                                  "joined_alternative_morphology": alternatives[joined],
                                  "recovery_tiers": tiers}

    strict = {w for w, e in families.items() if e["status"] == "existing_family_rule"}
    expanded = {w for w, e in families.items() if e["status"] == "expanded_suffix_evidence"}
    exact_compounds = {w for w, e in compound_details.items() if "exact_components" in e["recovery_tiers"]}
    strict_compounds = {w for w, e in compound_details.items() if "existing_family_components" in e["recovery_tiers"]}
    expanded_compounds = {w for w, e in compound_details.items() if "expanded_family_components" in e["recovery_tiers"]}
    established = strict | strict_compounds
    potential = established | expanded | expanded_compounds
    # Never silently prefer a family proposal over conflicting component evidence.
    conflicts = {}
    for word in (strict | expanded) & expanded_compounds:
        left = families[word]["result"]["preferred"]
        right = compound_details[word]["recovery_tiers"]["expanded_family_components"]
        if left != right:
            conflicts[word] = {"family": left, "components": right}
            established.discard(word)
            potential.discard(word)

    gaps = defaultdict(list)
    native_complete = []
    for word in excluded:
        branches = conversion_branches(word)
        if not branches.native_dsl:
            gaps["NO_NATIVE_OUTPUT"].append(word)
            continue
        native = plain(branches.native_dsl)
        native_complete.append(word)
        loan = plain(branches.loanword_dsl) if branches.loanword_dsl else ""
        changes = sorted({native[a:b] + " → " + loan[c:d] for tag, a, b, c, d in SequenceMatcher(None, native, loan, autojunk=False).get_opcodes() if tag != "equal"})
        gaps["; ".join(changes)].append(word)

    remaining = set(excluded) - potential
    family_groups = defaultdict(list)
    for word in remaining:
        identity = analyses.get(word)
        if identity is not None:
            family_groups[identity].append(word)
    group_report = sorted(({
        "lemma": identity.lemma, "part_of_speech": identity.part_of_speech,
        "forms": sorted(words), "form_count": len(words), "occurrences": sum(freq[w] for w in words),
        "direct_reviewed_donors": sorted(anchors.get(identity, [])),
        "examples": [{"word": w, "frequency": freq[w], "native_default": plain(conversion_branches(w).native_dsl) if conversion_branches(w).native_dsl else None,
                      "family_status": families[w]["status"]} for w in sorted(words, key=lambda w: (-freq[w], w))[:5]],
    } for identity, words in family_groups.items()), key=lambda e: (-e["occurrences"], e["lemma"]))

    separators = {}
    for word in excluded:
        if "\u00ad" in word:
            joined = word.replace("\u00ad", "")
            separators[word] = {"kind": "soft_hyphen", "joined": joined,
                                "joined_reviewed": joined in reviews,
                                "joined_origin_independent": conversion_branches(joined).state == "origin_independent"}
        elif "–" in word:
            separators[word] = {"kind": "en_dash", "parts": word.split("–"),
                                "note": "Not included in ASCII-hyphen composition counts"}

    def coverage(words):
        return {"forms": len(words), "occurrences": sum(freq[w] for w in words)}

    summary = {
        "excluded": coverage(excluded), "hyphenated": coverage(compounds),
        "family_status_counts": dict(Counter(e["status"] for e in families.values())),
        "existing_family_rule": coverage(strict), "expanded_suffix_evidence": coverage(expanded),
        "exact_component_compounds": coverage(exact_compounds),
        "existing_family_component_compounds_total": coverage(strict_compounds),
        "expanded_family_component_compounds_total": coverage(expanded_compounds),
        "compound_boundary_flags": coverage({w for w, e in compound_details.items() if e["boundary_flags"]}),
        "established_evidence_union": coverage(established),
        "expanded_evidence_union": coverage(potential),
        "additional_expanded_evidence": coverage(potential - established),
        "remaining_after_expanded_evidence": coverage(remaining),
        "cross_route_conflicts": conflicts,
        "native_branch_available": coverage(native_complete),
        "native_silent_conditional_omissions": coverage(silent_omissions),
        "native_omissions_unresolved_after_proposed_recovery": coverage(set(silent_omissions) - potential),
        "remaining_unambiguous_family_forms": sum(len(v) for v in family_groups.values()),
        "remaining_unambiguous_families": len(family_groups),
        "remaining_unknown_or_ambiguous_forms": sum(analyses.get(w) is None for w in remaining),
        "remaining_morphology_failure_counts": dict(Counter(alternatives[w]["status"] for w in remaining if analyses.get(w) is None)),
        "remaining_families_with_direct_donor": sum(bool(anchors.get(i)) for i in family_groups),
        "remaining_families_without_direct_donor": sum(not anchors.get(i) for i in family_groups),
        "untransferable_donor_structure_counts": dict(Counter(e["stem_structure"] for e in families.values()
                                                              if e["status"] == "no_transferable_donor")),
        "expanded_human_corrections_preserved": sum(families[w]["result"]["human_correction_transferred"] for w in expanded),
    }
    conn.close()
    after = fingerprint(db)
    if after != before:
        raise RuntimeError("Database changed during audit; discard inconsistent audit snapshot")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "read_only": True,
        "database": str(args.database), "database_sha256_before": before, "database_sha256_after": after,
        "analyzer_revision": analyzer.revision, "default_policy": POLICY,
        "limitations": ["Expanded suffix evidence is experimental, not an approval.",
                        "Literal shared lemmas only; no inferred stem alternations.",
                        "Morphology ambiguity/unknowns remain unresolved, not counted as unique lexical families.",
                        "Compound boundaries without flags are candidates, not linguistic proof.",
                        "Full compound policy variants must be preserved by any recovery implementation.",
                        "Native/RL differences are patterns of origin dependence, not demonstrated converter errors.",
                        "Token occurrence coverage does not equal newly exportable sentence coverage."],
        "summary": summary,
        "forms": {w: {"frequency": freq[w], "contexts": contexts[w], "family": families[w],
                      "native_dsl": conversion_branches(w).native_dsl, "loanword_dsl": conversion_branches(w).loanword_dsl,
                      "established_recovery_candidate": w in established, "expanded_recovery_candidate": w in potential}
                  for w in excluded},
        "components": component_details, "compounds": compound_details,
        "remaining_families": group_report, "other_separators": separators,
        "alternative_morphology": alternatives,
        "native_silent_conditional_omissions": {
            w: {"frequency": freq[w], "empty_character_calls": calls,
                "native_dsl": conversion_branches(w).native_dsl,
                "covered_by_recovery_candidate": w in potential}
            for w, calls in silent_omissions.items()},
        "disagreement_patterns": sorted(({"pattern": pattern, **coverage(words),
            "examples": [{"word": w, "frequency": freq[w], "native": plain(conversion_branches(w).native_dsl) if conversion_branches(w).native_dsl else None,
                          "loanword": plain(conversion_branches(w).loanword_dsl) if conversion_branches(w).loanword_dsl else None}
                         for w in sorted(words, key=lambda w: (-freq[w], w))[:8]]}
            for pattern, words in gaps.items()), key=lambda e: (-e["occurrences"], e["pattern"])),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"Evidence saved to {args.output}; database hash unchanged", flush=True)


if __name__ == "__main__":
    main()
