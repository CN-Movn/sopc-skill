from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "skill_manager.py"


def load_manager():
    spec = importlib.util.spec_from_file_location("deepseek_prepare_manager_test", MANAGER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AutomaticPrepareTests(unittest.TestCase):
    @staticmethod
    def _state(manager):
        temporary = tempfile.TemporaryDirectory()
        state = manager.state_paths(Path(temporary.name) / "state")
        return temporary, state

    @staticmethod
    def _configured() -> dict:
        return {"status": "configured", "checks": {}}

    @staticmethod
    def _ready_transport() -> dict:
        return {
            "status": "v1_transport_ready",
            "safe_to_spawn_send": True,
            "allowed_transport": "v1",
            "fallback_to_v2": False,
            "checks": {},
            "current_task": {
                "detected": True,
                "model": "gpt-parent",
                "multi_agent_version": "v1",
            },
        }

    def test_prepare_is_noop_when_environment_is_already_ready(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        lifecycle = mock.Mock()
        lifecycle.status.return_value = {"status": "running", "auto_start_task_valid": True}
        with (
            mock.patch.object(manager, "_current_task_evidence", return_value={"detected": True, "model": "gpt-parent"}),
            mock.patch.object(manager, "_capture", side_effect=[(0, self._configured()), (0, self._configured())]),
            mock.patch.object(manager, "_managed_skill_version_changed", return_value=False),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_bridge_runtime_ready", return_value=True),
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_ensure_bridge") as ensure_bridge,
            mock.patch.object(manager, "_repair_static_configuration") as repair,
            mock.patch.object(manager, "_transport_payload", return_value=self._ready_transport()),
        ):
            code, payload = manager._prepare_for_deepseek(state)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "deepseek_ready")
        self.assertFalse(payload["changed"])
        self.assertEqual(payload["actions"], [])
        ensure_bridge.assert_not_called()
        repair.assert_not_called()

    def test_prepare_repairs_static_drift_before_child_operation(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        lifecycle = mock.Mock()
        lifecycle.status.return_value = {"status": "running", "auto_start_task_valid": True}
        bridge = {"status": "running", "workdir": "bridge-work", "auto_start_task_valid": True}
        with (
            mock.patch.object(manager, "_current_task_evidence", return_value={"detected": True, "model": "gpt-parent"}),
            mock.patch.object(manager, "_capture", side_effect=[(2, {"status": "partial"}), (0, self._configured())]),
            mock.patch.object(manager, "_managed_skill_version_changed", return_value=False),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_bridge_runtime_ready", return_value=True),
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_ensure_bridge", return_value=bridge) as ensure_bridge,
            mock.patch.object(manager, "_repair_static_configuration", return_value=(0, {"status": "configured"})) as repair,
            mock.patch.object(manager, "_transport_payload", return_value=self._ready_transport()),
        ):
            code, payload = manager._prepare_for_deepseek(state)
        self.assertEqual(code, 0)
        self.assertTrue(payload["changed"])
        self.assertIn("static_repair", payload["actions"])
        ensure_bridge.assert_called_once_with(
            state,
            force_restart=False,
            current={"status": "running", "auto_start_task_valid": True},
        )
        repair.assert_called_once_with(state, bridge, "gpt-parent")

    def test_prepare_repairs_running_but_stale_bridge_before_child_operation(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        lifecycle = mock.Mock()
        lifecycle.status.return_value = {"status": "running", "auto_start_task_valid": True}
        bridge = {"status": "running", "workdir": "bridge-work", "auto_start_task_valid": True}
        with (
            mock.patch.object(manager, "_current_task_evidence", return_value={"detected": True, "model": "gpt-parent"}),
            mock.patch.object(manager, "_capture", side_effect=[(0, self._configured()), (0, self._configured())]),
            mock.patch.object(manager, "_managed_skill_version_changed", return_value=False),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_bridge_runtime_ready", return_value=False),
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_ensure_bridge", return_value=bridge) as ensure_bridge,
            mock.patch.object(manager, "_repair_static_configuration") as repair,
            mock.patch.object(manager, "_transport_payload", return_value=self._ready_transport()),
        ):
            code, payload = manager._prepare_for_deepseek(state)
        self.assertEqual(code, 0)
        self.assertIn("bridge_ensure", payload["actions"])
        ensure_bridge.assert_called_once_with(
            state,
            force_restart=False,
            current={"status": "running", "auto_start_task_valid": True},
        )
        repair.assert_not_called()

    def test_prepare_repairs_provider_once_after_isolated_bridge_replacement(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        lifecycle = mock.Mock()
        lifecycle.status.return_value = {
            "status": "unhealthy",
            "pid": 1111,
            "identity_verified": True,
            "bridge_abi_compatible": False,
        }
        bridge = {
            "status": "replaced_unrecoverable",
            "pid": 2222,
            "port": 2981,
            "workdir": "replacement",
            "provider_repair_required": True,
            "orphaned_pid": 1111,
        }
        with (
            mock.patch.object(manager, "_current_task_evidence", return_value={"detected": True, "model": "gpt-parent"}),
            mock.patch.object(manager, "_capture", side_effect=[(0, self._configured()), (0, self._configured())]),
            mock.patch.object(manager, "_managed_skill_version_changed", return_value=False),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_bridge_runtime_ready", return_value=False),
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_ensure_bridge", return_value=bridge) as ensure_bridge,
            mock.patch.object(manager, "_repair_static_configuration", return_value=(0, {"status": "configured"})) as repair,
            mock.patch.object(manager, "_transport_payload", return_value=self._ready_transport()),
        ):
            code, payload = manager._prepare_for_deepseek(state)
        self.assertEqual(code, 0)
        self.assertEqual(ensure_bridge.call_count, 1)
        self.assertEqual(repair.call_count, 1)
        self.assertIn("bridge_unrecoverable_replaced", payload["actions"])
        self.assertIn("static_repair", payload["actions"])

    def test_prepare_reuses_abi_compatible_bridge_after_skill_update(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        lifecycle = mock.Mock()
        lifecycle.status.return_value = {"status": "running", "auto_start_task_valid": True}
        bridge = {"status": "already_running", "workdir": "bridge-work", "bridge_abi_compatible": True}
        with (
            mock.patch.object(manager, "_current_task_evidence", return_value={"detected": True, "model": "gpt-parent"}),
            mock.patch.object(manager, "_capture", side_effect=[(0, self._configured()), (0, self._configured())]),
            mock.patch.object(manager, "_managed_skill_version_changed", return_value=True),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_bridge_runtime_ready", return_value=True),
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_ensure_bridge", return_value=bridge) as ensure_bridge,
            mock.patch.object(manager, "_repair_static_configuration", return_value=(0, {"status": "configured"})) as repair,
            mock.patch.object(manager, "_transport_payload", return_value=self._ready_transport()),
        ):
            code, payload = manager._prepare_for_deepseek(state)
        self.assertEqual(code, 0)
        self.assertEqual(
            payload["actions"],
            ["skill_update_bridge_reuse", "static_repair"],
        )
        ensure_bridge.assert_called_once_with(
            state,
            force_restart=False,
            current={"status": "running", "auto_start_task_valid": True},
        )
        repair.assert_called_once_with(state, bridge, "gpt-parent")

    def test_prepare_v2_task_prepares_same_parent_for_next_task_then_blocks(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        lifecycle = mock.Mock()
        lifecycle.status.return_value = {"status": "running", "auto_start_task_valid": True}
        bridge = {"status": "running", "workdir": "bridge-work", "auto_start_task_valid": True}
        blocked = {
            "status": "v1_transport_blocked",
            "safe_to_spawn_send": False,
            "error_code": "current_task_multi_agent_v2",
            "message": "new task required",
            "current_task": {"detected": True, "model": "gpt-ui-parent", "multi_agent_version": "v2"},
        }
        with (
            mock.patch.object(manager, "_current_task_evidence", return_value=blocked["current_task"]),
            mock.patch.object(manager, "_capture", side_effect=[(0, self._configured()), (0, self._configured())]),
            mock.patch.object(manager, "_managed_skill_version_changed", return_value=False),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_bridge_runtime_ready", return_value=True),
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_transport_payload", return_value=blocked),
            mock.patch.object(manager, "_ensure_bridge", return_value=bridge) as ensure_bridge,
            mock.patch.object(manager, "_repair_static_configuration", return_value=(0, {"status": "configured"})) as repair,
        ):
            code, payload = manager._prepare_for_deepseek(state)
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "configured_new_task_required")
        self.assertTrue(payload["new_task_required"])
        self.assertFalse(payload["safe_to_spawn_send"])
        self.assertIn("future_task_parent_v1_repair", payload["actions"])
        ensure_bridge.assert_called_once_with(state)
        repair.assert_called_once_with(state, bridge, "gpt-ui-parent")

    def test_prepare_command_returns_key_error_before_child_operation(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        lifecycle = mock.Mock()
        lifecycle.status.return_value = {"status": "stopped", "auto_start_task_valid": False}
        output = io.StringIO()
        with (
            mock.patch.object(manager, "_state", return_value=state),
            mock.patch.object(manager, "_current_task_evidence", return_value={"detected": True, "model": "gpt-parent"}),
            mock.patch.object(manager, "_capture", return_value=(2, {"status": "partial"})),
            mock.patch.object(manager, "_managed_skill_version_changed", return_value=False),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_require_key", side_effect=manager.ManagerError("upstream_key_missing", "missing key")),
            redirect_stdout(output),
        ):
            code = manager.main(["prepare", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "upstream_key_missing")

    def test_ensure_bridge_restarts_an_unhealthy_verified_process(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        lifecycle = mock.Mock()
        lifecycle.restart.return_value = {
            "status": "started",
            "pid": 2222,
            "workdir": "bridge-work",
        }
        current = {"status": "unhealthy", "pid": 1111, "identity_verified": True}
        with (
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
        ):
            result = manager._ensure_bridge(state, current=current)
        self.assertEqual(result["status"], "started")
        lifecycle.restart.assert_called_once()
        lifecycle.start.assert_not_called()
        lifecycle.status.assert_not_called()

    def test_ensure_bridge_replaces_unstoppable_process_without_killing_it(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        lifecycle = mock.Mock()
        lifecycle.restart.side_effect = manager.ManagerError(
            "bridge_stop_failed",
            "denied",
            {"stop_status": "stop_identity_unverified", "pid": 1111},
        )
        lifecycle.replace_unrecoverable.return_value = {
            "status": "replaced_unrecoverable",
            "pid": 2222,
            "port": 2981,
            "workdir": "replacement",
            "provider_repair_required": True,
            "orphaned_pid": 1111,
        }
        with (
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
        ):
            result = manager._ensure_bridge(
                state,
                current={"status": "unhealthy", "pid": 1111, "identity_verified": False},
            )
        self.assertEqual(result["status"], "replaced_unrecoverable")
        self.assertEqual(result["orphaned_pid"], 1111)
        lifecycle.restart.assert_called_once()
        lifecycle.replace_unrecoverable.assert_called_once()

    def test_ensure_bridge_reuses_healthy_abi_without_unnecessary_restart(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        bridge_root = Path(temporary.name) / "bridge"
        workdir = bridge_root / "runtime"
        workdir.mkdir(parents=True)
        (bridge_root / "bridge-runtime.json").write_text(
            json.dumps(
                {
                    "script": str((manager.RUNTIME_DIR / "scripts" / "bridge_standalone.py").resolve()),
                    "workdir": str(workdir.resolve()),
                    "bridge_abi_version": 1,
                }
            ),
            encoding="utf-8",
        )
        (workdir / "bridge.json").write_text(
            json.dumps(
                {
                    "token_file": str(manager._published_token_file().resolve()),
                    "token_script": str(
                        (manager.RUNTIME_DIR / "scripts" / "print_bridge_token.py").resolve()
                    ),
                    "bridge_abi_version": 1,
                }
            ),
            encoding="utf-8",
        )
        lifecycle = mock.Mock()
        lifecycle.start.return_value = {"status": "already_running", "pid": 1111}
        with (
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_bridge_state_root", return_value=bridge_root),
        ):
            result = manager._ensure_bridge(
                state,
                current={
                    "status": "running",
                    "identity_verified": True,
                    "bridge_abi_version": 1,
                    "bridge_abi_compatible": True,
                    "workdir": str(workdir.resolve()),
                    "port": 1981,
                },
            )
        self.assertEqual(result["status"], "already_running")
        self.assertEqual(result["workdir"], str(workdir.resolve()))
        lifecycle.start.assert_called_once()
        lifecycle.restart.assert_not_called()

    def test_ensure_bridge_restarts_when_bridge_json_is_missing(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        bridge_root = Path(temporary.name) / "bridge"
        workdir = bridge_root / "runtime"
        workdir.mkdir(parents=True)
        (bridge_root / "bridge-runtime.json").write_text(
            json.dumps(
                {
                    "script": str((manager.RUNTIME_DIR / "scripts" / "bridge_standalone.py").resolve()),
                    "workdir": str(workdir.resolve()),
                }
            ),
            encoding="utf-8",
        )
        lifecycle = mock.Mock()
        lifecycle.restart.return_value = {
            "status": "started",
            "pid": 2222,
            "workdir": str(workdir.resolve()),
        }
        with (
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_bridge_state_root", return_value=bridge_root),
        ):
            result = manager._ensure_bridge(
                state,
                current={"status": "running", "identity_verified": True},
            )
        self.assertEqual(result["status"], "started")
        lifecycle.restart.assert_called_once()
        lifecycle.start.assert_not_called()


    def test_normal_prepare_never_writes_appdata(self) -> None:
        """P0: with AppData write denied, a healthy prepare still reaches READY
        and provably performs zero AppData writes; all bridge mutable state
        lands under the canonical `.local` bridge root."""

        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        appdata = state.state_root
        bridge_root = Path(temporary.name) / "bridge"
        appdata_writes = {"count": 0}

        real_mkdir = Path.mkdir
        real_write_text = Path.write_text
        real_write_bytes = Path.write_bytes
        real_replace = Path.replace
        real_unlink = Path.unlink
        real_os_replace = os.replace
        real_os_unlink = os.unlink

        def blocked(target) -> bool:
            return str(Path(target).resolve()).casefold().startswith(
                str(appdata.resolve()).casefold() + os.sep
            )

        def guard(fn):
            def wrapper(*args, **kwargs):
                target = args[0] if args else kwargs.get("path")
                if blocked(target):
                    appdata_writes["count"] += 1
                    raise PermissionError(13, "Access is denied")
                return fn(*args, **kwargs)

            return wrapper

        lifecycle = mock.Mock()
        lifecycle.status.return_value = {"status": "not_started", "managed": False}
        lifecycle.start.return_value = {
            "status": "started",
            "pid": 2222,
            "workdir": str((bridge_root / "runtime").resolve()),
            "bridge_abi_version": 1,
            "bridge_abi_compatible": True,
        }
        with (
            mock.patch.object(manager, "_current_task_evidence", return_value={"detected": True, "model": "gpt-parent"}),
            mock.patch.object(manager, "_capture", side_effect=[(0, self._configured()), (0, self._configured())]),
            mock.patch.object(manager, "_managed_skill_version_changed", return_value=False),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_bridge_state_root", return_value=bridge_root),
            mock.patch.object(manager, "_transport_payload", return_value=self._ready_transport()),
            mock.patch.object(Path, "mkdir", side_effect=guard(real_mkdir)),
            mock.patch.object(Path, "write_text", side_effect=guard(real_write_text)),
            mock.patch.object(Path, "write_bytes", side_effect=guard(real_write_bytes)),
            mock.patch.object(Path, "replace", side_effect=guard(real_replace)),
            mock.patch.object(Path, "unlink", side_effect=guard(real_unlink)),
            mock.patch("os.replace", side_effect=guard(real_os_replace)),
            mock.patch("os.unlink", side_effect=guard(real_os_unlink)),
        ):
            code, payload = manager._prepare_for_deepseek(state)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "deepseek_ready")
        self.assertEqual(appdata_writes["count"], 0)
        self.assertEqual(
            str(Path(lifecycle.start.call_args.args[0]).resolve()),
            str((bridge_root / "runtime").resolve()),
        )


    def test_normal_prepare_never_writes_appdata_with_version_change(self) -> None:
        """A version-changed prepare (static repair path) also performs zero
        AppData writes; repair state lands in the canonical `.local` domain."""

        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        appdata = state.state_root
        bridge_root = Path(temporary.name) / "bridge"
        appdata_writes = {"count": 0}

        real_mkdir = Path.mkdir
        real_write_text = Path.write_text
        real_write_bytes = Path.write_bytes
        real_replace = Path.replace
        real_unlink = Path.unlink
        real_os_replace = os.replace
        real_os_unlink = os.unlink

        def blocked(target) -> bool:
            return str(Path(target).resolve()).casefold().startswith(
                str(appdata.resolve()).casefold() + os.sep
            )

        def guard(fn):
            def wrapper(*args, **kwargs):
                target = args[0] if args else kwargs.get("path")
                if blocked(target):
                    appdata_writes["count"] += 1
                    raise PermissionError(13, "Access is denied")
                return fn(*args, **kwargs)

            return wrapper

        lifecycle = mock.Mock()
        lifecycle.status.return_value = {"status": "not_started", "managed": False}
        lifecycle.start.return_value = {
            "status": "started",
            "pid": 2222,
            "workdir": str((bridge_root / "runtime").resolve()),
            "bridge_abi_version": 1,
            "bridge_abi_compatible": True,
        }
        with (
            mock.patch.object(manager, "_current_task_evidence", return_value={"detected": True, "model": "gpt-parent"}),
            mock.patch.object(manager, "_capture", side_effect=[(0, self._configured()), (0, self._configured())]),
            mock.patch.object(manager, "_managed_skill_version_changed", return_value=True),
            mock.patch.object(manager, "_lifecycle", return_value=lifecycle),
            mock.patch.object(manager, "_require_key"),
            mock.patch.object(manager, "_bridge_state_root", return_value=bridge_root),
            mock.patch.object(manager, "_repair_static_configuration", return_value=(0, {"status": "configured"})),
            mock.patch.object(manager, "_transport_payload", return_value=self._ready_transport()),
            mock.patch.object(Path, "mkdir", side_effect=guard(real_mkdir)),
            mock.patch.object(Path, "write_text", side_effect=guard(real_write_text)),
            mock.patch.object(Path, "write_bytes", side_effect=guard(real_write_bytes)),
            mock.patch.object(Path, "replace", side_effect=guard(real_replace)),
            mock.patch.object(Path, "unlink", side_effect=guard(real_unlink)),
            mock.patch("os.replace", side_effect=guard(real_os_replace)),
            mock.patch("os.unlink", side_effect=guard(real_os_unlink)),
        ):
            code, payload = manager._prepare_for_deepseek(state)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "deepseek_ready")
        self.assertEqual(appdata_writes["count"], 0)
        self.assertIn("static_repair", payload["actions"])

    def test_bridge_runtime_ready_rejects_stale_token_fingerprint(self) -> None:
        manager = load_manager()
        temporary, state = self._state(manager)
        self.addCleanup(temporary.cleanup)
        bridge_root = Path(temporary.name) / "bridge"
        workdir = bridge_root / "runtime"
        workdir.mkdir(parents=True)
        (bridge_root / "bridge-runtime.json").write_text(
            json.dumps(
                {
                    "script": str((manager.RUNTIME_DIR / "scripts" / "bridge_standalone.py").resolve()),
                    "workdir": str(workdir.resolve()),
                    "bridge_abi_version": 1,
                }
            ),
            encoding="utf-8",
        )
        (workdir / "bridge.json").write_text(
            json.dumps(
                {
                    "token_file": str(manager._published_token_file().resolve()),
                    "token_script": str(
                        (manager.RUNTIME_DIR / "scripts" / "print_bridge_token.py").resolve()
                    ),
                    "bridge_abi_version": 1,
                    "token_fingerprint": "sha256:stale-old-token",
                }
            ),
            encoding="utf-8",
        )
        current = {
            "status": "running",
            "identity_verified": True,
            "bridge_abi_version": 1,
            "bridge_abi_compatible": True,
        }
        with (
            mock.patch.object(manager, "_bridge_state_root", return_value=bridge_root),
            mock.patch.object(
                manager,
                "describe_token",
                return_value={"token_fingerprint": "sha256:current-token"},
            ),
        ):
            self.assertFalse(manager._bridge_runtime_ready(state, current))

        (workdir / "bridge.json").write_text(
            json.dumps(
                {
                    "token_file": str(manager._published_token_file().resolve()),
                    "token_script": str(
                        (manager.RUNTIME_DIR / "scripts" / "print_bridge_token.py").resolve()
                    ),
                    "bridge_abi_version": 1,
                    "token_fingerprint": "sha256:current-token",
                }
            ),
            encoding="utf-8",
        )
        with (
            mock.patch.object(manager, "_bridge_state_root", return_value=bridge_root),
            mock.patch.object(
                manager,
                "describe_token",
                return_value={"token_fingerprint": "sha256:current-token"},
            ),
        ):
            self.assertTrue(manager._bridge_runtime_ready(state, current))


if __name__ == "__main__":
    unittest.main()
