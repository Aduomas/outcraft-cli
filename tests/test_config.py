"""Credential resolution: nonempty OS variables first, then a private env file, literally."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from outcraft_cli.config import credentials
from outcraft_cli.errors import OutcraftError

EMAIL = "synthetic@example.invalid"
PASSWORD = "synthetic-password-not-a-secret"
OS_EMAIL = "os-synthetic@example.invalid"
OS_PASSWORD = "os-synthetic-password-not-a-secret"


class CredentialsTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def env_file(self, text: str, mode: int = 0o600) -> Path:
        path = self.directory / "synthetic.env"
        path.write_text(text)
        path.chmod(mode)
        return path

    def standard_file(self) -> Path:
        return self.env_file(f"OUTCRAFT_EMAIL={EMAIL}\nOUTCRAFT_PASSWORD={PASSWORD}\n")

    def test_no_file_and_no_variables_is_blank(self) -> None:
        self.assertEqual(credentials(None), ("", ""))

    def test_env_file_supplies_both_values(self) -> None:
        self.assertEqual(credentials(self.standard_file()), (EMAIL, PASSWORD))

    def test_nonempty_os_variables_override_the_file(self) -> None:
        os.environ.update({"OUTCRAFT_EMAIL": OS_EMAIL, "OUTCRAFT_PASSWORD": OS_PASSWORD})
        self.assertEqual(credentials(self.standard_file()), (OS_EMAIL, OS_PASSWORD))

    def test_blank_os_variables_fall_back_to_the_file(self) -> None:
        os.environ.update({"OUTCRAFT_EMAIL": "", "OUTCRAFT_PASSWORD": ""})
        self.assertEqual(credentials(self.standard_file()), (EMAIL, PASSWORD))

    def test_each_variable_falls_back_independently(self) -> None:
        cases = (
            ({"OUTCRAFT_EMAIL": OS_EMAIL, "OUTCRAFT_PASSWORD": ""}, (OS_EMAIL, PASSWORD)),
            ({"OUTCRAFT_EMAIL": "", "OUTCRAFT_PASSWORD": OS_PASSWORD}, (EMAIL, OS_PASSWORD)),
            ({"OUTCRAFT_PASSWORD": OS_PASSWORD}, (EMAIL, OS_PASSWORD)),
        )
        for variables, expected in cases:
            with (
                self.subTest(variables=variables),
                patch.dict(os.environ, variables, clear=True),
            ):
                self.assertEqual(credentials(self.standard_file()), expected)

    def test_os_variables_work_without_a_file(self) -> None:
        os.environ.update({"OUTCRAFT_EMAIL": OS_EMAIL, "OUTCRAFT_PASSWORD": OS_PASSWORD})
        self.assertEqual(credentials(None), (OS_EMAIL, OS_PASSWORD))

    def test_blank_file_values_stay_blank(self) -> None:
        path = self.env_file("OUTCRAFT_EMAIL=\nOUTCRAFT_PASSWORD=\n")
        self.assertEqual(credentials(path), ("", ""))

    def test_private_file_modes_are_accepted(self) -> None:
        for mode in (0o600, 0o400):
            with self.subTest(mode=oct(mode)):
                path = self.standard_file()
                path.chmod(mode)
                self.assertEqual(credentials(path), (EMAIL, PASSWORD))

    def test_group_or_world_access_is_refused_without_leaking(self) -> None:
        for mode in (0o644, 0o640, 0o604, 0o660, 0o666):
            with self.subTest(mode=oct(mode)):
                path = self.standard_file()
                path.chmod(mode)
                with self.assertRaises(OutcraftError) as caught:
                    credentials(path)
                self.assertNotIn(PASSWORD, str(caught.exception))
                self.assertNotIn(EMAIL, str(caught.exception))

    def test_insecure_file_is_refused_even_when_os_variables_are_set(self) -> None:
        os.environ.update({"OUTCRAFT_EMAIL": OS_EMAIL, "OUTCRAFT_PASSWORD": OS_PASSWORD})
        with self.assertRaises(OutcraftError):
            credentials(self.env_file(f"OUTCRAFT_PASSWORD={PASSWORD}\n", mode=0o644))

    def test_dollar_hash_and_backslash_are_literal(self) -> None:
        literal = r"p$HOME#x\n\\y$"
        cases = {
            "bare": f"OUTCRAFT_PASSWORD={literal}\n",
            "single quoted": f"OUTCRAFT_PASSWORD='{literal}'\n",
            "double quoted": f'OUTCRAFT_PASSWORD="{literal}"\n',
            "exported": f"export OUTCRAFT_PASSWORD={literal}\n",
        }
        os.environ["HOME"] = "/synthetic-home-must-not-expand"
        for label, text in cases.items():
            with self.subTest(label=label):
                _, password = credentials(self.env_file(f"OUTCRAFT_EMAIL={EMAIL}\n{text}"))
                self.assertEqual(password, literal)

    def test_command_substitution_is_never_executed(self) -> None:
        marker = self.directory / "executed"
        for template in ("$(touch {0})", "`touch {0}`", "${{PWD}}; touch {0}"):
            value = template.format(marker)
            with self.subTest(template=template):
                _, password = credentials(self.env_file(f"OUTCRAFT_PASSWORD={value}\n"))
                self.assertEqual(password, value)
                self.assertFalse(marker.exists())

    def test_hash_inside_value_is_not_a_comment(self) -> None:
        path = self.env_file("# leading comment\n\nOUTCRAFT_PASSWORD=before#after\n")
        self.assertEqual(credentials(path)[1], "before#after")

    def test_equals_inside_value_is_kept(self) -> None:
        path = self.env_file("OUTCRAFT_PASSWORD=a=b==\n")
        self.assertEqual(credentials(path)[1], "a=b==")

    def test_shell_lines_are_rejected_without_leaking(self) -> None:
        path = self.env_file(f"OUTCRAFT_EMAIL={EMAIL}\nsource {PASSWORD}\n")
        with self.assertRaises(OutcraftError) as caught:
            credentials(path)
        self.assertNotIn(PASSWORD, str(caught.exception))
        self.assertNotIn(EMAIL, str(caught.exception))

    def test_unrelated_keys_are_ignored_and_not_exported(self) -> None:
        path = self.env_file(f"OTHER_SECRET=ignored\nOUTCRAFT_PASSWORD={PASSWORD}\n")
        self.assertEqual(credentials(path), ("", PASSWORD))
        self.assertNotIn("OTHER_SECRET", os.environ)
        self.assertNotIn("OUTCRAFT_PASSWORD", os.environ)


if __name__ == "__main__":
    unittest.main()
