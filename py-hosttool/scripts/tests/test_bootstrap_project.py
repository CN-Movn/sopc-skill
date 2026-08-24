"""Pure-Python regression tests for the project bootstrapper."""
from __future__ import annotations

import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bootstrap_project.py"


class BootstrapProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="py-hosttool-bootstrap-test-"))
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)

    def run_bootstrap(self, destination: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(destination), *extra],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_generated_python_parses(self, destination: Path) -> None:
        for path in destination.rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_generates_both_layouts_and_replaces_identity_and_baudrate(self) -> None:
        for layout in ("workbench", "dashboard"):
            destination = self.temp_dir / layout
            result = self.run_bootstrap(
                destination,
                "--app-name", 'Demo "Lab"',
                "--version", "1.2.3",
                "--baudrate", "921600",
                "--layout", layout,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((destination / ".py-hosttool-generated").is_file())
            config = (destination / "hosttool" / "config.py").read_text(encoding="utf-8")
            self.assertIn("APP_NAME = 'Demo \"Lab\"'", config)
            self.assertIn("APP_VERSION = '1.2.3'", config)
            self.assertIn("DEFAULT_BAUDRATE = 921600", config)
            self.assertIn(f'DEFAULT_LAYOUT = "{layout}"', config)
            self.assertIn(
                '"--smoke-test"',
                (destination / "main.py").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "--smoke-test",
                (destination / "build.bat").read_text(encoding="utf-8"),
            )
            self.assert_generated_python_parses(destination)

    def test_empty_existing_destination_is_accepted(self) -> None:
        destination = self.temp_dir / "empty"
        destination.mkdir()
        result = self.run_bootstrap(
            destination, "--app-name", "Empty Destination", "--baudrate", "115200"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((destination / ".py-hosttool-generated").is_file())

    def test_invalid_identity_is_rejected_without_creating_destination(self) -> None:
        for option, value in (("--app-name", 'Bad"""Name'), ("--version", "1.0\nBAD")):
            destination = self.temp_dir / option.removeprefix("--")
            result = self.run_bootstrap(
                destination, "--app-name", "Safe", option, value,
                "--baudrate", "115200",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(destination.exists())

    def test_force_rejects_unmarked_nonempty_directory_and_preserves_files(self) -> None:
        destination = self.temp_dir / "unmarked"
        destination.mkdir()
        sentinel = destination / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        result = self.run_bootstrap(
            destination, "--app-name", "Unsafe", "--baudrate", "115200", "--force"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_force_replaces_a_project_created_by_bootstrapper(self) -> None:
        destination = self.temp_dir / "generated"
        first = self.run_bootstrap(
            destination, "--app-name", "First", "--baudrate", "115200"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        user_file = destination / "user-added.txt"
        user_file.write_text("old project content", encoding="utf-8")

        second = self.run_bootstrap(
            destination,
            "--app-name", "Second",
            "--version", "2.0.0",
            "--baudrate", "115200",
            "--force",
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse(user_file.exists())
        config = (destination / "hosttool" / "config.py").read_text(encoding="utf-8")
        self.assertIn("APP_NAME = 'Second'", config)
        self.assertIn("APP_VERSION = '2.0.0'", config)
        self.assert_generated_python_parses(destination)

    def test_syntax_failure_cleans_staging_and_leaves_destination_absent(self) -> None:
        module_spec = importlib.util.spec_from_file_location("bootstrap_project_under_test", SCRIPT)
        self.assertIsNotNone(module_spec)
        assert module_spec is not None and module_spec.loader is not None
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)

        template = self.temp_dir / "invalid-template"
        template.mkdir()
        (template / "bad.py").write_text('APP_NAME = "{{APP_NAME}}"\n', encoding="utf-8")
        module.TEMPLATE = template
        destination = self.temp_dir / "syntax-failure"
        old_argv = sys.argv
        try:
            sys.argv = [
                str(SCRIPT), str(destination),
                "--app-name", 'Bad"',
                "--baudrate", "115200",
            ]
            self.assertEqual(module.main(), 1)
        finally:
            sys.argv = old_argv
        self.assertFalse(destination.exists())
        self.assertEqual(
            list(self.temp_dir.glob(".syntax-failure.bootstrap-*")),
            [],
        )

    def test_baudrate_must_be_explicit(self) -> None:
        destination = self.temp_dir / "missing-baudrate"
        result = self.run_bootstrap(destination, "--app-name", "No Guessing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--baudrate", result.stderr)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
