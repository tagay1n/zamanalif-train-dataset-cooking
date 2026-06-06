from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tatar_preannotator.annotate import AnnotateSummary
from tatar_preannotator.cli import main


class PreannotatorCliTests(unittest.TestCase):
    def test_annotate_help_shows_default_paths(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(output):
            main(["annotate", "--help"])

        help_text = output.getvalue()
        self.assertIn("--db", help_text)
        self.assertIn("data/selected.sqlite", help_text)
        self.assertIn("--config", help_text)
        self.assertIn("config.yaml", help_text)

    def test_fatal_annotation_error_is_printed_and_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "selected.sqlite"
            config_path = Path(tmpdir) / "config.yaml"
            db_path.touch()
            config_path.write_text(
                """
gemini:
  model: model-a
  api_keys:
    - key-a
preannotation:
  exhausted_keys_path: exhausted_keys.json
  initial_batch_size: 1
  request_timeout_seconds: 5
  overload_sleep_seconds: 7
  target_annotated_count: 1
""",
                encoding="utf-8",
            )
            output = StringIO()
            with patch(
                "tatar_preannotator.cli.run_annotation",
                return_value=AnnotateSummary(
                    annotated_count=0,
                    pending_count=30_000,
                    stopped_reason="fatal_error",
                    error="fatal Gemini error: google-genai is not installed",
                ),
            ):
                with redirect_stdout(output):
                    exit_code = main(
                        ["annotate", "--db", str(db_path), "--config", str(config_path)]
                    )

        self.assertEqual(exit_code, 1)
        self.assertIn("fatal_error", output.getvalue())
        self.assertIn("google-genai is not installed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
