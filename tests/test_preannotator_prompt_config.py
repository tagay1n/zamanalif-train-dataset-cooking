from __future__ import annotations

import tempfile
import unittest

from tatar_preannotator.config import load_config
from tatar_preannotator.prompt import build_prompt
from tatar_preannotator.schema import Sample


class PromptConfigTests(unittest.TestCase):
    def test_prompt_contains_required_label_definitions_and_json_only_instruction(self) -> None:
        prompt = build_prompt([Sample(id="sent_000001", text="Казан университетында проект.")])

        self.assertIn("Return only valid JSON", prompt)
        self.assertIn('"RL" = Russian loanword', prompt)
        self.assertIn('"N" = native/non-Russian word', prompt)
        self.assertIn('"U" = unknown or uncertain', prompt)
        self.assertIn("Do not include markdown", prompt)
        self.assertIn("Do not output punctuation tokens", prompt)
        self.assertIn("homonym", prompt)

    def test_config_requires_all_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/config.yaml"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    """
gemini:
  model: gemini-2.5-flash
  api_keys:
    - key-a
preannotation:
  initial_batch_size: 30
  request_timeout_seconds: 120
  overload_sleep_seconds: 60
  target_annotated_count: 1000
"""
                )

            config = load_config(path)

            self.assertEqual(config.model, "gemini-2.5-flash")
            self.assertEqual(config.api_keys, ("key-a",))
            self.assertEqual(config.initial_batch_size, 30)

    def test_config_fails_fast_on_missing_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/config.yaml"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    """
gemini:
  api_keys:
    - key-a
preannotation:
  initial_batch_size: 30
  request_timeout_seconds: 120
  overload_sleep_seconds: 60
  target_annotated_count: 1000
"""
                )

            with self.assertRaisesRegex(ValueError, "gemini.model"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
