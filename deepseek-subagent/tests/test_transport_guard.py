from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from deepseek_subagent.core.agent_roster import list_agents, register_agent  # noqa: E402
from deepseek_subagent.core.paths import state_paths  # noqa: E402
from deepseek_subagent.platforms.codex.transport import (  # noqa: E402
    assess_v1_transport,
    inspect_current_task,
    inspect_thread_identity,
)


class TransportGuardTests(unittest.TestCase):
    THREAD_ID = "12345678-1234-4abc-8def-1234567890ab"
    AGENT_ID = "87654321-4321-4cba-8fed-abcdef123456"
    DEEPSEEK = "deepseek-v4-flash"

    def _home(self, root: Path, task_version: str = "v1", parent_version: str = "v1") -> Path:
        home = root / "codex"
        sessions = home / "sessions" / "2026" / "08" / "09"
        sessions.mkdir(parents=True)
        catalog = home / "models-with-deepseek.json"
        catalog.write_text(
            json.dumps(
                {
                    "models": [
                        {"slug": "gpt-parent", "multi_agent_version": parent_version},
                        {"slug": self.DEEPSEEK, "multi_agent_version": "v1"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (home / "config.toml").write_text(
            f'model = "gpt-parent"\nmodel_catalog_json = "{catalog.as_posix()}"\n'
            "[features]\nmulti_agent = true\nmulti_agent_v2 = false\n",
            encoding="utf-8",
        )
        rollout = sessions / f"rollout-test-{self.THREAD_ID}.jsonl"
        records = [
            {"type": "session_meta", "payload": {"id": self.THREAD_ID, "model_provider": "openai"}},
            {
                "type": "turn_context",
                "payload": {
                    "model": "gpt-parent",
                    "model_provider": "openai",
                    "multi_agent_version": task_version,
                },
            },
        ]
        rollout.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
        return home

    @staticmethod
    def _static() -> dict:
        return {
            "status": "configured",
            "checks": {
                "desktop_multi_agent_enabled": True,
                "desktop_multi_agent_v2_disabled": True,
                "role_model_uses_plaintext_v1": True,
                "parent_uses_plaintext_v1": True,
                "compatibility_mode_ok": True,
            },
        }

    def test_v1_environment_allows_deepseek_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(Path(directory))
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.THREAD_ID}):
                result = assess_v1_transport(home, self.DEEPSEEK, self._static())
        self.assertTrue(result["safe_to_spawn_send"])
        self.assertEqual(result["operation_scope"], "current_codex_process_only")
        self.assertFalse(result["cross_restart_child_recovery_supported"])
        self.assertEqual(result["allowed_transport"], "v1")
        self.assertFalse(result["fallback_to_v2"])

    def test_v2_task_is_blocked_fail_fast(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(Path(directory), task_version="v2")
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.THREAD_ID}):
                result = assess_v1_transport(home, self.DEEPSEEK, self._static())
        self.assertFalse(result["safe_to_spawn_send"])
        self.assertEqual(result["error_code"], "current_task_multi_agent_v2")
        self.assertIn("新的 Codex task", result["message"])
        self.assertFalse(result["repair_changes_current_task"])

    def test_unconfirmed_task_never_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(Path(directory))
            with mock.patch.dict(os.environ, {}, clear=True):
                result = assess_v1_transport(home, self.DEEPSEEK, self._static())
        self.assertEqual(result["error_code"], "current_task_v1_unconfirmed")
        self.assertFalse(result["fallback_to_v2"])

    def test_disabled_v1_feature_blocks_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(Path(directory))
            static = self._static()
            static["checks"]["desktop_multi_agent_enabled"] = False
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.THREAD_ID}):
                result = assess_v1_transport(home, self.DEEPSEEK, static)
        self.assertFalse(result["safe_to_spawn_send"])
        self.assertFalse(result["checks"]["multi_agent_v1_enabled"])

    def test_actual_parent_model_must_be_v1(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(Path(directory), parent_version="v2")
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.THREAD_ID}):
                result = assess_v1_transport(home, self.DEEPSEEK, self._static())
        self.assertEqual(result["error_code"], "current_parent_model_not_v1")
        self.assertFalse(result["safe_to_spawn_send"])

    def test_rollout_evidence_uses_matching_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(Path(directory))
            evidence = inspect_current_task(home, self.THREAD_ID)
        self.assertTrue(evidence["detected"])
        self.assertEqual(evidence["model"], "gpt-parent")
        self.assertEqual(evidence["multi_agent_version"], "v1")

    def test_same_v1_parent_sees_diagnostic_roster_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = state_paths(root / "state")
            register_agent(
                state.agents,
                self.AGENT_ID,
                "Vivado reviewer",
                r"C:\project\vivado",
                self.THREAD_ID,
                root,
                handoff_root=root / "handoffs",
            )
            home = self._home(root)
            fresh_state = state_paths(root / "state")
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.THREAD_ID}):
                transport = assess_v1_transport(home, self.DEEPSEEK, self._static())
            recovered = list_agents(fresh_state.agents, self.THREAD_ID)
        self.assertTrue(transport["safe_to_spawn_send"])
        self.assertEqual(recovered[0]["agent_id"], self.AGENT_ID)
        self.assertTrue(recovered[0]["owned_by_current_parent"])

    def test_unrelated_root_cannot_adopt_another_roots_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            register_agent(path, self.AGENT_ID, "reviewer", "scope", self.THREAD_ID, Path(directory), handoff_root=Path(directory) / "handoffs")
            unrelated = "aaaaaaaa-4321-4cba-8fed-abcdef123456"
            self.assertEqual(list_agents(path, unrelated), [])
            diagnostic = list_agents(path, unrelated, include_other_parents=True)[0]
            self.assertFalse(diagnostic["owned_by_current_parent"])

    def test_child_parent_identity_requires_agreeing_native_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "codex"
            sessions = home / "sessions" / "2026" / "08" / "09"
            sessions.mkdir(parents=True)
            rollout = sessions / f"rollout-child-{self.AGENT_ID}.jsonl"
            rollout.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": self.AGENT_ID,
                            "parent_thread_id": self.THREAD_ID,
                            "thread_source": "subagent",
                            "agent_role": "DeepSeek",
                            "model_provider": "opencode-go-bridge",
                            "source": {
                                "subagent": {
                                    "thread_spawn": {"parent_thread_id": self.THREAD_ID}
                                }
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            evidence = inspect_thread_identity(home, self.AGENT_ID)
        self.assertTrue(evidence["parent_verified"])
        self.assertEqual(evidence["parent_thread_id"], self.THREAD_ID)

    def test_conflicting_native_parent_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "codex"
            sessions = home / "sessions" / "2026" / "08" / "09"
            sessions.mkdir(parents=True)
            other = "aaaaaaaa-4321-4cba-8fed-abcdef123456"
            rollout = sessions / f"rollout-child-{self.AGENT_ID}.jsonl"
            rollout.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": self.AGENT_ID,
                            "parent_thread_id": self.THREAD_ID,
                            "thread_source": "subagent",
                            "source": {
                                "subagent": {"thread_spawn": {"parent_thread_id": other}}
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            evidence = inspect_thread_identity(home, self.AGENT_ID)
        self.assertFalse(evidence["parent_verified"])
        self.assertIsNone(evidence["parent_thread_id"])
        self.assertEqual(evidence["error_code"], "child_parent_evidence_conflict")


if __name__ == "__main__":
    unittest.main()
