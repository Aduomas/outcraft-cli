"""CLI dry runs and confirmation failures never authenticate or make HTTP requests."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from outcraft_cli.cli import main


class CliCampaignsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {"HOME": temporary.name}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        forbidden = patch(
            "socket.socket.connect",
            side_effect=AssertionError("HTTP forbidden in CLI dry run"),
        )
        self.network = forbidden.start()
        self.addCleanup(forbidden.stop)

    def invoke(self, argv: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
        self.network.assert_not_called()
        self.assertIsInstance(code, int)
        return code, stdout.getvalue(), stderr.getvalue()

    def dry_run(self, argv: list[str]) -> dict:
        code, stdout, stderr = self.invoke(argv)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertIsInstance(payload, dict)
        self.assertIs(payload["dry_run"], True)
        return payload

    def test_rename_without_yes_returns_nonzero_safe_json_error(self) -> None:
        code, stdout, stderr = self.invoke(["campaigns", "rename", "7", "Name"])
        self.assertNotEqual(code, 0)
        self.assertEqual(stdout, "")
        error = json.loads(stderr)
        self.assertIsInstance(error, dict)
        self.assertIn("error", error)
        self.assertNotIn("Traceback", stderr)

    def test_rename_dry_run_needs_neither_confirmation_nor_authentication(self) -> None:
        self.dry_run(["campaigns", "rename", "7", "Name", "--dry-run"])

    def test_delete_dry_run_explicitly_means_archive(self) -> None:
        payload = self.dry_run(["campaigns", "delete", "7", "--dry-run"])
        self.assertIn(
            "archive",
            json.dumps(payload).lower(),
            "delete must describe archiving, not permanent deletion",
        )

    def test_publish_dry_run_needs_no_authentication(self) -> None:
        self.dry_run(["campaigns", "publish", "7", "--dry-run"])

    def test_invalid_confirmed_rename_is_a_json_error_not_an_exception(self) -> None:
        code, stdout, stderr = self.invoke(["campaigns", "rename", "7", "ab", "--yes"])
        self.assertNotEqual(code, 0)
        self.assertEqual(stdout, "")
        error = json.loads(stderr)
        self.assertIsInstance(error, dict)
        self.assertIn("error", error)
        self.assertNotIn("Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
