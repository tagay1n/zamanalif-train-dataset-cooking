"""Preview and apply corpus corrections through one transaction path."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .contextual_review import _token_spans, effective_contextual_homonym_words, validate_contextual_review
from .conversion import parse_dsl, resolve_dsl
from .corpus_index import CorpusAuditError, CorpusResolver, IndexWriter, connect, records, search, state
from .labelstudio_import import (
    _collapsed_family_member_variant,
    _family_member_variants,
    _family_member_zamanalif,
    _replace_stored_variants,
)
from .schema import Sample
from .training_export import CYRILLIC_RE, TrainingExportError, _validate_resolved_sentence
from .validate import validate_response
from .word_export import ReviewedWord, conversion_branches, is_compatible_family_member, normalize_word


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _required_text(item: dict, field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CorpusAuditError(f"{field} requires a nonempty string.")
    return value


def _index(item: dict, field: str, default=None) -> int:
    value = item.get(field, default)
    if type(value) is not int or value < 0:
        raise CorpusAuditError(f"{field} requires a nonnegative integer.")
    return value


def _spelling(item: dict, previous: str | None, *, cyrillic: bool = False) -> str:
    if "find" in item:
        fragment = _required_text(item, "find")
        replacement = item.get("replace")
        if previous is None or (not cyrillic and parse_dsl(previous).has_choices):
            raise CorpusAuditError("Use a full spelling or select a plain policy variant for this entry.")
        if fragment not in previous or not isinstance(replacement, str):
            raise CorpusAuditError(f"{fragment!r} must occur in {previous!r}, and replacement must be text.")
        return previous.replace(fragment, replacement)
    return _required_text(item, "value") if "value" in item or previous is None else previous


def _validate_spelling(value: str, policy: dict, *, plain: bool = False):
    parsed = parse_dsl(value)
    if not value.strip() or (plain and parsed.has_choices):
        raise CorpusAuditError("Enter a nonempty spelling for the selected variant.")
    _validate_resolved_sentence("replacement", resolve_dsl(value, policy))


def _render_sentence(text: str, spans) -> str | None:
    pieces = []
    cursor = 0
    for start, end, rendered in spans:
        if rendered is None:
            return None
        pieces.extend([text[cursor:start], rendered])
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _signature(review: ReviewedWord | None):
    return json.loads(_json(asdict(review))) if review else None


class CorrectionBatch:
    def __init__(self, path: Path, connection: sqlite3.Connection, policy: dict, now: str):
        self.path = path
        self.connection = connection
        self.policy = policy
        self.now = now
        self.before = CorpusResolver(path, connection, policy)
        self.words = dict(self.before.words)
        self.entries = []
        self.changed_words = set()
        self.changed_samples = set()
        self.source_entries = {}
        self.changed_tokens = set()
        self.new_contextual = set()
        self.new_dictionary = set()

    def save_word(self, review: ReviewedWord, *, reason: str, direct: bool = False):
        word = review.normalized_word
        old = self.words.get(word)
        if old == review:
            return
        self.connection.execute("""
            insert into reviewed_words(normalized_word, zamanalif_dsl, origin, updated_at)
            values (?, ?, ?, ?) on conflict(normalized_word) do update
            set zamanalif_dsl = excluded.zamanalif_dsl, origin = excluded.origin, updated_at = excluded.updated_at
        """, (word, review.zamanalif_dsl, review.origin, self.now))
        if old is None or old.variants != review.variants:
            _replace_stored_variants(self.connection, word, review.variants, self.now)
        if direct:
            for table in ("reviewed_word_derivations", "reviewed_word_compound_derivations"):
                self.connection.execute(f"delete from {table} where normalized_word = ?", (word,))
        self.words[word] = review
        self.changed_words.add(word)
        self.entries.append(dict(kind=reason, word=word, before=_signature(old), after=_signature(review)))

    def dictionary(self, item: dict):
        word = _required_text(item, "word")
        if normalize_word(word) != word:
            raise CorpusAuditError(f"Use the normalized Cyrillic word for {word!r}.")
        old = self.words.get(word, ReviewedWord(word, "", ""))
        origin = item.get("origin", old.origin)
        if origin not in ("N", "RL", "U"):
            raise CorpusAuditError(f"{word}: supply a dictionary origin of N, RL, or U.")
        variants = old.variants
        if "variant" in item:
            position = _index(item, "variant")
            if position >= len(variants):
                raise CorpusAuditError(f"{word}: unknown variant {position}.")
            value = _spelling(item, variants[position].zamanalif)
            _validate_spelling(value, self.policy, plain=True)
            dsl = old.zamanalif_dsl
            if len(variants) == 1 and dsl == variants[position].zamanalif:
                dsl = value
            variants = variants[:position] + (replace(variants[position], zamanalif=value),) + variants[position + 1:]
        else:
            if variants and ("value" in item or "find" in item):
                raise CorpusAuditError(f"{word}: select a stored policy variant to edit.")
            dsl = _spelling(item, old.zamanalif_dsl or None)
            _validate_spelling(dsl, self.policy)
        self.save_word(ReviewedWord(word, dsl, origin, variants), reason="dictionary", direct=True)
        if not old.zamanalif_dsl:
            self.new_dictionary.add(word)

    def save_context(self, sample_id: str, index: int, word: str, value: str, origin: str):
        validate_contextual_review(word, value, origin)
        _validate_spelling(value, self.policy)
        self.connection.execute("""
            insert into contextual_reviews(sample_id, token_index, normalized_word, zamanalif_dsl, origin, updated_at)
            values (?, ?, ?, ?, ?, ?) on conflict(sample_id, token_index) do update
            set normalized_word = excluded.normalized_word, zamanalif_dsl = excluded.zamanalif_dsl,
                origin = excluded.origin, updated_at = excluded.updated_at
        """, (sample_id, index, word, value, origin, self.now))

    def contextual(self, item: dict):
        sample_id = _required_text(item, "sample_id")
        index = _index(item, "token_index")
        row = self.connection.execute("""
            select f.cyrillic, f.source, r.zamanalif_dsl, r.origin
            from audit_occurrences o join audit_forms f on f.id = o.form_id
            left join contextual_reviews r on r.sample_id = o.sample_id and r.token_index = o.token_index
            where o.sample_id = ? and o.token_index = ?
        """, (sample_id, index)).fetchone()
        if row is None or row["source"] not in ("contextual", "contextual_homonym", "stale_contextual"):
            raise CorpusAuditError(f"{sample_id} token {index} has no applicable contextual decision.")
        origin = item.get("origin", row["origin"])
        value = _spelling(item, row["zamanalif_dsl"])
        previous = dict(origin=row["origin"], value=row["zamanalif_dsl"])
        current = dict(origin=origin, value=value)
        if previous != current or row["source"] == "stale_contextual":
            self.save_context(sample_id, index, row["cyrillic"], value, origin)
            self.entries.append(dict(kind="contextual", sample_id=sample_id, token_index=index,
                                     before=previous, after=current))
            self.changed_samples.add(sample_id)
            self.changed_tokens.add((sample_id, index))
            self.new_contextual.add((sample_id, index))

    def source(self, sample_id: str, changes: list[dict]):
        row = next(records(self.connection, {sample_id}), None)
        if row is None:
            raise CorpusAuditError(f"{sample_id}: no eligible sentence.")
        text = row["text"]
        tokens = json.loads(row["tokens_json"])
        context = {
            row["token_index"]: [row["normalized_word"], row["zamanalif_dsl"], row["origin"], row["updated_at"]]
            for row in self.connection.execute("""
                select * from contextual_reviews where sample_id = ?
            """, (sample_id,))
        }
        before = dict(text=text, tokens=tokens[:], contextual=dict(context))
        spans = _token_spans(sample_id, text, tokens)
        last_start = len(tokens)
        for change in sorted(changes, key=lambda x: _index(x, "start"), reverse=True):
            start = _index(change, "start")
            end = _index(change, "end", start + 1)
            if not start < end <= last_start:
                raise CorpusAuditError(f"{sample_id}: invalid or overlapping token range [{start}, {end}).")
            last_start = start
            left, right = spans[start][0], spans[end - 1][1]
            fragment = text[left:right]
            replacement = _spelling(change, fragment, cyrillic=True)
            added = change.get("tokens")
            if added is None and end == start + 1 and not any(char.isspace() for char in replacement):
                added = [dict(tokens[start], text=replacement)]
            if not isinstance(added, list) or not added:
                raise CorpusAuditError("Supply the exact replacement tokens and labels for a split or merge.")
            validated = validate_response(_json([dict(id=sample_id, tatar=True, tokens=added)]),
                                          [Sample(id=sample_id, text=replacement)])
            if not validated.ok or validated.warnings:
                raise CorpusAuditError("; ".join(validated.errors + validated.warnings))
            replacement_spans = _token_spans(sample_id, replacement, added)
            cursor = 0
            for begin, finish in [*replacement_spans, (len(replacement), len(replacement))]:
                if CYRILLIC_RE.search(replacement[cursor:begin]):
                    raise CorpusAuditError("All Cyrillic text in the replacement must belong to a token.")
                cursor = finish
            delta = len(added) - (end - start)
            old_context = {key: value for key, value in context.items() if start <= key < end}
            selected = change.get("contextual")
            if selected is None and old_context:
                if end == start + 1 and len(added) == 1 and normalize_word(added[0]["text"]) == old_context[start][0]:
                    selected = [{"offset": 0, "value": old_context[start][1], "origin": old_context[start][2]}]
                else:
                    raise CorpusAuditError("Supply contextual replacements, or an explicit empty contextual list.")
            if selected is not None and not isinstance(selected, list):
                raise CorpusAuditError("contextual must be an array of replacement decisions.")
            context = {key if key < start else key + delta: value
                       for key, value in context.items() if key < start or key >= end}
            self.new_contextual = {
                (sid, ix + delta if sid == sample_id and ix >= end else ix)
                for sid, ix in self.new_contextual if sid != sample_id or not start <= ix < end
            }
            created = set()
            for decision in selected or []:
                if not isinstance(decision, dict):
                    raise CorpusAuditError("Each contextual replacement must be an object.")
                offset = _index(decision, "offset")
                if offset >= len(added) or offset in created:
                    raise CorpusAuditError("Use a unique contextual offset within the replacement tokens.")
                created.add(offset)
                word = normalize_word(added[offset]["text"])
                value, origin = _required_text(decision, "value"), decision.get("origin")
                if not word:
                    raise CorpusAuditError("Contextual decisions require a Cyrillic word.")
                validate_contextual_review(word, value, origin)
                _validate_spelling(value, self.policy)
                decision = [word, value, origin, self.now]
                if old_context.get(start + offset, [])[:3] == decision[:3]:
                    decision = old_context[start + offset]
                else:
                    self.new_contextual.add((sample_id, start + offset))
                context[start + offset] = decision
            offset_change = len(replacement) - (right - left)
            spans[start:] = [(left + a, left + b) for a, b in replacement_spans] + [
                (a + offset_change, b + offset_change) for a, b in spans[end:]
            ]
            text = text[:left] + replacement + text[right:]
            tokens[start:end] = added
            self.changed_tokens.update((sample_id, i) for i in range(start, end))
        if (text, tokens, context) == (before["text"], before["tokens"], before["contextual"]):
            return
        if _token_spans(sample_id, text, tokens) != spans:
            raise CorpusAuditError(f"{sample_id}: the replacement would change another token's alignment.")
        self.connection.execute("update samples set text = ? where id = ?", (text, sample_id))
        self.connection.execute("""
            update preannotation_state set tokens_json = ?, updated_at = ? where sample_id = ?
        """, (_json(tokens), self.now, sample_id))
        self.connection.execute("delete from contextual_reviews where sample_id = ?", (sample_id,))
        self.connection.executemany("""
            insert into contextual_reviews(sample_id, token_index, normalized_word, zamanalif_dsl, origin, updated_at)
            values (?, ?, ?, ?, ?, ?)
        """, ((sample_id, index, *values) for index, values in sorted(context.items())))
        # Dates are stored in the backup. Exclude them to make preview replay deterministic.
        before["contextual"] = {key: val[:3] for key, val in before["contextual"].items()}
        after = dict(text=text, tokens=tokens, contextual={key: val[:3] for key, val in context.items()})
        entry = dict(kind="source", sample_id=sample_id, before=before, after=after)
        self.source_entries[sample_id] = entry
        self.entries.append(entry)
        self.changed_samples.add(sample_id)

    def family_dependents(self):
        rows = self.connection.execute("""
            select normalized_word, source_word, lemma from reviewed_word_derivations order by normalized_word
        """).fetchall()
        pending = list(rows)
        while pending:
            remaining = []
            for word, donor, lemma in pending:
                if donor not in self.changed_words:
                    remaining.append((word, donor, lemma))
                    continue
                source = self.words.get(donor)
                if source is None or not is_compatible_family_member(donor, word, lemma, source.origin):
                    raise CorpusAuditError(f"{word}: add an explicit correction for the review derived from {donor}.")
                source_base = conversion_branches(donor).suggestion(source.origin)
                member_base = conversion_branches(word).suggestion(source.origin)
                try:
                    variant = _collapsed_family_member_variant(
                        source_base, source.zamanalif_dsl, source.variants, member_base,
                    )
                    if variant:
                        value, variants = variant.zamanalif, (variant,)
                    else:
                        value = _family_member_zamanalif(source_base, source.zamanalif_dsl, member_base)
                        variants = _family_member_variants(source_base, source.variants, member_base) if source.variants else ()
                except ValueError:
                    value, variants = "", ()
                if not value or (source.variants and not variants):
                    raise CorpusAuditError(f"{word}: add an explicit correction for the review derived from {donor}.")
                self.save_word(ReviewedWord(word, value, source.origin, variants), reason="family")
            if len(remaining) == len(pending):
                break
            pending = remaining

    def compound_dependents(self):
        rows = self.connection.execute("""
            select normalized_word, components_json from reviewed_word_compound_derivations order by normalized_word
        """).fetchall()
        for word, raw in rows:
            if word not in self.before.words:
                continue
            components = json.loads(raw)
            if not any(
                {part["word"], *(source["word"] for source in part["source_reviews"])} & self.changed_words
                for part in components
            ):
                continue
            for part in components:
                self.refresh_component(word, part)
            value = "-".join(part["dsl"] for part in components)
            self.save_word(ReviewedWord(word, value, "U"), reason="compound")
            self.connection.execute("""
                update reviewed_word_compound_derivations set components_json = ? where normalized_word = ?
            """, (_json(components), word))
            self.entries.append(dict(kind="compound evidence", word=word, before=json.loads(raw), after=components))

    def refresh_component(self, compound: str, part: dict):
        word = part["word"]
        review = self.words.get(word)
        status = part["status"]
        if status == "existing_family_rule" and review is None:
            family = part["family"]
            donor = family["source_word"]
            source = self.words.get(donor)
            origins = {self.words[item["word"]].origin for item in part["source_reviews"]
                       if item["word"] in self.words}
            if source and not source.variants and origins == {source.origin} and is_compatible_family_member(
                donor, word, family["lemma"], source.origin,
            ):
                value = _family_member_zamanalif(
                    conversion_branches(donor).suggestion(source.origin), source.zamanalif_dsl,
                    conversion_branches(word).suggestion(source.origin),
                )
                if value:
                    part.update(dsl=value, origin=source.origin)
                    for item in part["source_reviews"]:
                        item["signature"] = _signature(self.words.get(item["word"]))
                    return
        elif review and not review.variants and not parse_dsl(review.zamanalif_dsl).has_choices:
            signature = _signature(review)
            derivation = self.connection.execute("""
                select source_word, lemma, part_of_speech, analyzer_revision
                from reviewed_word_derivations where normalized_word = ?
            """, (word,)).fetchone()
            if not derivation or derivation[0] in self.words:
                part.pop("derivation", None)
                part.pop("family", None)
                part.update(
                    dsl=review.zamanalif_dsl, origin=review.origin, component_review=signature,
                    status="inherited_review" if derivation else "direct_review",
                    source_reviews=[dict(word=word, signature=signature)],
                )
                if derivation:
                    part["derivation"] = list(derivation)
                    part["source_reviews"].append(dict(word=derivation[0], signature=_signature(self.words[derivation[0]])))
                return
        elif status == "deterministic" and review is None:
            return
        raise CorpusAuditError(f"{compound}: add an explicit correction for the compound affected by {word}.")

    def rebuild_affected(self):
        resolver = CorpusResolver(self.path, self.connection, self.policy)
        if self.new_dictionary:
            homonyms = self.new_dictionary & effective_contextual_homonym_words(self.connection)
            if homonyms:
                raise CorpusAuditError(f"{min(homonyms)}: use occurrence-level corrections for a contextual homonym.")
        if invalid := self.changed_words - resolver.words.keys():
            raise CorpusAuditError(f"Dependent review needs an explicit correction: {min(invalid)}.")
        affected = set(self.changed_samples)
        affected_occurrences = set(self.changed_tokens)
        for word in self.changed_words:
            for row in self.connection.execute("""
                select o.sample_id, o.token_index
                from audit_forms f join audit_occurrences o on o.form_id = f.id
                where f.cyrillic = ? and f.source not in ('contextual', 'contextual_homonym')
            """, (word,)):
                affected.add(row["sample_id"])
                affected_occurrences.add((row["sample_id"], row["token_index"]))
        writer = IndexWriter(self.connection)
        old_forms = set()
        examples = []
        for row in records(self.connection, affected):
            sample_id = row["id"]
            old = list(self.connection.execute("""
                select o.*, f.source from audit_occurrences o join audit_forms f on f.id = o.form_id
                where o.sample_id = ? order by o.token_index
            """, (sample_id,)))
            tokens = json.loads(row["tokens_json"])
            resolved = resolver.sentence(sample_id, row["text"], tokens)
            for ix, (_, _, token) in enumerate(resolved):
                if (sample_id, ix) in self.new_contextual and token.source != "contextual":
                    raise CorpusAuditError(f"{sample_id} token {ix}: contextual decision would have no effect.")
            entry = self.source_entries.get(sample_id)
            old_text = entry["before"]["text"] if entry else row["text"]
            before = _render_sentence(old_text, (
                (token["start_offset"], token["end_offset"], token["rendered"]) for token in old
            ))
            after = _render_sentence(row["text"], (
                (start, end, token.text) for start, end, token in resolved
            ))
            if before is not None:
                try:
                    _validate_resolved_sentence(sample_id, before)
                except TrainingExportError:
                    before = None
            if before is not None:
                if after is None:
                    raise CorpusAuditError(f"{sample_id}: this correction would leave the sentence unresolved.")
                _validate_resolved_sentence(sample_id, after)
            if entry:
                entry["before"]["zamanalif"] = before
                entry["after"]["zamanalif"] = after
            previous = {token["token_index"]: token["rendered"] for token in old}
            if sample_id not in self.changed_samples or len(resolved) == len(old):
                for ix, (_, _, token) in enumerate(resolved):
                    if ix in previous and previous[ix] != token.text and len(examples) < 20:
                        examples.append(dict(sample_id=sample_id, token_index=ix,
                                             before=previous[ix], after=token.text))
            old_forms.update(token["form_id"] for token in old)
            self.connection.execute("delete from audit_occurrences where sample_id = ?", (sample_id,))
            writer.sentence(sample_id, tokens, resolved)
        for form_id in sorted(old_forms):
            if not self.connection.execute("select 1 from audit_occurrences where form_id = ?", (form_id,)).fetchone():
                self.connection.execute("delete from audit_forms where id = ?", (form_id,))
                self.connection.execute("delete from audit_fts where rowid = ?", (form_id,))
        self.connection.execute("update audit_state set index_revision = revision")
        return dict(words=len(self.changed_words), occurrences=len(affected_occurrences),
                    sentences=len(affected), examples=examples)

    def expand_bulk(self, item: dict):
        target = item.get("target")
        fragment = _required_text(item, "find")
        replacement = item.get("replace")
        if target not in ("dictionary", "contextual", "source") or not isinstance(replacement, str):
            raise CorpusAuditError("Choose dictionary, contextual or source and supply replacement text.")
        matches = search(self.connection, item.get("field", ""), item.get("mode", ""),
                         _required_text(item, "query"), limit=-1)
        form_ids = item.get("form_ids")
        if form_ids is not None:
            if not isinstance(form_ids, list) or any(type(value) is not int for value in form_ids):
                raise CorpusAuditError("form_ids must contain numeric search result IDs.")
            matching_ids = {match["form_id"] for match in matches}
            if set(form_ids) - matching_ids:
                raise CorpusAuditError("A selected form is outside the search results.")
            matches = [match for match in matches if match["form_id"] in form_ids]
        if target == "dictionary":
            field = {}
            if "variant" in item:
                field["variant"] = _index(item, "variant")
            seen = set()
            for match in matches:
                word = match["cyrillic"]
                review = self.words.get(word)
                if review is None or match["source"] != "dictionary" or word in seen:
                    continue
                seen.add(word)
                position = field.get("variant")
                if position is not None and position >= len(review.variants):
                    continue
                value = review.variants[position].zamanalif if position is not None else review.zamanalif_dsl
                if fragment in value:
                    yield dict(kind=target, word=word, find=fragment, replace=replacement, **field)
            return
        for match in matches:
            if target == "contextual" and match["source"] != "contextual":
                continue
            for row in self.connection.execute("""
                select o.sample_id, o.token_index, o.surface, r.zamanalif_dsl
                from audit_occurrences o left join contextual_reviews r
                on r.sample_id = o.sample_id and r.token_index = o.token_index
                where o.form_id = ? order by o.sample_id, o.token_index
            """, (match["form_id"],)):
                if target == "source" and fragment in row["surface"]:
                    yield dict(kind=target, sample_id=row["sample_id"], start=row["token_index"],
                               find=fragment, replace=replacement)
                elif target == "contextual" and fragment in row["zamanalif_dsl"]:
                    yield dict(kind=target, sample_id=row["sample_id"], token_index=row["token_index"],
                               find=fragment, replace=replacement)

    def run(self, changes: list[dict]) -> dict:
        source_changes = defaultdict(list)
        expanded = []
        for item in changes:
            if not isinstance(item, dict):
                raise CorpusAuditError("Each change must be an object.")
            expanded.extend(self.expand_bulk(item) if item.get("kind") == "bulk" else [item])
        for item in expanded:
            kind = item.get("kind")
            if kind == "dictionary":
                self.dictionary(item)
            elif kind == "contextual":
                self.contextual(item)
            elif kind == "source":
                source_changes[_required_text(item, "sample_id")].append(item)
            else:
                raise CorpusAuditError(f"Unknown correction type {kind!r}.")
        for sample_id, group in sorted(source_changes.items()):
            self.source(sample_id, group)
        if not self.entries:
            raise CorpusAuditError("No matching changes to preview.")
        if self.changed_words:
            self.family_dependents()
            self.compound_dependents()
        summary = self.rebuild_affected()
        return dict(changes=self.entries, **summary)


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _backup(path: Path, now: datetime) -> Path:
    stamp = now.strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.stem}.before-audit-{stamp}-{uuid4().hex[:8]}.sqlite")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
    return backup


def correct(path: Path, changes: list[dict], *, approved: dict | None = None) -> dict:
    if not isinstance(changes, list) or not changes:
        raise CorpusAuditError("Supply a nonempty array of changes.")
    now = datetime.now(timezone.utc)
    backup = None
    committed = False
    try:
        with closing(connect(path)) as connection, connection:
            connection.execute("begin immediate")
            current = state(connection)
            base = dict(database=current["identity"], revision=current["revision"], request=changes)
            if approved is not None:
                if any(approved.get(key) != value for key, value in base.items()):
                    raise CorpusAuditError("The preview is stale. Preview the correction again.")
                backup = _backup(path, now)
            batch = CorrectionBatch(path, connection, json.loads(current["policy_json"]), now.isoformat())
            result = batch.run(changes)
            # Round-trip numeric JSON object keys before hashing or comparing.
            preview = json.loads(json.dumps(dict(**base, **result), ensure_ascii=False))
            preview["confirmation"] = _digest(preview)
            if approved is None:
                connection.rollback()
                return preview
            if approved.get("confirmation") != preview["confirmation"]:
                raise CorpusAuditError("The preview changed. Preview the correction again.")
            cursor = connection.execute("""
                insert into audit_history(created_at, backup, preview_json) values (?, ?, ?)
            """, (now.isoformat(), str(backup), _json(preview)))
            connection.commit()
            committed = True
            return dict(**preview, backup=str(backup), correction_id=cursor.lastrowid)
    finally:
        if backup is not None and not committed:
            backup.unlink(missing_ok=True)
