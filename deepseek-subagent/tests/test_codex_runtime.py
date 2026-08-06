from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "runtime"))

from deepseek_subagent.core.errors import ManagerError  # noqa: E402
from deepseek_subagent.platforms.codex.runtime import CodexRuntimeDetector  # noqa: E402


class CodexRuntimeDetectorTests(unittest.TestCase):
    def test_windows_prefers_codex_from_path(self) -> None:
        candidate = Path(r"C:\Program Files\Codex\codex.exe")
        with (
            mock.patch("deepseek_subagent.platforms.codex.runtime.sys.platform", "win32"),
            mock.patch("deepseek_subagent.platforms.codex.runtime.shutil.which", return_value=str(candidate)),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            self.assertEqual(CodexRuntimeDetector.candidates(), (candidate,))

    def test_explicit_runtime_override_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "codex.exe"
            candidate.touch()
            with mock.patch.dict(os.environ, {"CODEX_DESKTOP_BIN": str(candidate)}):
                self.assertEqual(CodexRuntimeDetector.find(), str(candidate.resolve()))

    def test_version_timeout_has_stable_error(self) -> None:
        with mock.patch(
            "deepseek_subagent.platforms.codex.runtime.subprocess.run",
            side_effect=subprocess.TimeoutExpired("codex", 5),
        ):
            with self.assertRaises(ManagerError) as raised:
                CodexRuntimeDetector.version("codex.exe")
        self.assertEqual(raised.exception.code, "codex_version_unknown")


if __name__ == "__main__":
    unittest.main()
