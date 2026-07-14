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

    def test_manual_preannotate_help_shows_default_db(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(output):
            main(["manual-preannotate", "--help"])

        help_text = output.getvalue()
        self.assertIn("--db", help_text)
        self.assertIn("data/zamanalif.sqlite", help_text)
        self.assertIn("--host", help_text)
        self.assertIn("127.0.0.1", help_text)
        self.assertIn("--port", help_text)
        self.assertIn("8765", help_text)
        self.assertIn("--limit", help_text)

    def test_manual_preannotate_cli_passes_server_args(self) -> None:
        with patch("tatar_preannotator.cli.serve_manual_preannotation_web") as serve:
            exit_code = main(
                [
                    "manual-preannotate",
                    "--db",
                    "custom.sqlite",
                    "--host",
                    "127.0.0.2",
                    "--port",
                    "8766",
                    "--limit",
                    "5",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(serve.call_args.args[0], "custom.sqlite")
        self.assertEqual(serve.call_args.kwargs["host"], "127.0.0.2")
        self.assertEqual(serve.call_args.kwargs["port"], 8766)
        self.assertEqual(serve.call_args.kwargs["limit"], 5)

    def test_manual_preannotate_ctrl_c_exits_cleanly(self) -> None:
        with patch(
            "tatar_preannotator.cli.serve_manual_preannotation_web",
            side_effect=KeyboardInterrupt,
        ):
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["manual-preannotate"])

        self.assertEqual(exit_code, 130)
        self.assertIn("stopped", output.getvalue())

    def test_manual_preannotate_invalid_port_fails_fast(self) -> None:
        with self.assertRaisesRegex(SystemExit, "--port"):
            main(["manual-preannotate", "--port", "70000"])

    def test_repair_unprocessable_help_shows_default_db(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(output):
            main(["repair-unprocessable", "--help"])

        help_text = output.getvalue()
        self.assertIn("--db", help_text)
        self.assertIn("data/zamanalif.sqlite", help_text)
        self.assertIn("--limit", help_text)
        self.assertIn("--dry-run", help_text)

    def test_repair_unprocessable_cli_prints_summary(self) -> None:
        with patch("tatar_preannotator.cli.repair_unprocessable") as repair:
            repair.return_value.total = 10
            repair.return_value.repaired = 8
            repair.return_value.skipped_low_tatar_specific = 1
            repair.return_value.skipped_no_tokens = 0
            repair.return_value.invalid = 1
            repair.return_value.remaining = 2
            repair.return_value.dry_run = True
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "repair-unprocessable",
                        "--db",
                        "custom.sqlite",
                        "--limit",
                        "5",
                        "--dry-run",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(repair.call_args.args[0], "custom.sqlite")
        self.assertEqual(repair.call_args.kwargs["limit"], 5)
        self.assertTrue(repair.call_args.kwargs["dry_run"])
        self.assertIn("repaired=8", output.getvalue())

    def test_repair_unprocessable_invalid_limit_fails_fast(self) -> None:
        with self.assertRaisesRegex(SystemExit, "--limit"):
            main(["repair-unprocessable", "--limit", "0"])

    def test_resolve_conflicts_help_shows_default_db_and_port(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(output):
            main(["resolve-conflicts", "--help"])

        help_text = output.getvalue()
        self.assertIn("--db", help_text)
        self.assertIn("data/zamanalif.sqlite", help_text)
        self.assertIn("--port", help_text)
        self.assertIn("8766", help_text)
        self.assertIn("--limit", help_text)

    def test_resolve_conflicts_cli_passes_server_args(self) -> None:
        with patch("tatar_preannotator.cli.serve_conflict_resolver_web") as serve:
            exit_code = main(
                [
                    "resolve-conflicts",
                    "--db",
                    "custom.sqlite",
                    "--host",
                    "127.0.0.2",
                    "--port",
                    "8767",
                    "--limit",
                    "5",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(serve.call_args.args[0], "custom.sqlite")
        self.assertEqual(serve.call_args.kwargs["host"], "127.0.0.2")
        self.assertEqual(serve.call_args.kwargs["port"], 8767)
        self.assertEqual(serve.call_args.kwargs["limit"], 5)

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
