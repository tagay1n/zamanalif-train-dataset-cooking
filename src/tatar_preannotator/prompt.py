from __future__ import annotations

import json
from typing import Iterable

from .schema import Sample


def build_prompt(samples: Iterable[Sample]) -> str:
    """Build the Gemini prompt for one batch of samples."""
    items = [{"id": sample.id, "text": sample.text} for sample in samples]
    return (
        "You are pre-annotating Tatar Cyrillic sentences.\n"
        "Return only valid JSON. Do not include markdown, explanations, comments, or code fences.\n"
        "Return a JSON array with one object for each input sentence.\n"
        "For each input sentence:\n"
        "1. Set \"tatar\": true only if at least half of meaningful text is Tatar written in Cyrillic.\n"
        "2. If uncertain whether it is mostly Tatar, set \"tatar\": false.\n"
        "3. If \"tatar\": false, return \"tokens\": [].\n"
        "4. If \"tatar\": true, output word tokens only, in the same order.\n"
        "5. Preserve original token text exactly. Do not output punctuation tokens.\n"
        "6. Keep hyphenated written words as one token if they appear as one written word.\n"
        "7. Label each token with exactly one of: \"RL\", \"N\", \"U\".\n"
        "\"RL\" = Russian loanword, Russian word, or Russian/international word likely entering Tatar through Russian.\n"
        "\"N\" = native/non-Russian word: Turkic, Arabic, Persian, or other non-Russian integrated Tatar vocabulary.\n"
        "\"U\" = unknown or uncertain. Do not guess aggressively.\n"
        "8. For mixed-origin compounds, use \"U\" unless clearly classifiable.\n"
        "9. Add \"homonym\": true only when a token labeled \"RL\" also has an independent Tatar/native homonym with a different meaning.\n"
        "10. Otherwise omit the homonym field.\n"
        "Do not output lemmas, origin details, confidence, notes, meanings, skip reasons, ratio estimates, or extra fields.\n"
        "Expected item shape:\n"
        "{\"id\":\"sent_000001\",\"tatar\":true,\"tokens\":[{\"text\":\"Казан\",\"label\":\"N\"}]}\n"
        "Input sentences:\n"
        f"{json.dumps(items, ensure_ascii=False)}"
    )

