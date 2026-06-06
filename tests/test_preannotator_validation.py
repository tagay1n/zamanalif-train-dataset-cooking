from __future__ import annotations

import json
import unittest

from tatar_preannotator.schema import Sample
from tatar_preannotator.validate import validate_response


class ValidationTests(unittest.TestCase):
    def test_valid_response_passes_and_ignores_extra_fields_with_warning(self) -> None:
        raw = json.dumps(
            [
                {
                    "id": "sent_000001",
                    "tatar": True,
                    "tokens": [
                        {"text": "Казан", "label": "N"},
                        {"text": "университетында", "label": "RL", "homonym": True},
                    ],
                    "confidence": 0.8,
                }
            ],
            ensure_ascii=False,
        )

        result = validate_response(
            raw,
            [Sample(id="sent_000001", text="Казан университетында яңа проект башланды.")],
        )

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.items[0]["tokens"][1]["homonym"], True)
        self.assertTrue(result.warnings)

    def test_invalid_label_fails(self) -> None:
        result = validate_response(
            '[{"id":"sent_000001","tatar":true,"tokens":[{"text":"Казан","label":"X"}]}]',
            [Sample(id="sent_000001", text="Казан.")],
        )

        self.assertFalse(result.ok)
        self.assertIn("invalid label", "; ".join(result.errors))

    def test_homonym_on_non_rl_fails(self) -> None:
        result = validate_response(
            '[{"id":"sent_000001","tatar":true,"tokens":[{"text":"Казан","label":"N","homonym":true}]}]',
            [Sample(id="sent_000001", text="Казан.")],
        )

        self.assertFalse(result.ok)
        self.assertIn("homonym is only valid on RL", "; ".join(result.errors))

    def test_tatar_false_requires_empty_tokens(self) -> None:
        result = validate_response(
            '[{"id":"sent_000001","tatar":false,"tokens":[{"text":"Казан","label":"N"}]}]',
            [Sample(id="sent_000001", text="Казан.")],
        )

        self.assertFalse(result.ok)
        self.assertIn("tatar=false requires empty tokens", "; ".join(result.errors))

    def test_token_order_validation_fails(self) -> None:
        result = validate_response(
            '[{"id":"sent_000001","tatar":true,"tokens":[{"text":"проект","label":"RL"},{"text":"Казан","label":"N"}]}]',
            [Sample(id="sent_000001", text="Казан проект башланды.")],
        )

        self.assertFalse(result.ok)
        self.assertIn("out of order", "; ".join(result.errors))

    def test_missing_duplicate_and_unknown_ids_are_reported(self) -> None:
        result = validate_response(
            '[{"id":"sent_000001","tatar":false,"tokens":[]},{"id":"sent_000001","tatar":false,"tokens":[]},{"id":"sent_x","tatar":false,"tokens":[]}]',
            [
                Sample(id="sent_000001", text="Казан."),
                Sample(id="sent_000002", text="Әни."),
            ],
        )

        errors = "; ".join(result.errors)
        self.assertFalse(result.ok)
        self.assertIn("duplicate id: sent_000001", errors)
        self.assertIn("unknown id: sent_x", errors)
        self.assertIn("missing id: sent_000002", errors)


if __name__ == "__main__":
    unittest.main()
