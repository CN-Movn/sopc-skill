from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock
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
from deepseek_subagent.platforms.codex.paths import CodexPaths  # noqa: E402
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
        self.assertEqual(__version__, "1.7.3")
        self.assertEqual((SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip(), "1.7.3")
        self.assertEqual(make_role(OpenCodeGoProvider).reasoning_effort, "ultra")

    def test_setup_can_mark_actual_parent_model_v1_for_next_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge_dir = root / "bridge"
            bridge_dir.mkdir()
            token_script = root / "token.py"
            token_script.write_text("print('token')\n", encoding="utf-8")
            (bridge_dir / "bridge.json").write_text(
                json.dumps({"token_script": str(token_script), "base_url": "http://127.0.0.1:1981/v1"}),
                encoding="utf-8",
            )
            codex_home = root / "codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text('model = "gpt-config"\n', encoding="utf-8")
            paths = CodexPaths.from_home(codex_home, "DeepSeek")
            adapter = mock.Mock()
            adapter.resolve_paths.return_value = paths
            adapter.install_bridge.return_value = {"backup": "x"}
            adapter.status.return_value = {"status": "configured", "checks": {}}
            cache = {
                "models": [
                    {"slug": "gpt-5.6-sol", "multi_agent_version": "v2"},
                    {"slug": "gpt-config", "multi_agent_version": "v2"},
                    {"slug": "gpt-ui", "multi_agent_version": "v2"},
                ]
            }
            with (
                mock.patch.object(cli, "CodexAdapter", return_value=adapter),
                mock.patch.object(cli, "read_codex_models_cache", return_value=cache),
            ):
                cli._codex_setup(
                    cli.state_paths(str(root / "state")),
                    str(codex_home),
                    str(bridge_dir / "bridge.json"),
                    "repair",
                    ["gpt-ui"],
                )
            catalog = adapter.install_bridge.call_args.args[4]
            versions = {item["slug"]: item.get("multi_agent_version") for item in catalog["models"]}
            self.assertEqual(versions["gpt-config"], "v1")
            self.assertEqual(versions["gpt-ui"], "v1")
            self.assertEqual(versions["deepseek-v4-flash"], "v1")

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

    def test_windows_default_state_root_is_local_domain(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows-only default state root")
        from deepseek_subagent.core.paths import default_state_root

        self.assertEqual(default_state_root(), SKILL_ROOT / ".local" / "state")

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
        self.assertIn("%localappdata%\\deepseek-subagent\\agents.json", guidance)
        self.assertIn(".local\\agents.json", guidance)

    def test_real_local_secrets_are_not_managed_examples(self) -> None:
        local = SKILL_ROOT / ".local"
        self.assertTrue((local / "opencode-go.key.example").is_file())
        self.assertTrue((local / "README.txt").is_file())
        # A source/package tree must never contain real local secrets or user
        # handoff logs. An installed Skill is expected to retain user-owned
        # files alongside the managed examples, so do not inspect or reject
        # those files there.
        self.assertFalse((local / "handoffs").exists())
        if (SKILL_ROOT.parent / ".gitignore").is_file():
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

    def test_current_process_child_policy_is_explicit(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Current-process child lifecycle", skill)
        self.assertIn("Treat a completed child as idle and reusable", skill)
        self.assertIn("Reuse a known active or completed child with `send_input`", skill)
        self.assertIn("A complete Codex exit, Windows restart, `shutdown`, or `not_found`", skill)
        self.assertIn("Do not call `resume_agent`", skill)
        self.assertIn("cross_restart_child_recovery_supported=false", skill)
        self.assertIn("## Mandatory continuity log", skill)
        self.assertIn("agents handoff-init", skill)
        self.assertIn("agents handoff-start", skill)
        self.assertIn("agents handoff-check", skill)
        self.assertIn("agents successor-register", skill)
        self.assertIn("--baseline-sha256", skill)
        self.assertIn("update as part of every child turn's definition of done", skill)
        self.assertIn("successor continuity from a durable handoff", skill)
        self.assertIn("agents register --agent-id", skill)
        self.assertIn("agents retire --agent-id", skill)
        self.assertIn("Codex's active child registry is process-local", skill)
        self.assertIn("Never call `close_agent` unless the user explicitly asks", skill)
        self.assertIn("recommend closing it and creating a replacement", skill)
        self.assertIn("do not close or replace it before the user decides", skill)
        self.assertIn("severe hallucination, corrupted context, or repeated operational failure", skill)
        self.assertNotIn("supported reattachment attempt", skill)

    def test_handoff_contract_is_machine_local_deterministic_and_secret_safe(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        handoff = (SKILL_ROOT / "references" / "handoff-log.md").read_text(encoding="utf-8")
        implementation = (
            SKILL_ROOT
            / "runtime"
            / "deepseek_subagent"
            / "core"
            / "agent_handoff.py"
        ).read_text(encoding="utf-8")
        self.assertIn(".local\\handoffs", skill)
        self.assertNotIn(".deepseek-subagent\\handoffs", skill)
        self.assertIn("machine-local persistent user data", skill)
        self.assertIn("stable role", handoff.lower())
        self.assertIn("exact supplied marker", handoff)
        self.assertIn("Do not request or store private chain-of-thought", handoff)
        self.assertIn("HANDOFF_ROOT_RELATIVE", implementation)
        self.assertIn("verify_handoff_update", implementation)
        self.assertIn("handoff_history_modified", implementation)
        self.assertIn("baseline_sha256", implementation)
        self.assertNotIn("opencode-go.key", implementation)

    def test_parent_isolation_and_fresh_reply_policy_are_explicit(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("agents list --all-parents --json", skill)
        self.assertIn("diagnostic only", skill)
        self.assertIn("A legacy entry without parent evidence stays non-operable", skill)
        self.assertIn("Require a fresh reply", skill)
        self.assertIn("Describe `context preserved` only when that fresh reply states", skill)
        self.assertIn("`roster=open`", skill)
        self.assertIn("never sufficient evidence", skill)
        self.assertIn("A newly created root task must not adopt children owned by the old root", skill)

    def test_resume_oriented_capability_field_is_removed(self) -> None:
        implementation = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                SKILL_ROOT / "scripts" / "skill_manager.py",
                SKILL_ROOT / "runtime" / "deepseek_subagent" / "platforms" / "codex" / "transport.py",
            )
        )
        self.assertIn("safe_to_spawn_send", implementation)
        self.assertNotIn("safe_to_spawn_resume_send", implementation)

    def test_release_tree_has_no_generated_cache_or_build_artifacts(self) -> None:
        forbidden_names = {"__pycache__", ".pytest_cache", "build", "dist"}
        forbidden_suffixes = {".pyc", ".pyo"}
        offenders = [
            item.relative_to(SKILL_ROOT).as_posix()
            for item in SKILL_ROOT.rglob("*")
            if item.name in forbidden_names or item.suffix in forbidden_suffixes
        ]
        self.assertEqual(offenders, [])

    def test_runtime_and_launcher_have_no_development_checkout_dependency(self) -> None:
        roots = [SKILL_ROOT / "runtime", SKILL_ROOT / "scripts"]
        forbidden = ("d:\\workspace\\codex", "sopc-skill-main", "deepseek-subagent-v1.7.1-fix")
        offenders = []
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in {".py", ".ps1"}:
                    continue
                text = path.read_text(encoding="utf-8").lower()
                if any(value in text for value in forbidden):
                    offenders.append(path.relative_to(SKILL_ROOT).as_posix())
        self.assertEqual(offenders, [])
        launcher = (SKILL_ROOT / "scripts" / "deepseek-subagent.ps1").read_text(encoding="utf-8")
        manager = (SKILL_ROOT / "scripts" / "skill_manager.py").read_text(encoding="utf-8")
        self.assertIn("$PSScriptRoot", launcher)
        self.assertIn("Path(__file__).resolve().parents[1]", manager)

    def test_double_click_prepare_launcher_is_user_and_location_independent(self) -> None:
        launcher = (SKILL_ROOT / "双击运行prepare.cmd").read_text(encoding="utf-8")
        self.assertIn("%~dp0scripts\\deepseek-subagent.ps1", launcher)
        self.assertIn("-NoProfile -ExecutionPolicy Bypass", launcher)
        self.assertIn('bootstrap --json', launcher)
        self.assertIn('repair --json', launcher)
        self.assertIn('pause', launcher.lower())
        self.assertNotIn("Administrator", launcher)
        self.assertNotIn("C:\\Users\\", launcher)

    def test_deepseek_transport_is_v1_only_and_fail_closed(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        compatibility = (SKILL_ROOT / "references" / "compatibility.md").read_text(encoding="utf-8")
        self.assertIn("prepare --json", skill)
        self.assertIn("automatic preconfiguration", skill.lower())
        self.assertIn("multi_agent_v1", skill)
        self.assertIn("Never fall back to V2", skill)
        self.assertIn("cannot mutate a task already initialized as V2", skill)
        self.assertIn("does not decrypt it", compatibility)


if __name__ == "__main__":
    unittest.main()
