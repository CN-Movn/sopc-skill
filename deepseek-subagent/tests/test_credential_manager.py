from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "skill_manager.py"


def load_manager():
    spec = importlib.util.spec_from_file_location("deepseek_skill_manager_test", MANAGER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _handoff_root_patch(manager, root: Path):
    module = sys.modules[manager.initialize_handoff.__module__]
    stack = ExitStack()
    stack.enter_context(
        mock.patch.object(module, "default_handoff_root", return_value=root / "handoffs")
    )
    stack.enter_context(
        mock.patch.object(manager, "default_handoff_root", return_value=root / "handoffs")
    )
    stack.enter_context(
        mock.patch.object(manager, "_canonical_roster_file", return_value=root / "agents.json")
    )
    stack.enter_context(
        mock.patch.object(manager, "_legacy_appdata_root", return_value=root / "legacy")
    )
    return stack


class CredentialManagerTests(unittest.TestCase):
    def test_local_json_request_bypasses_environment_proxy(self):
        manager = load_manager()
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.read.return_value = b'{"status":"ok"}'
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(manager.urllib.request, "build_opener", return_value=opener) as build:
            status, body = manager._json_request("http://127.0.0.1:1981/health", "token")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})
        self.assertEqual(build.call_args.args[0].proxies, {})

    def test_credentials_status_reports_presence_without_value(self) -> None:
        manager = load_manager()
        secret = "manager-sentinel-key"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "opencode-go.key"
            target.write_text(secret + "\n", encoding="utf-8")
            output = io.StringIO()
            with (
                mock.patch.object(manager, "_local_key_file", return_value=target),
                mock.patch.object(manager, "_state", return_value=mock.Mock(state_root=Path(directory))),
                mock.patch.object(manager, "_lifecycle", return_value=mock.Mock()),
                redirect_stdout(output),
            ):
                code = manager.main(["credentials", "status", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "credential_present")
        self.assertNotIn(secret, output.getvalue())

    def test_credentials_set_and_key_argument_are_removed(self) -> None:
        manager = load_manager()
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            manager.main(["credentials", "set", "--key", "must-not-be-used"])

    def test_status_uses_real_doctor_pipeline(self) -> None:
        manager = load_manager()
        payload = {"status": "partial", "failure_stage": "auth_command", "error_code": "auth_command_failed"}
        with (
            mock.patch.object(manager, "_state", return_value=mock.Mock()),
            mock.patch.object(manager, "_lifecycle", return_value=mock.Mock()),
            mock.patch.object(manager, "_doctor", return_value=(2, payload)) as doctor,
            redirect_stdout(io.StringIO()),
        ):
            code = manager.main(["status", "--json"])
        self.assertEqual(code, 2)
        doctor.assert_called_once()

    def test_agents_register_and_list_do_not_require_bridge_runtime(self) -> None:
        manager = load_manager()
        agent_id = "12345678-1234-4abc-8def-1234567890ab"
        parent_id = "aaaaaaaa-1234-4abc-8def-1234567890ab"
        with tempfile.TemporaryDirectory() as directory:
            state = manager.state_paths(Path(directory) / "state")
            output = io.StringIO()
            with (
                mock.patch.object(manager, "_state", return_value=state),
                mock.patch.object(
                    manager,
                    "_current_root_thread",
                    return_value=(parent_id, {"detected": True}),
                ),
                mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                mock.patch.object(manager, "_verify_child_parent"),
                mock.patch.object(manager, "_lifecycle") as lifecycle,
                _handoff_root_patch(manager, Path(directory)),
                redirect_stdout(output),
            ):
                code = manager.main(
                    [
                        "agents",
                        "register",
                        "--agent-id",
                        agent_id,
                        "--stable-role",
                        "Vivado BD reviewer",
                        "--scope",
                        r"C:\project\vivado",
                        "--project-root",
                        directory,
                        "--nickname",
                        "Mencius",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            registered = json.loads(output.getvalue())
            self.assertEqual(registered["agent"]["agent_id"], agent_id)
            self.assertTrue(Path(registered["agent"]["handoff_file"]).is_file())
            lifecycle.assert_not_called()

            output = io.StringIO()
            with (
                mock.patch.object(manager, "_state", return_value=state),
                mock.patch.object(
                    manager,
                    "_current_root_thread",
                    return_value=(parent_id, {"detected": True}),
                ),
                mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                mock.patch.object(manager, "_lifecycle") as lifecycle,
                _handoff_root_patch(manager, Path(directory)),
                redirect_stdout(output),
            ):
                code = manager.main(["agents", "list", "--json"])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["count"], 1)
            self.assertTrue(payload["agents"][0]["owned_by_current_parent"])
            self.assertFalse(payload["cross_restart_child_recovery_supported"])
            self.assertEqual(payload["liveness_policy"], "fresh_child_reply_required")
            self.assertEqual(payload["continuity_strategy"], "project_handoff_log")
            self.assertNotIn("operable_by_current_parent", payload["agents"][0])
            lifecycle.assert_not_called()

            output = io.StringIO()
            with (
                mock.patch.object(manager, "_state", return_value=state),
                mock.patch.object(manager, "_current_root_thread", return_value=(parent_id, {"detected": True})),
                mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                _handoff_root_patch(manager, Path(directory)),
                redirect_stdout(output),
            ):
                code = manager.main(["agents", "handoff-start", "--agent-id", agent_id, "--json"])
            self.assertEqual(code, 0)
            turn = json.loads(output.getvalue())
            handoff_file = Path(turn["handoff_file"])
            with handoff_file.open("a", encoding="utf-8") as handle:
                handle.write("\n### completed\n" + turn["required_marker"] + "\n- Result: done\n")

            output = io.StringIO()
            with (
                mock.patch.object(manager, "_state", return_value=state),
                mock.patch.object(manager, "_current_root_thread", return_value=(parent_id, {"detected": True})),
                mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                _handoff_root_patch(manager, Path(directory)),
                redirect_stdout(output),
            ):
                code = manager.main(
                    [
                        "agents",
                        "handoff-check",
                        "--agent-id",
                        agent_id,
                        "--turn-token",
                        turn["turn_token"],
                        "--after-size",
                        str(turn["baseline_size"]),
                        "--baseline-sha256",
                        turn["baseline_sha256"],
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())["updated"])

    def test_handoff_init_prepares_first_turn_before_agent_spawn(self) -> None:
        manager = load_manager()
        parent_id = "aaaaaaaa-1234-4abc-8def-1234567890ab"
        with tempfile.TemporaryDirectory() as directory:
            state = manager.state_paths(Path(directory) / "state")
            output = io.StringIO()
            with (
                mock.patch.object(manager, "_state", return_value=state),
                mock.patch.object(manager, "_current_root_thread", return_value=(parent_id, {"detected": True})),
                mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                _handoff_root_patch(manager, Path(directory)),
                redirect_stdout(output),
            ):
                code = manager.main(
                    [
                        "agents",
                        "handoff-init",
                        "--stable-role",
                        "Tom",
                        "--scope",
                        "ARQ RX",
                        "--project-root",
                        directory,
                        "--json",
                    ]
                )
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "handoff_turn_ready")
        self.assertTrue(payload["update_required_before_accepting_child_result"])

    def test_handoff_init_uses_per_handoff_lock_not_global(self) -> None:
        manager = load_manager()
        parent_id = "aaaaaaaa-1234-4abc-8def-1234567890ab"
        with tempfile.TemporaryDirectory() as directory:
            state = manager.state_paths(Path(directory) / "state")
            output = io.StringIO()
            with (
                mock.patch.object(manager, "_state", return_value=state),
                mock.patch.object(
                    manager,
                    "_current_root_thread",
                    return_value=(parent_id, {"detected": True}),
                ),
                mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                _handoff_root_patch(manager, Path(directory)),
                mock.patch.object(
                    manager,
                    "_roster_lock",
                    side_effect=AssertionError("roster lock must not be used for handoff-init"),
                ),
                redirect_stdout(output),
            ):
                code = manager.main(
                    [
                        "agents",
                        "handoff-init",
                        "--stable-role",
                        "Tom",
                        "--scope",
                        "ARQ RX",
                        "--project-root",
                        directory,
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "handoff_turn_ready")
            lock_dir = Path(directory) / "handoffs" / ".locks"
            self.assertTrue(lock_dir.is_dir())
            self.assertEqual(len(list(lock_dir.glob("*.lock"))), 1)

    def test_handoff_lock_is_per_handoff_not_global(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with _handoff_root_patch(manager, root):
                lock_tx = manager._handoff_lock(str(root), "TX", "ARQ TX scheduler")
                lock_rx = manager._handoff_lock(str(root), "RX", "ARQ RX scheduler")
                lock_tx_again = manager._handoff_lock(str(root), "TX", "ARQ TX scheduler")
                self.assertNotEqual(lock_tx.path, lock_rx.path)
                self.assertEqual(lock_tx.path, lock_tx_again.path)
                self.assertIn(os.path.join("handoffs", ".locks"), str(lock_tx.path))
                with lock_tx:
                    with lock_rx:
                        pass
                with lock_tx:
                    with self.assertRaises(TimeoutError):
                        with lock_tx_again:
                            pass

    def test_handoff_init_lock_timeout_returns_structured_error(self) -> None:
        manager = load_manager()
        parent_id = "aaaaaaaa-1234-4abc-8def-1234567890ab"
        with tempfile.TemporaryDirectory() as directory:
            state = manager.state_paths(Path(directory) / "state")
            with _handoff_root_patch(manager, Path(directory)):
                held = manager._handoff_lock(directory, "Tom", "ARQ RX")
                held.acquire()
                try:
                    output = io.StringIO()
                    with (
                        mock.patch.object(manager, "_state", return_value=state),
                        mock.patch.object(
                            manager,
                            "_current_root_thread",
                            return_value=(parent_id, {"detected": True}),
                        ),
                        mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                        redirect_stdout(output),
                    ):
                        code = manager.main(
                            [
                                "agents",
                                "handoff-init",
                                "--stable-role",
                                "Tom",
                                "--scope",
                                "ARQ RX",
                                "--project-root",
                                directory,
                                "--json",
                            ]
                        )
                finally:
                    held.release()
            self.assertEqual(code, 2)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "operation_in_progress")
            self.assertIn("同一 handoff 操作仍在进行", payload["message"])
            self.assertNotIn("Traceback", output.getvalue())

    def test_roster_canonical_path_migrates_legacy_appdata_once(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            (root / "legacy" / "agents.json").parent.mkdir(parents=True, exist_ok=True)
            legacy_payload = {
                "schema_version": 3,
                "agents": [
                    {
                        "agent_id": "12345678-1234-4abc-8def-1234567890ab",
                        "stable_role": "TX",
                        "scope": "ARQ TX scheduler",
                        "parent_thread_id": "aaaaaaaa-1234-4abc-8def-1234567890ab",
                        "parent_evidence": "rollout_session_meta",
                        "handoff_file": "C:/handoffs/tx.md",
                        "handoff_generation": 3,
                        "state": "open",
                        "successor_of": None,
                    }
                ],
            }
            (root / "legacy" / "agents.json").write_text(json.dumps(legacy_payload), encoding="utf-8")
            with _handoff_root_patch(manager, root):
                canonical, notes = manager._roster_path(state)
                self.assertEqual(canonical, root / "agents.json")
                self.assertTrue(canonical.is_file())
                self.assertEqual(notes["roster_migrated_from"], str(root / "legacy" / "agents.json"))
                migrated = json.loads(canonical.read_text(encoding="utf-8"))
                entry = migrated["agents"][0]
                self.assertEqual(entry["handoff_generation"], 3)
                self.assertEqual(entry["state"], "open")
                self.assertEqual(entry["parent_thread_id"], "aaaaaaaa-1234-4abc-8def-1234567890ab")
                self.assertEqual(entry["handoff_file"], "C:/handoffs/tx.md")
                archive = (root / "legacy" / "agents.json").with_name("agents.json.migrated-v1.6.8.bak")
                self.assertTrue(archive.is_file())
                self.assertFalse((root / "legacy" / "agents.json").is_file())

                again, notes_again = manager._roster_path(state)
                self.assertEqual(again, canonical)
                self.assertNotIn("roster_migrated_from", notes_again)

    def test_roster_migration_archives_legacy_and_never_conflicts_after_updates(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            (root / "legacy" / "agents.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "legacy" / "agents.json").write_text(
                json.dumps({"schema_version": 3, "agents": []}),
                encoding="utf-8",
            )
            with _handoff_root_patch(manager, root):
                canonical, notes = manager._roster_path(state)
                self.assertIn("roster_migrated_from", notes)
                canonical.write_text(
                    json.dumps(
                        {
                            "schema_version": 3,
                            "agents": [
                                {
                                    "agent_id": "12345678-1234-4abc-8def-1234567890ab",
                                    "stable_role": "new",
                                    "scope": "new scope",
                                    "state": "open",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                again, notes_again = manager._roster_path(state)
                self.assertEqual(again, canonical)
                self.assertNotIn("roster_legacy_conflict", notes_again)
                self.assertNotIn("roster_migrated_from", notes_again)

    def test_roster_clean_reinstall_does_not_restore_archived_legacy(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            (root / "legacy" / "agents.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "legacy" / "agents.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "agents": [
                            {
                                "agent_id": "12345678-1234-4abc-8def-1234567890ab",
                                "stable_role": "stale",
                                "scope": "stale scope",
                                "state": "open",
                                "handoff_file": "C:/handoffs/stale.md",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with _handoff_root_patch(manager, root):
                canonical, notes = manager._roster_path(state)
                self.assertIn("roster_migrated_from", notes)
                self.assertTrue(canonical.is_file())
                canonical.unlink()
                again, notes_again = manager._roster_path(state)
                self.assertFalse(canonical.is_file())
                self.assertNotIn("roster_migrated_from", notes_again)
                self.assertTrue((root / "legacy" / "agents.json").with_name("agents.json.migrated-v1.6.8.bak").is_file())

    def test_roster_unfinished_migration_with_identical_content_is_archived(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            canonical = root / "agents.json"
            payload = json.dumps({"schema_version": 3, "agents": []})
            canonical.write_text(payload, encoding="utf-8")
            (root / "legacy" / "agents.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "legacy" / "agents.json").write_text(payload, encoding="utf-8")
            with _handoff_root_patch(manager, root):
                resolved, notes = manager._roster_path(state)
            self.assertEqual(resolved, canonical)
            self.assertNotIn("roster_legacy_conflict", notes)
            self.assertTrue((root / "legacy" / "agents.json").with_name("agents.json.migrated-v1.6.8.bak").is_file())
            self.assertFalse((root / "legacy" / "agents.json").is_file())

    def test_roster_migration_finalize_failed_rolls_back(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            (root / "legacy" / "agents.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "legacy" / "agents.json").write_text(
                json.dumps({"schema_version": 3, "agents": []}),
                encoding="utf-8",
            )
            with (
                _handoff_root_patch(manager, root),
                mock.patch.object(
                    Path,
                    "replace",
                    side_effect=OSError(5, "Access is denied"),
                ),
            ):
                with self.assertRaises(manager.ManagerError) as raised:
                    manager._roster_path(state)
            self.assertEqual(raised.exception.code, "roster_migration_finalize_failed")
            self.assertFalse((root / "agents.json").exists())
            self.assertTrue((root / "legacy" / "agents.json").is_file())

    def test_roster_conflict_archives_legacy_out_of_discovery(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            canonical = root / "agents.json"
            canonical.write_text(json.dumps({"schema_version": 3, "agents": []}), encoding="utf-8")
            (root / "legacy" / "agents.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "legacy" / "agents.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "agents": [
                            {
                                "agent_id": "12345678-1234-4abc-8def-1234567890ab",
                                "stable_role": "old",
                                "scope": "old scope",
                                "state": "open",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            canonical_before = canonical.read_bytes()
            digest = manager.sha256_bytes((root / "legacy" / "agents.json").read_bytes())[:12]
            with _handoff_root_patch(manager, root):
                resolved, notes = manager._roster_path(state)
            self.assertEqual(resolved, canonical)
            self.assertIn("roster_legacy_conflict_archived", notes)
            self.assertEqual(canonical.read_bytes(), canonical_before)
            self.assertFalse((root / "legacy" / "agents.json").is_file())
            conflict_archive = (root / "legacy" / "agents.json").with_name(f"agents.json.legacy-conflict-{digest}.bak")
            self.assertTrue(conflict_archive.is_file())
            self.assertEqual(notes["roster_legacy_conflict_archived"]["archived_to"], str(conflict_archive))

    def test_roster_divergent_legacy_never_restored_after_clean_reinstall(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            canonical = root / "agents.json"
            canonical.write_text(json.dumps({"schema_version": 3, "agents": []}), encoding="utf-8")
            (root / "legacy" / "agents.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "legacy" / "agents.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "agents": [
                            {
                                "agent_id": "12345678-1234-4abc-8def-1234567890ab",
                                "stable_role": "stale",
                                "scope": "stale scope",
                                "state": "open",
                                "handoff_file": "C:/handoffs/stale.md",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            digest = manager.sha256_bytes((root / "legacy" / "agents.json").read_bytes())[:12]
            with _handoff_root_patch(manager, root):
                resolved, notes = manager._roster_path(state)
                self.assertEqual(resolved, canonical)
                self.assertIn("roster_legacy_conflict_archived", notes)
                self.assertFalse((root / "legacy" / "agents.json").is_file())

                canonical.unlink()
                again, notes_again = manager._roster_path(state)
                self.assertFalse(canonical.is_file())
                self.assertNotIn("roster_migrated_from", notes_again)
                self.assertTrue(
                    (root / "legacy" / "agents.json").with_name(f"agents.json.legacy-conflict-{digest}.bak").is_file()
                )

    def test_handoff_init_preflight_blocks_foreign_open_owner_before_spawn(self) -> None:
        manager = load_manager()
        parent_a = "aaaaaaaa-1234-4abc-8def-1234567890ab"
        parent_b = "bbbbbbbb-1234-4abc-8def-1234567890ab"
        agent_a = "12345678-1234-4abc-8def-1234567890ab"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            with _handoff_root_patch(manager, root):
                roster, _ = manager._roster_path(state)
                manager.register_agent(
                    roster,
                    agent_a,
                    "Tom",
                    "ARQ RX",
                    parent_a,
                    directory,
                    handoff_root=root / "handoffs",
                )
                output = io.StringIO()
                with (
                    mock.patch.object(manager, "_state", return_value=state),
                    mock.patch.object(
                        manager,
                        "_current_root_thread",
                        return_value=(parent_b, {"detected": True}),
                    ),
                    mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                    redirect_stdout(output),
                ):
                    code = manager.main(
                        [
                            "agents",
                            "handoff-init",
                            "--stable-role",
                            "Tom",
                            "--scope",
                            "ARQ RX",
                            "--project-root",
                            directory,
                            "--json",
                        ]
                    )
            self.assertEqual(code, 2)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "handoff_owned_by_other_parent")
            self.assertEqual(payload["owner_agent_id"], agent_a)
            self.assertEqual(payload["owner_parent_thread_id"], parent_a)
            self.assertNotIn("turn_token", payload)

    def test_handoff_init_preflight_allows_current_parent_owner(self) -> None:
        manager = load_manager()
        parent_id = "aaaaaaaa-1234-4abc-8def-1234567890ab"
        agent_id = "12345678-1234-4abc-8def-1234567890ab"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            with _handoff_root_patch(manager, root):
                roster, _ = manager._roster_path(state)
                manager.register_agent(
                    roster,
                    agent_id,
                    "Tom",
                    "ARQ RX",
                    parent_id,
                    directory,
                    handoff_root=root / "handoffs",
                )
                output = io.StringIO()
                with (
                    mock.patch.object(manager, "_state", return_value=state),
                    mock.patch.object(
                        manager,
                        "_current_root_thread",
                        return_value=(parent_id, {"detected": True}),
                    ),
                    mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                    redirect_stdout(output),
                ):
                    code = manager.main(
                        [
                            "agents",
                            "handoff-init",
                            "--stable-role",
                            "Tom",
                            "--scope",
                            "ARQ RX",
                            "--project-root",
                            directory,
                            "--json",
                        ]
                    )
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "handoff_turn_ready")
            self.assertFalse(payload["created"])

    def test_handoff_init_preflight_ignores_superseded_owner(self) -> None:
        manager = load_manager()
        parent_a = "aaaaaaaa-1234-4abc-8def-1234567890ab"
        parent_b = "bbbbbbbb-1234-4abc-8def-1234567890ab"
        agent_a = "12345678-1234-4abc-8def-1234567890ab"
        agent_b = "87654321-4321-4cba-8fed-abcdef123456"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            with _handoff_root_patch(manager, root):
                roster, _ = manager._roster_path(state)
                manager.register_agent(
                    roster,
                    agent_a,
                    "Tom",
                    "ARQ RX",
                    parent_a,
                    directory,
                    handoff_root=root / "handoffs",
                )
                manager.register_successor(
                    roster,
                    agent_b,
                    agent_a,
                    "Tom",
                    "ARQ RX",
                    parent_b,
                    directory,
                    handoff_root=root / "handoffs",
                )
                output = io.StringIO()
                with (
                    mock.patch.object(manager, "_state", return_value=state),
                    mock.patch.object(
                        manager,
                        "_current_root_thread",
                        return_value=(parent_b, {"detected": True}),
                    ),
                    mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                    redirect_stdout(output),
                ):
                    code = manager.main(
                        [
                            "agents",
                            "handoff-init",
                            "--stable-role",
                            "Tom",
                            "--scope",
                            "ARQ RX",
                            "--project-root",
                            directory,
                            "--json",
                        ]
                    )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "handoff_turn_ready")

    def test_reinstall_prep_stops_verified_bridge(self) -> None:
        manager = load_manager()
        state = manager.state_paths(Path(tempfile.gettempdir()) / "reinstall-prep-test-state")
        lifecycle = mock.Mock()
        lifecycle.status.return_value = {
            "status": "running",
            "pid": 4242,
            "identity_verified": True,
        }
        lifecycle.stop.return_value = {
            "status": "stopped",
            "pid": 4242,
            "identity_verified": True,
            "control_status": "control_shutdown_accepted",
        }
        output = io.StringIO()
        with (
            mock.patch.object(manager, "_state", return_value=state),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            redirect_stdout(output),
        ):
            code = manager.main(["reinstall-prep", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ready_for_reinstall"])
        self.assertTrue(payload["bridge_stopped"])
        lifecycle.stop.assert_called_once()

    def test_reinstall_prep_idempotent_without_bridge(self) -> None:
        manager = load_manager()
        state = manager.state_paths(Path(tempfile.gettempdir()) / "reinstall-prep-test-state")
        lifecycle = mock.Mock()
        lifecycle.status.return_value = {"status": "not_started", "managed": False}
        output = io.StringIO()
        with (
            mock.patch.object(manager, "_state", return_value=state),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            redirect_stdout(output),
        ):
            code = manager.main(["reinstall-prep", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ready_for_reinstall"])
        self.assertFalse(payload["bridge_stopped"])
        lifecycle.stop.assert_not_called()

    def test_reinstall_prep_fails_closed_on_unverified_identity(self) -> None:
        manager = load_manager()
        state = manager.state_paths(Path(tempfile.gettempdir()) / "reinstall-prep-test-state")
        lifecycle = mock.Mock()
        lifecycle.status.return_value = {
            "status": "running",
            "pid": 4242,
            "identity_verified": False,
        }
        lifecycle.stop.return_value = {
            "status": "stop_identity_unverified",
            "pid": 4242,
            "identity_verified": False,
            "stopped": False,
        }
        output = io.StringIO()
        with (
            mock.patch.object(manager, "_state", return_value=state),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            redirect_stdout(output),
        ):
            code = manager.main(["reinstall-prep", "--json"])
        self.assertEqual(code, 2)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ready_for_reinstall"])
        self.assertEqual(payload["error_code"], "stop_identity_unverified")

    def test_reinstall_prep_blocked_when_stop_fails(self) -> None:
        manager = load_manager()
        state = manager.state_paths(Path(tempfile.gettempdir()) / "reinstall-prep-test-state")
        lifecycle = mock.Mock()
        lifecycle.status.return_value = {
            "status": "running",
            "pid": 4242,
            "identity_verified": True,
        }
        lifecycle.stop.return_value = {
            "status": "stop_failed",
            "pid": 4242,
            "identity_verified": True,
            "stopped": False,
        }
        output = io.StringIO()
        with (
            mock.patch.object(manager, "_state", return_value=state),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            redirect_stdout(output),
        ):
            code = manager.main(["reinstall-prep", "--json"])
        self.assertEqual(code, 2)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ready_for_reinstall"])
        self.assertEqual(payload["error_code"], "stop_failed")

    def test_roster_lock_is_local_domain_not_appdata(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with _handoff_root_patch(manager, root):
                lock = manager._roster_lock()
                self.assertEqual(
                    str(Path(lock.path).resolve()),
                    str((root / "locks" / "agents.lock").resolve()),
                )

    def test_parallel_register_two_agents_succeeds_once(self) -> None:
        manager = load_manager()
        parent_id = "aaaaaaaa-1234-4abc-8def-1234567890ab"
        agent_tx = "12345678-1234-4abc-8def-1234567890ab"
        agent_rx = "87654321-4321-4cba-8fed-abcdef123456"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            with _handoff_root_patch(manager, root):
                roster, _ = manager._roster_path(state)
                errors: list[str] = []
                barrier = threading.Barrier(2)

                def do_register(agent_id, role, scope):
                    try:
                        barrier.wait(timeout=10)
                        with manager._roster_lock():
                            manager.register_agent(
                                roster,
                                agent_id,
                                role,
                                scope,
                                parent_id,
                                directory,
                                handoff_root=root / "handoffs",
                            )
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{agent_id}: {type(exc).__name__}: {exc}")

                t1 = threading.Thread(target=do_register, args=(agent_tx, "TX", "ARQ TX scheduler"))
                t2 = threading.Thread(target=do_register, args=(agent_rx, "RX", "ARQ RX scheduler"))
                t1.start()
                t2.start()
                t1.join(timeout=30)
                t2.join(timeout=30)
                self.assertEqual(errors, [])
                entries = manager.list_agents(roster, parent_id)
                self.assertEqual({e["agent_id"] for e in entries}, {agent_tx, agent_rx})
                self.assertTrue(all(e["state"] == "open" for e in entries))

    def test_roster_lock_timeout_returns_structured_error(self) -> None:
        manager = load_manager()
        parent_id = "aaaaaaaa-1234-4abc-8def-1234567890ab"
        agent_id = "12345678-1234-4abc-8def-1234567890ab"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            with _handoff_root_patch(manager, root):
                held = manager._roster_lock()
                held.acquire()
                try:
                    output = io.StringIO()
                    with (
                        mock.patch.object(manager, "_state", return_value=state),
                        mock.patch.object(
                            manager,
                            "_current_root_thread",
                            return_value=(parent_id, {"detected": True}),
                        ),
                        mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                        mock.patch.object(manager, "_verify_child_parent"),
                        redirect_stdout(output),
                    ):
                        code = manager.main(
                            [
                                "agents",
                                "register",
                                "--agent-id",
                                agent_id,
                                "--stable-role",
                                "TX",
                                "--scope",
                                "ARQ TX scheduler",
                                "--project-root",
                                directory,
                                "--json",
                            ]
                        )
                finally:
                    held.release()
            self.assertEqual(code, 2)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "operation_in_progress")
            self.assertIn("roster 操作正在进行", payload["message"])
            self.assertNotIn("Traceback", output.getvalue())

    def test_register_verifies_child_before_roster_lock(self) -> None:
        manager = load_manager()
        parent_id = "aaaaaaaa-1234-4abc-8def-1234567890ab"
        agent_id = "12345678-1234-4abc-8def-1234567890ab"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = manager.state_paths(root / "state")
            order: list[str] = []
            real_lock = manager._roster_lock

            def spy_lock():
                order.append("lock")
                return real_lock()

            def spy_verify(*_args, **_kwargs):
                order.append("verify")

            with _handoff_root_patch(manager, root):
                output = io.StringIO()
                with (
                    mock.patch.object(manager, "_state", return_value=state),
                    mock.patch.object(
                        manager,
                        "_current_root_thread",
                        return_value=(parent_id, {"detected": True}),
                    ),
                    mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                    mock.patch.object(manager, "_verify_child_parent", side_effect=spy_verify),
                    mock.patch.object(manager, "_roster_lock", side_effect=spy_lock),
                    redirect_stdout(output),
                ):
                    code = manager.main(
                        [
                            "agents",
                            "register",
                            "--agent-id",
                            agent_id,
                            "--stable-role",
                            "TX",
                            "--scope",
                            "ARQ TX scheduler",
                            "--project-root",
                            directory,
                            "--json",
                        ]
                    )
            self.assertEqual(code, 0)
            self.assertEqual(order, ["verify", "lock"])

    def test_successor_register_transfers_global_handoff_ownership(self) -> None:
        manager = load_manager()
        old_agent_id = "12345678-1234-4abc-8def-1234567890ab"
        new_agent_id = "87654321-4321-4cba-8fed-abcdef123456"
        old_parent_id = "aaaaaaaa-1234-4abc-8def-1234567890ab"
        new_parent_id = "bbbbbbbb-1234-4abc-8def-1234567890ab"
        with tempfile.TemporaryDirectory() as directory:
            state = manager.state_paths(Path(directory) / "state")
            with _handoff_root_patch(manager, Path(directory)):
                roster, _ = manager._roster_path(state)
                manager.register_agent(
                    roster,
                    old_agent_id,
                    "Tom",
                    "ARQ RX",
                    old_parent_id,
                    Path(directory),
                    handoff_root=Path(directory) / "handoffs",
                )
            output = io.StringIO()
            with (
                mock.patch.object(manager, "_state", return_value=state),
                mock.patch.object(
                    manager,
                    "_current_root_thread",
                    return_value=(new_parent_id, {"detected": True}),
                ),
                mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                mock.patch.object(manager, "_verify_child_parent") as verify_parent,
                _handoff_root_patch(manager, Path(directory)),
                redirect_stdout(output),
            ):
                code = manager.main(
                    [
                        "agents",
                        "successor-register",
                        "--agent-id",
                        new_agent_id,
                        "--previous-agent-id",
                        old_agent_id,
                        "--stable-role",
                        "Tom",
                        "--scope",
                        "ARQ RX",
                        "--project-root",
                        directory,
                        "--json",
                    ]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "agent_successor_registered")
            self.assertTrue(payload["ownership_transferred"])
            self.assertEqual(payload["agent"]["successor_of"], old_agent_id)
            self.assertEqual(payload["agent"]["handoff_generation"], 2)
            verify_parent.assert_called_once_with(state, new_agent_id, new_parent_id)

    def test_doctor_accepts_running_on_demand_bridge_without_autostart_task(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory() as directory:
            state = manager.state_paths(Path(directory) / "state")
            codex_home = Path(directory) / "codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                '[model_providers.opencode-go-bridge]\nbase_url = "http://127.0.0.1:1981/v1"\n'
                '[model_providers.opencode-go-bridge.auth]\ncommand = "token-command"\nargs = []\n',
                encoding="utf-8",
            )
            manager.write_manifest(state.state_root, {"platform_home": str(codex_home)})
            lifecycle = mock.Mock()
            lifecycle.status.return_value = {
                "status": "running",
                "auto_start_task_present": False,
                "bridge_abi_compatible": True,
                "bridge_abi_version": 1,
                "launch_mode": "on_demand",
                "base_url": "http://127.0.0.1:1981/v1",
            }
            parent_id = "aaaaaaaa-1234-4abc-8def-1234567890ab"
            with (
                mock.patch.object(
                    manager,
                    "_capture",
                    return_value=(
                        0,
                        {
                            "status": "configured",
                            "checks": {"desktop_codex_path": "codex.exe"},
                        },
                    ),
                ),
                mock.patch.object(
                    manager,
                    "_transport_payload",
                    return_value={
                        "safe_to_spawn_send": True,
                        "checks": {
                            "multi_agent_v1_enabled": True,
                            "multi_agent_v2_disabled": True,
                            "deepseek_model_v1": True,
                            "configured_parent_v1": True,
                            "current_task_v1": True,
                            "current_parent_model_v1": True,
                        },
                    },
                ),
                mock.patch.object(manager, "credential_status", return_value={"status": "credential_present"}),
                mock.patch.object(manager, "discover_credential", return_value=mock.Mock()),
                mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
                mock.patch.object(manager, "_current_root_thread", return_value=(parent_id, {"detected": True})),
                mock.patch.object(manager, "_authoritative_parent_bindings", return_value={}),
                mock.patch.object(manager, "_json_request", return_value=(200, {"status": "ok"})),
                mock.patch.object(
                    manager.subprocess,
                    "run",
                    return_value=mock.Mock(returncode=0, stdout="local-token\n", stderr=""),
                ),
                mock.patch.object(manager, "direct_test"),
            ):
                code, payload = manager._doctor(state)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "configured")
        self.assertTrue(payload["checks"]["bridge_process"])
        self.assertTrue(payload["checks"]["bridge_abi"])
        self.assertTrue(payload["checks"]["bridge_on_demand"])

    def test_doctor_blocks_v2_before_bridge_diagnosis(self) -> None:
        manager = load_manager()
        state = mock.Mock()
        blocked = {
            "safe_to_spawn_send": False,
            "error_code": "current_task_multi_agent_v2",
            "message": "create a new Codex task",
            "checks": {},
        }
        with (
            mock.patch.object(manager, "_capture", return_value=(0, {"status": "configured"})),
            mock.patch.object(manager, "_transport_payload", return_value=blocked),
            mock.patch.object(manager, "_lifecycle") as lifecycle,
        ):
            code, payload = manager._doctor(state)
        self.assertEqual(code, 2)
        self.assertEqual(payload["failure_stage"], "current_task_transport")
        self.assertEqual(payload["error_code"], "current_task_multi_agent_v2")
        lifecycle.assert_not_called()

    def test_repair_does_not_claim_current_v2_task_changed_in_place(self) -> None:
        manager = load_manager()
        state = mock.Mock(state_root=Path("state"))
        lifecycle = mock.Mock()
        bridge = {"workdir": "bridge-work", "status": "running"}
        blocked = {
            "safe_to_spawn_send": False,
            "error_code": "current_task_multi_agent_v2",
            "message": "new task required",
        }
        output = io.StringIO()
        with (
            mock.patch.object(manager, "_state", return_value=state),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_ensure_bridge", return_value=bridge),
            mock.patch.object(manager, "_capture", return_value=(0, {"status": "configured"})),
            mock.patch.object(manager, "_annotate_manifest"),
            mock.patch.object(manager, "_transport_payload", return_value=blocked),
            redirect_stdout(output),
        ):
            code = manager.main(["repair", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "configured_new_task_required")
        self.assertTrue(payload["new_task_required"])


if __name__ == "__main__":
    unittest.main()
