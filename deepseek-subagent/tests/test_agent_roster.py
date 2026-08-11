from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from deepseek_subagent.core.agent_roster import (  # noqa: E402
    list_agents,
    read_roster,
    register_agent,
    register_successor,
    retire_agent,
)
from deepseek_subagent.core.errors import ManagerError  # noqa: E402
from deepseek_subagent.core.paths import state_paths  # noqa: E402


class AgentRosterTests(unittest.TestCase):
    AGENT_ID = "12345678-1234-4abc-8def-1234567890ab"
    OTHER_AGENT_ID = "87654321-4321-4cba-8fed-abcdef123456"
    PARENT_ID = "aaaaaaaa-1234-4abc-8def-1234567890ab"
    OTHER_PARENT_ID = "bbbbbbbb-1234-4abc-8def-1234567890ab"

    def test_roster_survives_restart_as_diagnostic_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            first = state_paths(Path(directory) / "state")
            entry = register_agent(
                first.agents,
                self.AGENT_ID,
                "Vivado BD reviewer",
                r"C:\project\vivado",
                self.PARENT_ID,
                Path(directory),
                "Mencius",
                handoff_root=Path(directory) / "handoffs",
            )
            second = state_paths(Path(directory) / "state")
            recovered = list_agents(second.agents, self.PARENT_ID)
            self.assertEqual(entry["agent_id"], self.AGENT_ID)
            self.assertEqual(recovered[0]["parent_thread_id"], self.PARENT_ID)
            self.assertTrue(recovered[0]["owned_by_current_parent"])
            self.assertEqual(recovered[0]["handoff_status"], "ready")
            self.assertTrue(Path(recovered[0]["handoff_file"]).is_file())
            self.assertEqual(list_agents(second.agents, self.OTHER_PARENT_ID), [])
            self.assertNotIn("prompt", json.dumps(read_roster(second.agents)))

    def test_register_is_idempotent_and_reactivates_retired_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            register_agent(path, self.AGENT_ID, "role-a", "scope-a", self.PARENT_ID, Path(directory), handoff_root=Path(directory) / "handoffs")
            retire_agent(path, self.AGENT_ID, self.PARENT_ID)
            register_agent(path, self.AGENT_ID, "role-b", "scope-b", self.PARENT_ID, Path(directory), "Mencius", handoff_root=Path(directory) / "handoffs")
            agents = list_agents(path, self.PARENT_ID)
            self.assertEqual(len(agents), 1)
            self.assertEqual(agents[0]["state"], "open")
            self.assertEqual(agents[0]["stable_role"], "role-b")
            self.assertEqual(agents[0]["handoff_generation"], 1)

    def test_cross_parent_reassignment_and_retirement_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            register_agent(path, self.AGENT_ID, "role", "scope", self.PARENT_ID, Path(directory), handoff_root=Path(directory) / "handoffs")
            with self.assertRaises(ManagerError) as register_error:
                register_agent(path, self.AGENT_ID, "role", "scope", self.OTHER_PARENT_ID, Path(directory), handoff_root=Path(directory) / "handoffs")
            self.assertEqual(register_error.exception.code, "agent_parent_conflict")
            with self.assertRaises(ManagerError) as retire_error:
                retire_agent(path, self.AGENT_ID, self.OTHER_PARENT_ID)
            self.assertEqual(retire_error.exception.code, "agent_parent_mismatch")

    def test_same_parent_cannot_run_two_open_owners_for_one_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            register_agent(path, self.AGENT_ID, "Tom", "scope", self.PARENT_ID, Path(directory), handoff_root=Path(directory) / "handoffs")
            with self.assertRaises(ManagerError) as raised:
                register_agent(
                    path,
                    self.OTHER_AGENT_ID,
                    "tom",
                    "scope",
                    self.PARENT_ID,
                    Path(directory),
                    handoff_root=Path(directory) / "handoffs",
                )
            self.assertEqual(raised.exception.code, "handoff_active_owner_conflict")

    def test_other_parent_cannot_claim_an_open_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            project_root = Path(directory) / "project"
            project_root.mkdir()
            register_agent(path, self.AGENT_ID, "Tom", "scope", self.PARENT_ID, project_root, handoff_root=Path(directory) / "handoffs")
            with self.assertRaises(ManagerError) as raised:
                register_agent(
                    path,
                    self.OTHER_AGENT_ID,
                    "tom",
                    "scope",
                    self.OTHER_PARENT_ID,
                    project_root,
                    handoff_root=Path(directory) / "handoffs",
                )
            self.assertEqual(raised.exception.code, "handoff_owned_by_other_parent")
            self.assertEqual(
                len(list_agents(path, None, include_other_parents=True)),
                1,
            )

    def test_same_role_and_scope_in_different_projects_get_different_handoffs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = state_paths(root / "state").agents
            store = root / "handoffs"
            project_a = root / "project-a"
            project_a.mkdir()
            project_b = root / "project-b"
            project_b.mkdir()
            first = register_agent(
                path,
                self.AGENT_ID,
                "Tom",
                "scope",
                self.PARENT_ID,
                project_a,
                handoff_root=store,
            )
            second = register_agent(
                path,
                self.OTHER_AGENT_ID,
                "Tom",
                "scope",
                self.PARENT_ID,
                project_b,
                handoff_root=store,
            )
            self.assertNotEqual(first["handoff_file"], second["handoff_file"])
            self.assertFalse((project_a / ".deepseek-subagent").exists())
            self.assertFalse((project_b / ".deepseek-subagent").exists())
            self.assertEqual(len(list_agents(path, self.PARENT_ID)), 2)
            self.assertTrue(Path(first["handoff_file"]).is_file())
            self.assertTrue(Path(second["handoff_file"]).is_file())

    def test_explicit_successor_transfer_supersedes_old_owner_and_increments_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            project_root = Path(directory) / "project"
            project_root.mkdir()
            original = register_agent(
                path,
                self.AGENT_ID,
                "Tom",
                "scope",
                self.PARENT_ID,
                project_root,
                handoff_root=Path(directory) / "handoffs",
            )

            successor = register_successor(
                path,
                self.OTHER_AGENT_ID,
                self.AGENT_ID,
                "tom",
                "scope",
                self.OTHER_PARENT_ID,
                project_root,
                "Tom-next",
                handoff_root=Path(directory) / "handoffs",
            )

            self.assertEqual(successor["handoff_file"], original["handoff_file"])
            self.assertEqual(successor["handoff_generation"], original["handoff_generation"] + 1)
            self.assertEqual(successor["successor_of"], self.AGENT_ID)
            open_agents = list_agents(path, None, include_other_parents=True)
            self.assertEqual([item["agent_id"] for item in open_agents], [self.OTHER_AGENT_ID])
            all_agents = list_agents(
                path,
                None,
                include_retired=True,
                include_other_parents=True,
            )
            old = next(item for item in all_agents if item["agent_id"] == self.AGENT_ID)
            self.assertEqual(old["state"], "superseded")
            self.assertEqual(old["superseded_by"], self.OTHER_AGENT_ID)

    def test_successor_transfer_requires_exact_open_previous_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            project_root = Path(directory) / "project"
            project_root.mkdir()
            register_agent(path, self.AGENT_ID, "Tom", "scope", self.PARENT_ID, project_root, handoff_root=Path(directory) / "handoffs")
            retire_agent(path, self.AGENT_ID, self.PARENT_ID)
            with self.assertRaises(ManagerError) as raised:
                register_successor(
                    path,
                    self.OTHER_AGENT_ID,
                    self.AGENT_ID,
                    "Tom",
                    "scope",
                    self.OTHER_PARENT_ID,
                    project_root,
                    handoff_root=Path(directory) / "handoffs",
                )
            self.assertEqual(raised.exception.code, "handoff_previous_owner_not_open")

    def test_all_parent_view_is_diagnostic_and_not_operable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            register_agent(path, self.AGENT_ID, "role-a", "scope-a", self.PARENT_ID, Path(directory), handoff_root=Path(directory) / "handoffs")
            register_agent(path, self.OTHER_AGENT_ID, "role-b", "scope-b", self.OTHER_PARENT_ID, Path(directory), handoff_root=Path(directory) / "handoffs")
            agents = list_agents(
                path,
                self.PARENT_ID,
                include_other_parents=True,
            )
            by_id = {entry["agent_id"]: entry for entry in agents}
            self.assertTrue(by_id[self.AGENT_ID]["owned_by_current_parent"])
            self.assertFalse(by_id[self.OTHER_AGENT_ID]["owned_by_current_parent"])

    def test_legacy_parent_is_never_guessed_but_native_binding_can_resolve_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "agents": [
                            {
                                "agent_id": self.AGENT_ID,
                                "stable_role": "legacy",
                                "scope": "scope",
                                "state": "open",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(list_agents(path, self.PARENT_ID), [])
            diagnostic = list_agents(path, self.PARENT_ID, include_other_parents=True)
            self.assertIsNone(diagnostic[0]["parent_thread_id"])
            resolved = list_agents(
                path,
                self.PARENT_ID,
                parent_bindings={
                    self.AGENT_ID: {
                        "parent_thread_id": self.PARENT_ID,
                        "parent_evidence": "rollout_session_meta",
                    }
                },
            )
            self.assertTrue(resolved[0]["owned_by_current_parent"])
            self.assertEqual(resolved[0]["handoff_status"], "legacy_unconfigured")

    def test_schema_two_roster_migrates_in_memory_without_guessing_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "agents": [
                            {
                                "agent_id": self.AGENT_ID,
                                "stable_role": "legacy-v2",
                                "scope": "scope",
                                "parent_thread_id": self.PARENT_ID,
                                "parent_evidence": "rollout_session_meta",
                                "state": "open",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            migrated = read_roster(path)
            listed = list_agents(path, self.PARENT_ID)
        self.assertEqual(migrated["schema_version"], 3)
        self.assertEqual(listed[0]["handoff_status"], "legacy_unconfigured")

    def test_retired_agents_are_hidden_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            register_agent(path, self.AGENT_ID, "role", "scope", self.PARENT_ID, Path(directory), handoff_root=Path(directory) / "handoffs")
            retire_agent(path, self.AGENT_ID, self.PARENT_ID)
            self.assertEqual(list_agents(path, self.PARENT_ID), [])
            retired = list_agents(path, self.PARENT_ID, include_retired=True)[0]
            self.assertEqual(retired["state"], "retired")
            self.assertFalse(retired["owned_by_current_parent"])

    def test_invalid_id_and_corrupt_roster_fail_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = state_paths(Path(directory) / "state").agents
            with self.assertRaises(ManagerError):
                register_agent(path, "not-a-uuid", "role", "scope", self.PARENT_ID, Path(directory))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{broken", encoding="utf-8")
            before = path.read_bytes()
            with self.assertRaises(ManagerError) as raised:
                register_agent(path, self.AGENT_ID, "role", "scope", self.PARENT_ID, Path(directory), handoff_root=Path(directory) / "handoffs")
            self.assertEqual(raised.exception.code, "agent_roster_invalid")
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
