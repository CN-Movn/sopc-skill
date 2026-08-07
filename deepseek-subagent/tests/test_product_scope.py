from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = SKILL_ROOT / "runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

from deepseek_subagent import __version__, cli  # noqa: E402
from deepseek_subagent.core.agent_role import make_role  # noqa: E402
from deepseek_subagent.core.manifest import upgrade_payload  # noqa: E402
from deepseek_subagent.providers import OpenCodeGoProvider, get_provider  # noqa: E402
from deepseek_subagent.registry import get_platform, get_provider_definition  # noqa: E402


class ProductScopeTests(unittest.TestCase):
    def test_fixed_codex_adapter(self) -> None:
        self.assertEqual(get_platform().id, "codex")

    def test_fixed_opencode_go_provider(self) -> None:
        self.assertIs(get_provider(), OpenCodeGoProvider)
        self.assertIs(get_provider_definition(), OpenCodeGoProvider)
        self.assertEqual(OpenCodeGoProvider.config_id, "opencode-go-bridge")
        self.assertEqual(OpenCodeGoProvider.model, "deepseek-v4-flash")

    def test_release_uses_ultra_reasoning(self) -> None:
        self.assertEqual(__version__, "1.4.2")
        self.assertEqual((SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip(), "1.4.2")
        self.assertEqual(make_role(OpenCodeGoProvider).reasoning_effort, "ultra")

    def test_cli_has_no_host_or_provider_selection(self) -> None:
        for flag in ("--platform", "--provider", "--platform-home"):
            with self.subTest(flag=flag), self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                cli.parse_args(["status", flag, "unused"])

    def test_help_has_no_selection_flags(self) -> None:
        with self.assertRaises(SystemExit) as raised, redirect_stdout(StringIO()):
            cli.parse_args(["--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_legacy_manifest_view_converges_to_fixed_route(self) -> None:
        old = {
            "schema_version": 2,
            "platform": "legacy-host",
            "provider": "legacy-provider",
            "requested_provider": "legacy-provider",
            "provider_selection_match": False,
        }
        view = upgrade_payload(old)
        self.assertEqual(view["platform"], "codex")
        self.assertEqual(view["provider"], "opencode-go")

    def test_runtime_has_one_package_tree(self) -> None:
        self.assertTrue((RUNTIME_ROOT / "deepseek_subagent").is_dir())
        self.assertFalse((SKILL_ROOT / "deepseek_subagent").exists())

    def test_removed_adapter_and_credential_paths_absent(self) -> None:
        package = RUNTIME_ROOT / "deepseek_subagent"
        platform_entries = {
            item.name for item in (package / "platforms").iterdir()
            if item.name != "__pycache__"
        }
        self.assertEqual(platform_entries, {"__init__.py", "codex"})
        self.assertEqual(
            {item.name for item in (package / "providers").glob("*.py")},
            {"__init__.py", "opencode_go.py"},
        )
        self.assertFalse((package / "credentials").exists())

    def test_status_payload_does_not_emit_selection_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir()
            (state / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "platform": "codex",
                        "provider": "opencode-go",
                        "requested_provider": "legacy",
                        "provider_selection_match": False,
                    }
                ),
                encoding="utf-8",
            )
            payload = cli._status_payload(cli.state_paths(str(state)), str(Path(directory) / "codex"))
            self.assertNotIn("requested_provider", payload["checks"])
            self.assertNotIn("provider_selection_match", payload["checks"])

    def test_credential_guidance_uses_only_fixed_local_file(self) -> None:
        implementation = (SKILL_ROOT / "scripts" / "skill_manager.py").read_text(encoding="utf-8").lower()
        guidance = "\n".join(
            [
                (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8"),
                (SKILL_ROOT / "references" / "windows-development.md").read_text(encoding="utf-8"),
            ]
        ).lower()
        self.assertNotIn("clipboard", implementation)
        self.assertNotIn("computer-use", implementation)
        self.assertIn(".local\\opencode-go.key", guidance)
        self.assertNotIn("credentials set --key", guidance)
        self.assertNotIn("auth.json", guidance)
        self.assertNotIn("%localappdata%", guidance)

    def test_real_local_secrets_are_not_managed_examples(self) -> None:
        local = SKILL_ROOT / ".local"
        self.assertTrue((local / "opencode-go.key.example").is_file())
        self.assertTrue((local / "README.txt").is_file())
        self.assertFalse((local / "opencode-go.key").exists())
        self.assertFalse((local / "local-bridge-token.txt").exists())

    def test_real_local_files_are_git_ignored(self) -> None:
        gitignore = SKILL_ROOT.parent / ".gitignore"
        if not gitignore.is_file():
            self.skipTest("installed Skill does not include repository .gitignore")
        ignore = gitignore.read_text(encoding="utf-8")
        self.assertIn("deepseek-subagent/.local/opencode-go.key", ignore)
        self.assertIn("deepseek-subagent/.local/local-bridge-token.txt", ignore)
        self.assertIn("deepseek-subagent/.local/local-bridge-token-state.json", ignore)

    def test_scheduling_policy_is_neutral_and_user_directed(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("zero, one, or multiple", skill)
        self.assertIn("Follow an explicit user request", skill)
        self.assertIn("Do not split work merely to demonstrate delegation", skill)
        self.assertNotIn("Delegate large text-source reading", skill)
        self.assertNotIn("Always dispatch explicitly", skill)
        self.assertNotIn("For independent scopes, multiple DeepSeek workers", skill)

    def test_persistent_assistant_reuse_policy_is_explicit(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Treat a completed child as idle and reusable, not disposable", skill)
        self.assertIn("Reuse the known open child with `send_input`", skill)
        self.assertIn("call `resume_agent` and then reuse it", skill)
        self.assertIn("Spawn a replacement only when the prior Agent is `not_found`", skill)
        self.assertIn("Do not close a persistent assistant merely because its current assignment completed", skill)
        self.assertIn("severe hallucination, corrupted context, or repeated operational failure", skill)


if __name__ == "__main__":
    unittest.main()
