from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from zamanalif_selector.features import count_tatar_specific_letters

from . import db
from .manual_preannotate import (
    ManualPreannotateError,
    _count_unprocessable,
    _load_prior_token_stats,
    _load_unprocessable,
    build_editable_tokens,
    save_manual_item,
)
from .schema import Sample
from .validate import validate_response
from .word_export import load_reviewed_words


LOCAL_REPAIR_MODEL_NAME = "local-repair"
MIN_TATAR_SPECIFIC_LETTERS = 2


@dataclass(frozen=True)
class LocalRepairSummary:
    """Counts produced by local unprocessable-row repair."""

    total: int
    repaired: int
    skipped_low_tatar_specific: int
    skipped_no_tokens: int
    invalid: int
    remaining: int
    dry_run: bool


def repair_unprocessable(
    db_path: str | Path,
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> LocalRepairSummary:
    """Repair unprocessable rows using local tokenization and prior word statistics."""
    database = Path(db_path)
    if not database.exists():
        raise ManualPreannotateError(f"database file does not exist: {database}")

    with db.connect(database) as conn:
        db.ensure_preannotation_schema(conn)
        reviewed = load_reviewed_words(database)
        prior_labels, prior_homonyms = _load_prior_token_stats(conn)
        rows = _load_unprocessable(conn, limit=limit)

        repaired = 0
        skipped_low_tatar_specific = 0
        skipped_no_tokens = 0
        invalid = 0
        for row in rows:
            sample = Sample(id=str(row["id"]), text=str(row["text"]))
            if count_tatar_specific_letters(sample.text) < MIN_TATAR_SPECIFIC_LETTERS:
                skipped_low_tatar_specific += 1
                continue
            tokens = build_editable_tokens(
                sample.text,
                reviewed=reviewed,
                prior_labels=prior_labels,
                prior_homonyms=prior_homonyms,
            )
            if not tokens:
                skipped_no_tokens += 1
                continue
            item = {
                "id": sample.id,
                "tatar": True,
                "tokens": [token.to_json() for token in tokens],
            }
            validation = validate_response(
                json.dumps([item], ensure_ascii=False),
                [sample],
            )
            if not validation.ok:
                invalid += 1
                continue
            if not dry_run:
                save_manual_item(conn, sample, item, model=LOCAL_REPAIR_MODEL_NAME)
            repaired += 1

        remaining = _count_unprocessable(conn)
    return LocalRepairSummary(
        total=len(rows),
        repaired=repaired,
        skipped_low_tatar_specific=skipped_low_tatar_specific,
        skipped_no_tokens=skipped_no_tokens,
        invalid=invalid,
        remaining=remaining,
        dry_run=dry_run,
    )
