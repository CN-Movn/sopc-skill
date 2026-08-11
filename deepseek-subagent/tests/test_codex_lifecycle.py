from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "runtime"))

from deepseek_subagent.cli import build_codex_catalog  # noqa: E402
from deepseek_subagent.core.agent_role import make_role  # noqa: E402
from deepseek_subagent.core.errors import ManagerError  # noqa: E402
from deepseek_subagent.core.paths import state_paths  # noqa: E402
from deepseek_subagent.core.transaction import make_backup, restore_backup  # noqa: E402
from deepseek_subagent.platforms.codex import adapter as adapter_mod  # noqa: E402
from deepseek_subagent.platforms.codex.adapter import CodexAdapter  # noqa: E402
from deepseek_subagent.platforms.codex.paths import CodexPaths  # noqa: E402
from deepseek_subagent.providers import OpenCodeGoProvider  # noqa: E402


class CodexLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = state_paths(self.root / "state")
        self.codex_home = (self.root / "codex-home").resolve()
        self.paths = CodexPaths.from_home(self.codex_home, "DeepSeek")
        self.paths.config.parent.mkdir(parents=True, exist_ok=True)
        self.original = (
            'model = "gpt-5.6-sol"\n'
            '# unrelated user comment\n'
            '[features]\n'
            'js_repl = false\n'
            'multi_agent = false\n'
            'multi_agent_v2 = true\n'
        )
        self.paths.config.write_text(self.original, encoding="utf-8", newline="\n")
        self.role = make_role(OpenCodeGoProvider)
        self.auth = {
            "command": "python",
            "args": [str(self.root / "keygen.py")],
            "timeout_ms": 5000,
            "refresh_interval_ms": 0,
        }
        self.adapter = CodexAdapter()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def catalog(self) -> dict:
        payload = build_codex_catalog(
            {
                "models": [
                    {
                        "slug": "gpt-5.6-sol",
                        "display_name": "Parent",
                        "multi_agent_version": "v2",
                    }
                ]
            },
            OpenCodeGoProvider.model,
        )
        for item in payload["models"]:
            item["multi_agent_version"] = "v1"
        return payload

    def install(self) -> dict:
        return self.adapter.install_bridge(
            self.state,
            self.paths,
            self.role,
            OpenCodeGoProvider,
            self.catalog(),
            self.auth,
            "http://127.0.0.1:1981/v1",
        )

    def test_disable_is_idempotent_and_preserves_recovery_state(self) -> None:
        self.install()
        first = self.adapter.disable(self.state, object(), str(self.codex_home))
        second = self.adapter.disable(self.state, object(), str(self.codex_home))
        self.assertEqual(first["status"], "disabled")
        self.assertEqual(second["status"], "disabled")
        self.assertTrue(self.state.manifest.is_file())
        self.assertFalse(self.paths.agent.exists())

    def test_disabled_install_can_be_repaired_to_configured(self) -> None:
        self.install()
        self.adapter.disable(self.state, object(), str(self.codex_home))
        self.install()
        status = self.adapter.status(
            self.state, object(), self.role, OpenCodeGoProvider, str(self.codex_home)
        )
        self.assertEqual(status["status"], "configured")
        self.assertTrue(self.paths.agent.is_file())

    def test_agent_drift_blocks_disable_without_deleting_manifest(self) -> None:
        self.install()
        self.paths.agent.write_text("user replacement\n", encoding="utf-8")
        with self.assertRaises(ManagerError) as raised:
            self.adapter.disable(self.state, object(), str(self.codex_home))
        self.assertEqual(raised.exception.code, "conflict")
        self.assertTrue(self.state.manifest.is_file())

    def test_agent_drift_blocks_uninstall_without_deleting_manifest(self) -> None:
        self.install()
        self.paths.agent.write_text("user replacement\n", encoding="utf-8")
        with self.assertRaises(ManagerError) as raised:
            self.adapter.uninstall(self.state, object(), platform_home=str(self.codex_home))
        self.assertEqual(raised.exception.code, "conflict")
        self.assertTrue(self.state.manifest.is_file())

    def test_unrelated_config_edit_survives_disable_and_uninstall(self) -> None:
        self.install()
        text = self.paths.config.read_text(encoding="utf-8") + "\n# edit after install\n"
        self.paths.config.write_text(text, encoding="utf-8", newline="\n")
        disabled = self.adapter.disable(self.state, object(), str(self.codex_home))
        self.assertEqual(disabled["status"], "disabled")
        self.assertIn("# edit after install", self.paths.config.read_text(encoding="utf-8"))
        uninstalled = self.adapter.uninstall(
            self.state, object(), platform_home=str(self.codex_home)
        )
        self.assertEqual(uninstalled["status"], "uninstalled")
        self.assertIn("# edit after install", self.paths.config.read_text(encoding="utf-8"))

    def test_managed_field_drift_is_not_overwritten(self) -> None:
        self.install()
        text = self.paths.config.read_text(encoding="utf-8").replace(
            "multi_agent_v2 = false", "multi_agent_v2 = true"
        )
        self.paths.config.write_text(text, encoding="utf-8", newline="\n")
        result = self.adapter.disable(self.state, object(), str(self.codex_home))
        self.assertIn(result["status"], {"disabled", "disabled_with_conflicts"})
        self.assertIn("multi_agent_v2 = true", self.paths.config.read_text(encoding="utf-8"))

    def test_manifest_write_failure_rolls_back_install_files(self) -> None:
        before = self.paths.config.read_bytes()
        with mock.patch.object(adapter_mod, "write_manifest", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(self.paths.config.read_bytes(), before)
        self.assertFalse(self.paths.catalog.exists())
        self.assertFalse(self.paths.agent.exists())
        self.assertFalse(self.state.manifest.exists())

    def test_v1_feature_drift_is_not_overwritten(self) -> None:
        self.install()
        text = self.paths.config.read_text(encoding="utf-8").replace(
            "multi_agent = true", 'multi_agent = "custom"'
        )
        self.paths.config.write_text(text, encoding="utf-8", newline="\n")
        result = self.adapter.disable(self.state, object(), str(self.codex_home))
        self.assertIn("features.multi_agent", result["field_conflicts"])
        self.assertIn('multi_agent = "custom"', self.paths.config.read_text(encoding="utf-8"))

    def test_platform_mismatch_is_rejected_without_file_changes(self) -> None:
        self.install()
        manifest = json.loads(self.state.manifest.read_text(encoding="utf-8"))
        manifest["platform"] = "legacy-host"
        self.state.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        before = self.paths.config.read_bytes()
        with self.assertRaises(ManagerError) as raised:
            self.adapter.disable(self.state, object(), str(self.codex_home))
        self.assertEqual(raised.exception.code, "platform_mismatch")
        self.assertEqual(self.paths.config.read_bytes(), before)

    def test_backup_restores_same_named_targets(self) -> None:
        left = self.root / "left" / "same.txt"
        right = self.root / "right" / "same.txt"
        left.parent.mkdir()
        right.parent.mkdir()
        left.write_text("left", encoding="utf-8")
        right.write_text("right", encoding="utf-8")
        backup = make_backup(self.state, (left, right))
        left.write_text("changed-left", encoding="utf-8")
        right.write_text("changed-right", encoding="utf-8")
        restore_backup(backup, (left, right))
        self.assertEqual(left.read_text(encoding="utf-8"), "left")
        self.assertEqual(right.read_text(encoding="utf-8"), "right")

    def test_uninstall_restores_original_bytes(self) -> None:
        self.install()
        result = self.adapter.uninstall(
            self.state, object(), platform_home=str(self.codex_home)
        )
        self.assertEqual(result["status"], "uninstalled")
        self.assertEqual(self.paths.config.read_text(encoding="utf-8"), self.original)


if __name__ == "__main__":
    unittest.main()
