"""Tests for the PATH-independent Windows lifecycle launcher."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "deepseek-subagent.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell is required")
class WindowsLauncherTests(unittest.TestCase):
    def _resolve(self, home: Path, env: dict[str, str] | None = None) -> dict:
        command = (
            "$env:DEEPSEEK_SUBAGENT_LAUNCHER_IMPORT_ONLY='1'; "
            f". '{str(LAUNCHER).replace("'", "''")}'; "
            f"Resolve-DeepSeekPython -HomePath '{str(home).replace("'", "''")}' | ConvertTo-Json -Compress"
        )
        merged = os.environ.copy()
        merged.pop("CODEX_PYTHON", None)
        if env:
            merged.update(env)
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=merged,
            check=True,
        )
        return json.loads(result.stdout)

    @staticmethod
    def _touch_runtime(home: Path, name: str) -> Path:
        executable = home / ".cache" / "codex-runtimes" / name / "dependencies" / "python" / "python.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"test candidate")
        return executable

    def test_path_without_python_selects_primary_codex_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            expected = self._touch_runtime(home, "codex-primary-runtime")
            result = self._resolve(home, {"PATH": str(home / "empty-path")})
            self.assertTrue(result["Found"])
            self.assertTrue(os.path.samefile(result["Executable"], expected))
            self.assertEqual(result["Source"], "codex-primary-runtime")

    def test_codex_python_has_highest_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self._touch_runtime(home, "codex-primary-runtime")
            configured = home / "configured python.cmd"
            configured.write_text("@exit /b 0\n", encoding="ascii")
            result = self._resolve(home, {"CODEX_PYTHON": str(configured)})
            self.assertTrue(os.path.samefile(result["Executable"], configured))
            self.assertEqual(result["Source"], "CODEX_PYTHON")

    def test_other_runtime_is_used_when_primary_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            expected = self._touch_runtime(home, "codex-runtime-2026-08")
            result = self._resolve(home, {"PATH": str(home / "empty-path")})
            self.assertTrue(os.path.samefile(result["Executable"], expected))
            self.assertEqual(result["Source"], "codex-runtime:codex-runtime-2026-08")

    def test_arguments_and_exit_code_are_forwarded(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "skill with spaces"
            scripts = skill / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(LAUNCHER, scripts / LAUNCHER.name)
            output = Path(directory) / "arguments.json"
            manager = textwrap.dedent(
                """
                import json, os, sys
                with open(os.environ["LAUNCHER_TEST_OUTPUT"], "w", encoding="utf-8") as stream:
                    json.dump(sys.argv[1:], stream)
                raise SystemExit(int(sys.argv[-1]))
                """
            ).strip()
            (scripts / "skill_manager.py").write_text(manager, encoding="utf-8")
            env = os.environ.copy()
            env["CODEX_PYTHON"] = sys.executable
            env["LAUNCHER_TEST_OUTPUT"] = str(output)
            result = subprocess.run(
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(scripts / LAUNCHER.name),
                    "status",
                    "two words",
                    "23",
                ],
                env=env,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 23)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), ["status", "two words", "23"])

    def test_launcher_and_manager_paths_may_contain_spaces(self):
        with tempfile.TemporaryDirectory(prefix="deepseek launcher space ") as directory:
            scripts = Path(directory) / "nested skill" / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(LAUNCHER, scripts / LAUNCHER.name)
            (scripts / "skill_manager.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            env = os.environ.copy()
            env["CODEX_PYTHON"] = sys.executable
            result = subprocess.run(
                [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(scripts / LAUNCHER.name)],
                env=env,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_missing_interpreter_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_home = Path(directory) / "empty home"
            empty_path = Path(directory) / "empty path"
            fake_home.mkdir()
            empty_path.mkdir()
            env = os.environ.copy()
            env.pop("CODEX_PYTHON", None)
            env["USERPROFILE"] = str(fake_home)
            env["HOME"] = str(fake_home)
            env["PATH"] = str(empty_path)
            result = subprocess.run(
                [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LAUNCHER), "status"],
                text=True,
                encoding="utf-8",
                capture_output=True,
                env=env,
            )
            self.assertEqual(result.returncode, 127)
            self.assertIn("no usable Python interpreter", result.stderr)
            self.assertIn("Codex primary runtime", result.stderr)
            self.assertIn("Other Codex runtimes", result.stderr)
            self.assertIn("py -3", result.stderr)
            self.assertIn("PATH command: python", result.stderr)

    def test_unusable_py_launcher_is_not_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "empty home"
            command_path = Path(directory) / "commands"
            home.mkdir()
            command_path.mkdir()
            (command_path / "py.cmd").write_text("@exit /b 1\n", encoding="ascii")
            result = self._resolve(home, {"PATH": str(command_path)})
            self.assertFalse(result["Found"])
            self.assertIsNone(result["Executable"])


if __name__ == "__main__":
    unittest.main()
