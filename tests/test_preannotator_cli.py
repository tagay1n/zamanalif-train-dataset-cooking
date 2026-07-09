from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tatar_preannotator.annotate import AnnotateSummary
from tatar_preannotator.cli import ShutdownController, main


class PreannotatorCliTests(unittest.TestCase):
    def test_annotate_help_shows_default_paths(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(output):
            main(["annotate", "--help"])

        help_text = output.getvalue()
        self.assertIn("--db", help_text)
        self.assertIn("data/zamanalif.sqlite", help_text)
        self.assertIn("--config", help_text)
        self.assertIn("config.yaml", help_text)
        self.assertIn("--model", help_text)
        self.assertIn("--retry-unprocessable", help_text)

    def test_fatal_annotation_error_is_printed_and_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "zamanalif.sqlite"
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
  requests_per_minute: 5
  graceful_shutdown_timeout_seconds: 300
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

    def test_forced_shutdown_summary_exits_130(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "zamanalif.sqlite"
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
  requests_per_minute: 5
  graceful_shutdown_timeout_seconds: 300
  initial_batch_size: 1
  request_timeout_seconds: 5
  overload_sleep_seconds: 7
  target_annotated_count: 1
""",
                encoding="utf-8",
            )
            with patch(
                "tatar_preannotator.cli.run_annotation",
                return_value=AnnotateSummary(
                    annotated_count=0,
                    pending_count=1,
                    stopped_reason="forced_shutdown",
                    error="forced shutdown requested",
                ),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    exit_code = main(
                        ["annotate", "--db", str(db_path), "--config", str(config_path)]
                    )

        self.assertEqual(exit_code, 130)

    def test_model_cli_option_overrides_config_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "zamanalif.sqlite"
            config_path = Path(tmpdir) / "config.yaml"
            db_path.touch()
            config_path.write_text(
                """
gemini:
  model: config-model
  api_keys:
    - key-a
preannotation:
  exhausted_keys_path: exhausted_keys.json
  requests_per_minute: 5
  graceful_shutdown_timeout_seconds: 300
  initial_batch_size: 1
  request_timeout_seconds: 5
  overload_sleep_seconds: 7
  target_annotated_count: 1
""",
                encoding="utf-8",
            )
            with patch(
                "tatar_preannotator.cli.run_annotation",
                return_value=AnnotateSummary(
                    annotated_count=1,
                    pending_count=0,
                    stopped_reason="target_reached",
                ),
            ) as run_annotation:
                output = StringIO()
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "annotate",
                            "--db",
                            str(db_path),
                            "--config",
                            str(config_path),
                            "--model",
                            "cli-model",
                            "--retry-unprocessable",
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_annotation.call_args.kwargs["config"].model, "cli-model")
        self.assertTrue(run_annotation.call_args.kwargs["retry_unprocessable"])

    def test_blank_model_cli_option_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "zamanalif.sqlite"
            config_path = Path(tmpdir) / "config.yaml"
            db_path.touch()
            config_path.write_text(
                """
gemini:
  model: config-model
  api_keys:
    - key-a
preannotation:
  exhausted_keys_path: exhausted_keys.json
  requests_per_minute: 5
  graceful_shutdown_timeout_seconds: 300
  initial_batch_size: 1
  request_timeout_seconds: 5
  overload_sleep_seconds: 7
  target_annotated_count: 1
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "--model must be a non-empty string"):
                main(
                    [
                        "annotate",
                        "--db",
                        str(db_path),
                        "--config",
                        str(config_path),
                        "--model",
                        " ",
                    ]
                )

    def test_shutdown_controller_first_and_second_sigint(self) -> None:
        messages: list[str] = []
        controller = ShutdownController(300, log=messages.append)

        controller._handle_sigint(None, None)
        controller._handle_sigint(None, None)

        self.assertTrue(controller.requested())
        self.assertTrue(controller.forced())
        self.assertIn("shutdown requested", messages[0])
        self.assertIn("forced shutdown requested", messages[1])


if __name__ == "__main__":
    unittest.main()
