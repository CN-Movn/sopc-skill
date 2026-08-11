"""桥后台生命周期测试（Windows 用户级 Task Scheduler）。

覆盖：启动、重复启动幂等、停止、异常退出、残留 PID 恢复、端口占用、
卸载清理、pid 文件写入。全部 mock 掉 schtasks/tasklist/网络，不接触
真实计划任务与真实桥进程。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from deepseek_subagent.bridges.opencode_go.lifecycle import (  # noqa: E402
    AUTO_START_TASK_NAME,
    WINDOWS_EPOCH_FILETIME,
    TASK_NAME,
    BridgeLifecycle,
)
from deepseek_subagent.core.errors import ManagerError  # noqa: E402

PYTHONW = r"C:\venv\Scripts\pythonw.exe"
SCRIPT = r"D:\repo\deepseek-subagent\runtime\scripts\bridge_standalone.py"


def patch_tools(
    alive=None,
    health=None,
    port_open=None,
    schtasks_returncode=0,
    task_exists=None,
):
    def _decorator(fn):
        @mock.patch.object(BridgeLifecycle, "_listener_pid", return_value=None)
        @mock.patch.object(BridgeLifecycle, "_wait_bridge_info")
        @mock.patch.object(BridgeLifecycle, "_wait_stopped", return_value=True)
        @mock.patch.object(BridgeLifecycle, "_spawn_bridge")
        @mock.patch.object(
            BridgeLifecycle,
            "_authenticated_shutdown",
            return_value={"status": "control_unsupported", "accepted": False},
        )
        @mock.patch(
            "deepseek_subagent.bridges.opencode_go.lifecycle.process_creation_time",
            return_value=42,
        )
        @mock.patch.object(
            BridgeLifecycle,
            "_pid_alive",
            side_effect=alive if callable(alive) else (lambda *_: alive),
        )
        @mock.patch.object(
            BridgeLifecycle,
            "_health_probe",
            return_value={
                "healthy": health is True,
                "http_status": 200 if health is True else 0,
                "error_code": None if health is True else "bridge_unreachable",
            },
        )
        @mock.patch.object(BridgeLifecycle, "_port_open", return_value=port_open)
        @mock.patch.object(
            BridgeLifecycle,
            "_task_exists",
            return_value=True if task_exists is None else task_exists,
        )
        @mock.patch("subprocess.run")
        @mock.patch.object(BridgeLifecycle, "_wait_pid")
        def wrapper(
            self,
            wait_pid,
            run,
            task_exists_m,
            port_open_m,
            health_m,
            alive_m,
            creation_time_m,
            shutdown_m,
            spawn_m,
            wait_stopped_m,
            bridge_info_m,
            listener_pid_m,
        ):
            run.return_value = mock.Mock(returncode=schtasks_returncode, stdout="", stderr="")
            bridge_info_m.side_effect = lambda _workdir, pid, requested_port: {
                "pid": pid,
                "port": requested_port or 2981,
                "base_url": f"http://127.0.0.1:{requested_port or 2981}/v1",
                "bridge_instance_id": "started-instance",
                "bridge_abi_version": 1,
                "launch_mode": "on_demand",
            }
            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.token_store._resolve_windows_principal_sids",
                return_value=["S-1-5-21-1000"],
            ):
                return fn(
                    self,
                    wait_pid=wait_pid,
                    run=run,
                    alive=alive_m,
                    health=health_m,
                    port_open=port_open_m,
                    task_exists=task_exists_m,
                )

        return wrapper

    return _decorator


class BridgeLifecycleTests(unittest.TestCase):
    def _make(self, directory):
        state_root = Path(directory) / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        workdir = Path(directory) / "work"
        token_dir = Path(directory) / ".local"
        return BridgeLifecycle(
            state_root,
            pythonw=PYTHONW,
            script=SCRIPT,
            token_dir=str(token_dir),
        ), state_root, workdir

    def _runtime(self, state_root, pid=1234, port=1981, workdir=None):
        root = Path(state_root).parent
        workdir_path = Path(workdir) if workdir else root / "work"
        workdir_path.mkdir(parents=True, exist_ok=True)
        workdir_path.joinpath("bridge.pid").write_text(str(pid), encoding="utf-8")
        workdir_path.joinpath("bridge.json").write_text(
            json.dumps(
                {
                    "pid": pid,
                    "port": port,
                    "base_url": f"http://127.0.0.1:{port}/v1",
                    "workdir": str(workdir_path.resolve()),
                    "pythonw": str(Path(PYTHONW).resolve()),
                    "script": str(Path(SCRIPT).resolve()),
                    "bridge_instance_id": "test-instance",
                    "bridge_abi_version": 1,
                    "launch_mode": "on_demand",
                    "process_creation_time": 42,
                    "token_file": str((root / ".local" / "local-bridge-token.txt").resolve()),
                    "token_script": str(
                        (
                            Path(SCRIPT).parents[2]
                            / "runtime"
                            / "scripts"
                            / "print_bridge_token.py"
                        ).resolve()
                    ),
                    "token_version": 1,
                    "token_generation": 1,
                    "token_fingerprint": "sha256:test",
                }
            ),
            encoding="utf-8",
        )
        state_root.joinpath("bridge-runtime.json").write_text(
            json.dumps(
                {
                    "pid": pid,
                    "port": port,
                    "workdir": str(workdir_path),
                    "base_url": f"http://127.0.0.1:{port}/v1",
                    "started_at": "2026-08-05T18:00:00",
                    "task_name": TASK_NAME,
                    "auto_start_task": AUTO_START_TASK_NAME,
                    "pythonw": str(Path(PYTHONW).resolve()),
                    "script": str(Path(SCRIPT).resolve()),
                    "bridge_instance_id": "test-instance",
                    "bridge_abi_version": 1,
                    "launch_mode": "on_demand",
                    "process_creation_time": 42,
                    "token_file": str((root / ".local" / "local-bridge-token.txt").resolve()),
                    "token_script": str(
                        (
                            Path(SCRIPT).parents[2]
                            / "runtime"
                            / "scripts"
                            / "print_bridge_token.py"
                        ).resolve()
                    ),
                }
            ),
            encoding="utf-8",
        )

    def _make_legacy_runtime(self, state_root, pid=1234, port=1981, workdir=None):
        self._runtime(state_root, pid=pid, port=port, workdir=workdir)
        root = Path(state_root).parent
        workdir_path = Path(workdir) if workdir else root / "work"
        info = json.loads((workdir_path / "bridge.json").read_text(encoding="utf-8"))
        runtime = json.loads((state_root / "bridge-runtime.json").read_text(encoding="utf-8"))
        for key in (
            "workdir",
            "pythonw",
            "script",
            "bridge_instance_id",
            "bridge_abi_version",
            "launch_mode",
            "process_creation_time",
        ):
            info.pop(key, None)
        for key in (
            "bridge_instance_id",
            "bridge_abi_version",
            "launch_mode",
            "process_creation_time",
            "token_script",
        ):
            runtime.pop(key, None)
        runtime["started_at"] = "2026-08-09T20:49:08+08:00"
        (workdir_path / "bridge.json").write_text(json.dumps(info), encoding="utf-8")
        (state_root / "bridge-runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
        return runtime, info, workdir_path

    def test_start_refuses_existing_listener_before_mutating_runtime_files(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, _state_root, workdir = self._make(directory)
            workdir.mkdir(parents=True)
            pid_file = workdir / "bridge.pid"
            info_file = workdir / "bridge.json"
            pid_file.write_text("14400", encoding="utf-8")
            info_file.write_text('{"pid": 14400}', encoding="utf-8")
            with (
                mock.patch.object(lifecycle, "_listener_pid", return_value=14400),
                mock.patch.object(lifecycle, "_discover_runtime", return_value=None),
                mock.patch.object(lifecycle, "_spawn_bridge") as spawn,
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store._restrict_to_current_user",
                ),
                self.assertRaises(ManagerError) as raised,
            ):
                lifecycle.start(str(workdir), port=1981)
            self.assertEqual(raised.exception.code, "port_busy")
            self.assertEqual(raised.exception.details["listener_pid"], 14400)
            self.assertEqual(pid_file.read_text(encoding="utf-8"), "14400")
            self.assertEqual(info_file.read_text(encoding="utf-8"), '{"pid": 14400}')
            spawn.assert_not_called()

    def test_discover_runtime_recovers_verified_listener_when_pid_file_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, _workdir = self._make(directory)
            workdir = state_root / "runtime"
            self._runtime(state_root, pid=14400, workdir=str(workdir))
            lifecycle.runtime_file.unlink()
            (workdir / "bridge.pid").write_text("4516", encoding="utf-8")
            with (
                mock.patch.object(lifecycle, "_listener_pid", return_value=14400),
                mock.patch.object(lifecycle, "_bridge_identity_matches", return_value=True),
            ):
                recovered = lifecycle._discover_runtime(1981)
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered["pid"], 14400)
            self.assertEqual((workdir / "bridge.pid").read_text(encoding="utf-8"), "14400")

    def test_health_probe_bypasses_proxy_and_reports_rejected_local_token(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, _state_root, workdir = self._make(directory)
            lifecycle.token_dir.mkdir(parents=True)
            (lifecycle.token_dir / "local-bridge-token.txt").write_text("test-token\n", encoding="utf-8")
            body = b'{"error":{"code":"local_bridge_token_invalid"}}'
            error = urllib.error.HTTPError(
                "http://127.0.0.1:1981/health",
                401,
                "Unauthorized",
                {},
                BytesIO(body),
            )
            opener = mock.Mock()
            opener.open.side_effect = error
            with mock.patch("urllib.request.build_opener", return_value=opener) as build:
                result = lifecycle._health_probe(1981, workdir)
            build.assert_called_once()
            self.assertIsInstance(build.call_args.args[0], urllib.request.ProxyHandler)
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertFalse(result["healthy"])
            self.assertEqual(result["http_status"], 401)
            self.assertEqual(result["error_code"], "local_bridge_token_invalid")

    def test_launch_reports_listener_pid_mismatch_instead_of_generic_unhealthy(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, _state_root, workdir = self._make(directory)
            with (
                mock.patch.object(lifecycle, "_listener_pid", side_effect=[None, None, 14400]),
                mock.patch.object(lifecycle, "_spawn_bridge"),
                mock.patch.object(lifecycle, "_wait_pid", return_value=4516),
                mock.patch.object(
                    lifecycle,
                    "_wait_bridge_info",
                    return_value={"pid": 4516, "port": 1981},
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.token_store._restrict_to_current_user",
                ),
                self.assertRaises(ManagerError) as raised,
            ):
                lifecycle.start(str(workdir), port=1981)
            self.assertEqual(raised.exception.code, "bridge_listener_pid_mismatch")
            self.assertEqual(raised.exception.details["pid"], 4516)
            self.assertEqual(raised.exception.details["listener_pid"], 14400)

    @patch_tools(alive=True, health=True, port_open=True)
    def test_start_launches_on_demand_process_and_records_runtime(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            wait_pid.return_value = 4242
            result = lifecycle.start(str(workdir), port=1981, auto_start=False)
            self.assertEqual(result["status"], "started")
            self.assertEqual(result["pid"], 4242)
            self.assertEqual(result["port"], 1981)
            self.assertFalse(
                any(
                    call.args
                    and call.args[0][0] == "schtasks"
                    and "/Create" in call.args[0]
                    for call in run.call_args_list
                )
            )
            runtime = json.loads(state_root.joinpath("bridge-runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(runtime["pid"], 4242)
            self.assertIsNone(runtime["task_name"])
            self.assertEqual(runtime["launch_mode"], "on_demand")
            self.assertEqual(runtime["token_version"], 1)
            self.assertEqual(runtime["token_generation"], 1)
            self.assertTrue(runtime["token_fingerprint"].startswith("sha256:"))

    @patch_tools(alive=True, health=True, port_open=True)
    def test_start_idempotent_when_already_running(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root)
            result = lifecycle.start(str(workdir))
            self.assertEqual(result["status"], "already_running")
            self.assertEqual(result["pid"], 1234)
            self.assertFalse(
                any(
                    c.args
                    and c.args[0][0] == "schtasks"
                    and "/Create" in c.args[0]
                    for c in run.call_args_list
                )
            )
            self.assertTrue(result["legacy_auto_start_removed"])

    @patch_tools(alive=True, health=False, port_open=True)
    def test_start_reports_unhealthy_without_restart(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root)
            result = lifecycle.start(str(workdir))
            self.assertEqual(result["status"], "unhealthy")
            self.assertTrue(result["identity_verified"])
            self.assertFalse(any(c.args and c.args[0][0] == "schtasks" for c in run.call_args_list))

    @patch_tools(alive=False, health=True, port_open=True)
    def test_start_recovers_stale_pid(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root)
            wait_pid.return_value = 7777
            result = lifecycle.start(str(workdir))
            self.assertEqual(result["status"], "started")
            self.assertEqual(result["pid"], 7777)

    @patch_tools(alive=False, health=True, port_open=True)
    def test_start_port_busy_raises(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            wait_pid.return_value = None
            port_open.return_value = True
            with self.assertRaises(ManagerError) as raised:
                lifecycle.start(str(workdir))
            self.assertEqual(raised.exception.code, "port_busy")

    @patch_tools(alive=True, health=True, port_open=True)
    def test_status_running(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root)
            result = lifecycle.status()
            self.assertEqual(result["status"], "running")
            self.assertEqual(result["pid"], 1234)
            self.assertEqual(result["base_url"], "http://127.0.0.1:1981/v1")
            self.assertIn("runtime_token_mismatch", result)

    @patch_tools(alive=True, health=False, port_open=True)
    def test_status_unhealthy(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root)
            self.assertEqual(lifecycle.status()["status"], "unhealthy")

    @patch_tools(alive=True, health=True, port_open=True)
    def test_status_recovers_missing_bridge_json_from_strong_runtime_identity(
        self, wait_pid, run, alive, health, port_open, task_exists
    ):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root, workdir=str(workdir))
            (workdir / "bridge.json").unlink()
            result = lifecycle.status()
            self.assertEqual(result["status"], "running")
            self.assertTrue(result["identity_verified"])

    @patch_tools(alive=True, health=True, port_open=True)
    def test_status_discovers_bridge_when_manager_metadata_is_missing(
        self, wait_pid, run, alive, health, port_open, task_exists
    ):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, _workdir = self._make(directory)
            managed_workdir = state_root / "runtime"
            self._runtime(state_root, workdir=str(managed_workdir))
            (state_root / "bridge-runtime.json").unlink()
            result = lifecycle.status()
            self.assertEqual(result["status"], "running")
            self.assertTrue(result["identity_verified"])
            self.assertTrue((state_root / "bridge-runtime.json").is_file())

    @patch_tools(alive=False, health=False, port_open=False)
    def test_status_stale(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root)
            result = lifecycle.status()
            self.assertEqual(result["status"], "stale")

    @patch_tools(alive=False, health=False, port_open=False)
    def test_status_not_started(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            result = lifecycle.status()
            self.assertEqual(result["status"], "not_started")

    @patch_tools(alive=True, health=True, port_open=True)
    def test_legacy_appdata_bridge_is_discovered_read_only(
        self, wait_pid, run, alive, health, port_open, task_exists
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "bridge"
            legacy = root / "appdata"
            legacy.mkdir(parents=True)
            token_dir = root / ".local"
            workdir = legacy / "bridge-runtime"
            workdir.mkdir(parents=True)
            self._runtime(legacy, pid=1234, port=1981, workdir=str(workdir))
            legacy_before = (legacy / "bridge-runtime.json").read_bytes()
            lifecycle = BridgeLifecycle(
                canonical,
                pythonw=PYTHONW,
                script=SCRIPT,
                token_dir=str(token_dir),
                legacy_state_root=legacy,
            )
            result = lifecycle.status(port=1981)
            self.assertEqual(result["status"], "running")
            self.assertEqual(result["runtime_source"], "legacy")
            self.assertFalse((canonical / "bridge-runtime.json").exists())
            self.assertEqual((legacy / "bridge-runtime.json").read_bytes(), legacy_before)

    @patch_tools(alive=lambda pid: pid == 2222, health=True, port_open=True)
    def test_stale_canonical_metadata_adopts_live_canonical_artifacts(
        self, wait_pid, run, alive, health, port_open, task_exists
    ):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, _workdir = self._make(directory)
            managed_workdir = state_root / "runtime"
            self._runtime(state_root, pid=2222, workdir=str(managed_workdir))
            stale = json.loads((state_root / "bridge-runtime.json").read_text(encoding="utf-8"))
            stale.update(
                {
                    "pid": 1111,
                    "port": 5781,
                    "workdir": str(state_root / "bridge-runtime-recovery-stale"),
                    "base_url": "http://127.0.0.1:5781/v1",
                }
            )
            (state_root / "bridge-runtime.json").write_text(json.dumps(stale), encoding="utf-8")

            result = lifecycle.status(port=1981)

            self.assertEqual(result["status"], "running")
            self.assertEqual(result["pid"], 2222)
            self.assertEqual(result["port"], 1981)
            persisted = json.loads((state_root / "bridge-runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["pid"], 2222)
            self.assertEqual(Path(persisted["workdir"]), managed_workdir.resolve())

    @patch_tools(alive=lambda pid: pid == 3333, health=True, port_open=True)
    def test_stale_canonical_metadata_falls_back_to_live_legacy_runtime(
        self, wait_pid, run, alive, health, port_open, task_exists
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "bridge"
            legacy = root / "appdata"
            legacy.mkdir(parents=True)
            token_dir = root / ".local"
            legacy_workdir = legacy / "bridge-runtime"
            self._runtime(legacy, pid=3333, port=1981, workdir=str(legacy_workdir))
            stale = json.loads((legacy / "bridge-runtime.json").read_text(encoding="utf-8"))
            stale.update(
                {
                    "pid": 1111,
                    "port": 5781,
                    "workdir": str(canonical / "bridge-runtime-recovery-stale"),
                    "base_url": "http://127.0.0.1:5781/v1",
                }
            )
            canonical.mkdir(parents=True)
            (canonical / "bridge-runtime.json").write_text(json.dumps(stale), encoding="utf-8")
            lifecycle = BridgeLifecycle(
                canonical,
                pythonw=PYTHONW,
                script=SCRIPT,
                token_dir=str(token_dir),
                legacy_state_root=legacy,
            )

            result = lifecycle.status(port=1981)

            self.assertEqual(result["status"], "running")
            self.assertEqual(result["pid"], 3333)
            self.assertEqual(result["runtime_source"], "legacy")
            persisted = json.loads((canonical / "bridge-runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["pid"], 1111)

    @patch_tools(alive=True, health=True, port_open=True)
    def test_start_adopts_legacy_bridge_and_writes_canonical_metadata(
        self, wait_pid, run, alive, health, port_open, task_exists
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "bridge"
            legacy = root / "appdata"
            legacy.mkdir(parents=True)
            token_dir = root / ".local"
            workdir = legacy / "bridge-runtime"
            workdir.mkdir(parents=True)
            self._runtime(legacy, pid=1234, port=1981, workdir=str(workdir))
            lifecycle = BridgeLifecycle(
                canonical,
                pythonw=PYTHONW,
                script=SCRIPT,
                token_dir=str(token_dir),
                legacy_state_root=legacy,
            )
            with mock.patch(
                "deepseek_subagent.bridges.opencode_go.lifecycle.ensure_token",
                return_value=(
                    "token",
                    {
                        "token_version": 1,
                        "token_generation": 1,
                        "token_fingerprint": "sha256:x",
                        "created_at": "2026-01-01T00:00:00",
                        "rotated_at": None,
                    },
                ),
            ):
                result = lifecycle.start(str(canonical / "runtime"), port=1981, auto_start=False)
            self.assertEqual(result["status"], "already_running")
            self.assertTrue((canonical / "bridge-runtime.json").is_file())
            runtime = json.loads((canonical / "bridge-runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(runtime["pid"], 1234)
            self.assertNotIn("_runtime_source", runtime)

    def test_replacement_recovery_dir_is_under_canonical_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "bridge"
            lifecycle = BridgeLifecycle(
                canonical,
                pythonw=PYTHONW,
                script=SCRIPT,
                token_dir=str(root / ".local"),
                legacy_state_root=root / "appdata",
            )
            with mock.patch.object(BridgeLifecycle, "_launch_bridge") as launch:
                launch.return_value = {
                    "status": "started",
                    "pid": 9999,
                    "port": 0,
                    "workdir": "ignored",
                }
                result = lifecycle.replace_unrecoverable(
                    {
                        "pid": 1111,
                        "status": "running",
                        "identity_verified": False,
                        "bridge_abi_version": 1,
                        "stop_status": "stop_identity_unverified",
                    }
                )
            self.assertEqual(result["status"], "replaced_unrecoverable")
            recovery_workdir = launch.call_args.args[0]
            self.assertTrue(str(Path(recovery_workdir).resolve()).startswith(str(canonical.resolve())))

    @patch_tools(alive=lambda *_: True, health=True, port_open=True)
    def test_stop_kills_managed_pid_only(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root)
            alive.side_effect = [True, True, False]
            result = lifecycle.stop()
            self.assertEqual(result["status"], "stopped")
            self.assertTrue(result["stopped"])
            calls = [c.args[0] if c.args else None for c in run.call_args_list]
            kill = next(c for c in calls if c and c[0] == "taskkill")
            self.assertIn("1234", kill)
            self.assertIn("/PID", kill)
            self.assertFalse(state_root.joinpath("bridge-runtime.json").exists())

    def test_stop_prefers_authenticated_self_shutdown_without_taskkill(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, _workdir = self._make(directory)
            self._runtime(state_root)
            with (
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.lifecycle.process_creation_time",
                    return_value=42,
                ),
                mock.patch.object(lifecycle, "_pid_alive", return_value=True),
                mock.patch.object(
                    lifecycle,
                    "_authenticated_shutdown",
                    return_value={"status": "control_shutdown_accepted", "accepted": True},
                ),
                mock.patch.object(lifecycle, "_wait_stopped", return_value=True),
                mock.patch.object(lifecycle, "_delete_task"),
                mock.patch("subprocess.run") as run,
            ):
                result = lifecycle.stop()
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["control_status"], "control_shutdown_accepted")
            self.assertFalse(
                any(call.args and call.args[0][0] == "taskkill" for call in run.call_args_list)
            )

    def test_stop_reports_stable_failure_when_control_and_taskkill_are_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, _workdir = self._make(directory)
            self._runtime(state_root)
            denied = mock.Mock(returncode=1, stdout="", stderr="Access is denied.")
            with (
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.lifecycle.process_creation_time",
                    return_value=42,
                ),
                mock.patch.object(lifecycle, "_pid_alive", return_value=True),
                mock.patch.object(
                    lifecycle,
                    "_authenticated_shutdown",
                    return_value={"status": "control_unavailable", "accepted": False},
                ),
                mock.patch.object(lifecycle, "_wait_stopped", return_value=False),
                mock.patch.object(lifecycle, "_end_task"),
                mock.patch("subprocess.run", return_value=denied),
            ):
                result = lifecycle.stop()
            self.assertEqual(result["status"], "stop_failed")
            self.assertEqual(result["control_status"], "control_unavailable")
            self.assertEqual(result["taskkill"]["returncode"], 1)
            self.assertIn("Access is denied", result["taskkill"]["stderr"])

    def test_unrecoverable_bridge_is_replaced_on_dynamic_port_without_termination(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, _state_root, _workdir = self._make(directory)
            launched = {
                "status": "started",
                "pid": 2222,
                "port": 2981,
                "workdir": str(Path(directory) / "replacement"),
                "provider_repair_required": True,
            }
            with mock.patch.object(lifecycle, "_launch_bridge", return_value=launched) as launch:
                result = lifecycle.replace_unrecoverable(
                    {
                        "status": "unhealthy",
                        "pid": 1111,
                        "identity_verified": True,
                        "bridge_abi_version": None,
                        "stop_status": "stop_failed",
                    }
                )
            self.assertEqual(result["status"], "replaced_unrecoverable")
            self.assertEqual(result["orphaned_pid"], 1111)
            self.assertFalse(result["manual_action_required"])
            self.assertEqual(launch.call_args.kwargs["port"], 0)

    @patch_tools(alive=False, health=False, port_open=False)
    def test_stop_not_running_cleans_tasks(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            result = lifecycle.stop()
            self.assertEqual(result["status"], "not_running")
            self.assertTrue(result["cleaned_tasks"])

    @patch_tools(alive=True, health=True, port_open=True)
    def test_restart_stops_then_starts(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root)
            alive.side_effect = [True, True, False]
            wait_pid.return_value = 9999
            result = lifecycle.restart(str(workdir))
            self.assertEqual(result["status"], "started")
            self.assertEqual(result["pid"], 9999)

    @patch_tools(alive=True, health=True, port_open=True)
    def test_restart_preserves_token_generation(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            token_dir = Path(directory) / ".local"
            token, state = __import__(
                "deepseek_subagent.bridges.opencode_go.token_store", fromlist=["ensure_token"]
            ).ensure_token(token_dir)
            self._runtime(state_root, workdir=str(workdir))
            alive.side_effect = [True, True, False]
            wait_pid.return_value = 9999
            lifecycle.restart(str(workdir))
            restored, restored_state = __import__(
                "deepseek_subagent.bridges.opencode_go.token_store", fromlist=["ensure_token"]
            ).ensure_token(token_dir)
            self.assertEqual(restored, token)
            self.assertEqual(restored_state["token_generation"], state["token_generation"])

    @patch_tools(alive=False, health=False, port_open=False, task_exists=False)
    def test_uninstall_cleanup_removes_tasks_and_runtime(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root)
            result = lifecycle.uninstall_cleanup()
            self.assertEqual(result["status"], "stopped")
            self.assertTrue(result["tasks_cleaned"])
            self.assertFalse(state_root.joinpath("bridge-runtime.json").exists())
            deleted = [
                c.args[0] for c in run.call_args_list if c.args and c.args[0][0] == "schtasks" and "/Delete" in c.args[0]
            ]
            names = [c[c.index("/TN") + 1] for c in deleted]
            self.assertIn(TASK_NAME, names)
            self.assertIn(AUTO_START_TASK_NAME, names)

    @patch_tools(alive=True, health=True, port_open=True)
    def test_auto_start_creates_onlogon_task(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            wait_pid.return_value = 5555
            result = lifecycle.start(str(workdir), auto_start=True)
            self.assertEqual(result["status"], "started")
            calls = [c.args[0] if c.args else None for c in run.call_args_list]
            onlogon = next(c for c in calls if c and "/SC" in c and "ONLOGON" in c)
            self.assertIn("/TN", onlogon)
            self.assertIn(AUTO_START_TASK_NAME, onlogon)

    @patch_tools(alive=True, health=True, port_open=True, task_exists=False)
    def test_running_bridge_repairs_missing_autostart_task(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root, workdir=str(workdir))
            result = lifecycle.start(str(workdir), auto_start=True)
            self.assertEqual(result["status"], "already_running")
            self.assertTrue(result["auto_start_repaired"])
            created = [
                call.args[0]
                for call in run.call_args_list
                if call.args and call.args[0][0] == "schtasks" and "/Create" in call.args[0]
            ]
            self.assertTrue(any(AUTO_START_TASK_NAME in command for command in created))

    @patch_tools(alive=True, health=True, port_open=True, task_exists=True)
    def test_running_bridge_repairs_stale_autostart_command(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root, workdir=str(workdir))
            with mock.patch.object(lifecycle, "_task_command_matches", return_value=False):
                result = lifecycle.start(str(workdir), auto_start=True)
            self.assertEqual(result["status"], "already_running")
            self.assertTrue(result["auto_start_repaired"])
            self.assertTrue(result["auto_start_task_valid"])
            created = [
                call.args[0]
                for call in run.call_args_list
                if call.args and call.args[0][0] == "schtasks" and "/Create" in call.args[0]
            ]
            self.assertTrue(any(AUTO_START_TASK_NAME in command for command in created))

    @patch_tools(alive=True, health=True, port_open=True, task_exists=True)
    def test_running_bridge_keeps_valid_autostart_command(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root, workdir=str(workdir))
            with mock.patch.object(lifecycle, "_task_command_matches", return_value=True):
                result = lifecycle.start(str(workdir), auto_start=True)
            self.assertEqual(result["status"], "already_running")
            self.assertFalse(result["auto_start_repaired"])
            self.assertTrue(result["auto_start_task_valid"])
            self.assertFalse(
                any(
                    call.args
                    and call.args[0][0] == "schtasks"
                    and "/Create" in call.args[0]
                    for call in run.call_args_list
                )
            )

    @patch_tools(alive=lambda pid: pid == 5678, health=True, port_open=True, task_exists=True)
    def test_status_adopts_onlogon_pid_after_machine_restart(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root, pid=1234, workdir=str(workdir))
            workdir.mkdir(parents=True, exist_ok=True)
            (workdir / "bridge.pid").write_text("5678", encoding="utf-8")
            (workdir / "bridge.json").write_text(
                json.dumps(
                    {
                        "pid": 5678,
                        "port": 1981,
                        "base_url": "http://127.0.0.1:1981/v1",
                        "workdir": str(workdir.resolve()),
                        "pythonw": str(Path(PYTHONW).resolve()),
                        "script": str(Path(SCRIPT).resolve()),
                        "bridge_instance_id": "reboot-instance",
                        "process_creation_time": 42,
                        "token_file": str((Path(directory) / ".local" / "local-bridge-token.txt").resolve()),
                        "token_script": str(
                            (
                                Path(SCRIPT).parents[2]
                                / "runtime"
                                / "scripts"
                                / "print_bridge_token.py"
                            ).resolve()
                        ),
                        "token_version": 1,
                        "token_generation": 1,
                        "token_fingerprint": "sha256:test",
                    }
                ),
                encoding="utf-8",
            )
            result = lifecycle.status()
            self.assertEqual(result["status"], "running")
            self.assertEqual(result["pid"], 5678)
            stored = json.loads((state_root / "bridge-runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["pid"], 5678)
            self.assertIn("recovered_at", stored)

    @patch_tools(alive=lambda pid: pid == 5678, health=True, port_open=True, task_exists=True)
    def test_stop_adopts_onlogon_pid_after_machine_restart(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root, pid=5678, workdir=str(workdir))
            runtime = json.loads((state_root / "bridge-runtime.json").read_text(encoding="utf-8"))
            runtime["pid"] = 1234
            (state_root / "bridge-runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
            alive.side_effect = [True, True, False]
            result = lifecycle.stop()
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["pid"], 5678)
            kill = next(
                call.args[0]
                for call in run.call_args_list
                if call.args and call.args[0][0] == "taskkill"
            )
            self.assertIn("5678", kill)
            self.assertNotIn("1234", kill)
            self.assertFalse((state_root / "bridge-runtime.json").exists())

    @patch_tools(alive=lambda pid: pid == 5678, health=True, port_open=True, task_exists=True)
    def test_restart_adopts_onlogon_pid_before_start(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root, pid=5678, workdir=str(workdir))
            runtime = json.loads((state_root / "bridge-runtime.json").read_text(encoding="utf-8"))
            runtime["pid"] = 1234
            (state_root / "bridge-runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
            alive.side_effect = [True, True, False]
            wait_pid.return_value = 9999
            result = lifecycle.restart(str(workdir), auto_start=True)
            self.assertEqual(result["status"], "started")
            self.assertEqual(result["pid"], 9999)
            kill = next(
                call.args[0]
                for call in run.call_args_list
                if call.args and call.args[0][0] == "taskkill"
            )
            self.assertIn("5678", kill)

    @patch_tools(alive=lambda pid: pid == 5678, health=True, port_open=True, task_exists=True)
    def test_rotate_token_adopts_onlogon_pid_before_start(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root, pid=5678, workdir=str(workdir))
            runtime = json.loads((state_root / "bridge-runtime.json").read_text(encoding="utf-8"))
            runtime["pid"] = 1234
            (state_root / "bridge-runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
            alive.side_effect = [True, True, False]
            wait_pid.return_value = 9999
            result = lifecycle.rotate_token(str(workdir), auto_start=True)
            self.assertEqual(result["status"], "token_rotated")
            self.assertEqual(result["pid"], 9999)
            self.assertGreaterEqual(result["token_generation"], 2)
            kill = next(
                call.args[0]
                for call in run.call_args_list
                if call.args and call.args[0][0] == "taskkill"
            )
            self.assertIn("5678", kill)

    @patch_tools(alive=True, health=False, port_open=True, task_exists=True)
    def test_stop_preserves_runtime_when_pid_identity_is_unverified(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root, workdir=str(workdir))
            with mock.patch.object(lifecycle, "_bridge_identity_matches", return_value=False):
                result = lifecycle.stop()
            self.assertEqual(result["status"], "stop_identity_unverified")
            self.assertFalse(result["stopped"])
            self.assertTrue((state_root / "bridge-runtime.json").exists())
            self.assertTrue((workdir / "bridge.pid").exists())
            self.assertFalse(
                any(call.args and call.args[0][0] == "taskkill" for call in run.call_args_list)
            )
            self.assertFalse(
                any(
                    call.args
                    and call.args[0][0] == "schtasks"
                    and "/Delete" in call.args[0]
                    for call in run.call_args_list
                )
            )

    @patch_tools(alive=True, health=False, port_open=True, task_exists=True)
    def test_restart_aborts_when_pid_identity_is_unverified(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root, workdir=str(workdir))
            with (
                mock.patch.object(lifecycle, "_bridge_identity_matches", return_value=False),
                self.assertRaises(ManagerError) as raised,
            ):
                lifecycle.restart(str(workdir))
            self.assertEqual(raised.exception.code, "bridge_stop_failed")
            self.assertTrue((state_root / "bridge-runtime.json").exists())
            self.assertFalse(
                any(
                    call.args
                    and call.args[0][0] == "schtasks"
                    and "/Create" in call.args[0]
                    for call in run.call_args_list
                )
            )

    def test_legacy_bridge_is_adopted_only_with_exact_live_process_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            runtime, info, workdir = self._make_legacy_runtime(
                state_root, workdir=str(workdir)
            )
            actual_creation = WINDOWS_EPOCH_FILETIME + int(
                __import__("datetime").datetime.fromisoformat(
                    "2026-08-09T12:49:06.925018+00:00"
                ).timestamp()
                * 10_000_000
            )
            expected = lifecycle._quoted_command(workdir, 1981, workdir / "bridge.pid")
            with (
                mock.patch.object(lifecycle, "_pid_alive", return_value=True),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.lifecycle.process_creation_time",
                    return_value=actual_creation,
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.lifecycle.process_image_path",
                    return_value=str(Path(PYTHONW).resolve()),
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.lifecycle.process_command_line",
                    return_value=expected,
                ),
                mock.patch.object(lifecycle, "_task_command_matches", return_value=False),
            ):
                self.assertTrue(lifecycle._bridge_identity_matches(runtime, info))

    def test_legacy_bridge_rejects_pid_reuse_even_when_paths_match(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            runtime, info, workdir = self._make_legacy_runtime(
                state_root, workdir=str(workdir)
            )
            reused_creation = WINDOWS_EPOCH_FILETIME + int(
                __import__("datetime").datetime.fromisoformat(
                    "2026-08-09T13:49:06+00:00"
                ).timestamp()
                * 10_000_000
            )
            expected = lifecycle._quoted_command(workdir, 1981, workdir / "bridge.pid")
            with (
                mock.patch.object(lifecycle, "_pid_alive", return_value=True),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.lifecycle.process_creation_time",
                    return_value=reused_creation,
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.lifecycle.process_image_path",
                    return_value=str(Path(PYTHONW).resolve()),
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.lifecycle.process_command_line",
                    return_value=expected,
                ),
                mock.patch.object(lifecycle, "_task_command_matches", return_value=False),
            ):
                self.assertFalse(lifecycle._bridge_identity_matches(runtime, info))

    def test_legacy_bridge_rejects_unrelated_python_process(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            runtime, info, _workdir = self._make_legacy_runtime(
                state_root, workdir=str(workdir)
            )
            actual_creation = WINDOWS_EPOCH_FILETIME + int(
                __import__("datetime").datetime.fromisoformat(
                    "2026-08-09T12:49:06+00:00"
                ).timestamp()
                * 10_000_000
            )
            with (
                mock.patch.object(lifecycle, "_pid_alive", return_value=True),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.lifecycle.process_creation_time",
                    return_value=actual_creation,
                ),
                mock.patch(
                    "deepseek_subagent.bridges.opencode_go.lifecycle.process_image_path",
                    return_value=r"C:\other\pythonw.exe",
                ),
                mock.patch.object(lifecycle, "_task_command_matches", return_value=False),
            ):
                self.assertFalse(lifecycle._bridge_identity_matches(runtime, info))

    def test_legacy_command_line_rejects_extra_or_retargeted_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, _state_root, workdir = self._make(directory)
            valid = lifecycle._quoted_command(workdir, 1981, workdir / "bridge.pid")
            self.assertTrue(lifecycle._command_line_matches_bridge(valid, workdir, 1981, workdir / "bridge.pid"))
            legacy_without_no_bytecode = valid.replace(" -B ", " ", 1)
            self.assertTrue(
                lifecycle._command_line_matches_bridge(
                    legacy_without_no_bytecode, workdir, 1981, workdir / "bridge.pid"
                )
            )
            self.assertFalse(
                lifecycle._command_line_matches_bridge(
                    valid + " --port 1982", workdir, 1981, workdir / "bridge.pid"
                )
            )
            self.assertFalse(
                lifecycle._command_line_matches_bridge(
                    valid.replace("--port 1981", "--port 1982"),
                    workdir,
                    1981,
                    workdir / "bridge.pid",
                )
            )

    def test_task_command_matches_schtasks_xml(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, _state_root, _workdir = self._make(directory)
            expected = '"C:\\venv\\Scripts\\pythonw.exe" "D:\\repo\\bridge.py" --port 1981'
            xml = (
                '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
                '<Actions><Exec><Command>C:\\venv\\Scripts\\pythonw.exe</Command>'
                '<Arguments>"D:\\repo\\bridge.py" --port 1981</Arguments>'
                '</Exec></Actions></Task>'
            )
            with mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout=xml, stderr=""),
            ):
                self.assertTrue(lifecycle._task_command_matches(AUTO_START_TASK_NAME, expected))

    def test_task_command_matches_utf16_schtasks_xml(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, _state_root, _workdir = self._make(directory)
            expected = '"C:\\venv\\Scripts\\pythonw.exe" "D:\\repo\\bridge.py" --port 1981'
            xml = (
                '<?xml version="1.0" encoding="UTF-16"?>'
                '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
                '<Actions><Exec><Command>C:\\venv\\Scripts\\pythonw.exe</Command>'
                '<Arguments>"D:\\repo\\bridge.py" --port 1981</Arguments>'
                '</Exec></Actions></Task>'
            ).encode("utf-16")
            with mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout=xml, stderr=b""),
            ):
                self.assertTrue(lifecycle._task_command_matches(AUTO_START_TASK_NAME, expected))

    @patch_tools(alive=True, health=True, port_open=True, task_exists=True)
    def test_on_demand_reuse_removes_legacy_autostart_without_moving_runtime(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, future_workdir = self._make(directory)
            old_workdir = Path(directory) / "old-work"
            self._runtime(state_root, pid=1234, workdir=str(old_workdir))
            reused = lifecycle.start(str(future_workdir), auto_start=False)
            self.assertEqual(reused["status"], "already_running")
            self.assertTrue(reused["legacy_auto_start_removed"])
            persisted = json.loads((state_root / "bridge-runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(persisted["workdir"]), old_workdir)
            self.assertIsNone(persisted["auto_start_task"])
            self.assertIsNone(persisted["auto_start_workdir"])


class StandalonePidFileTests(unittest.TestCase):
    def test_pid_file_written_and_removed(self):
        import subprocess as sp

        code = (
            "import sys, pathlib, time\n"
            "from pathlib import Path\n"
            "pid_file = Path(sys.argv[1])\n"
            "pid_file.write_text(str(__import__('os').getpid()), encoding='utf-8')\n"
            "time.sleep(0.2)\n"
            "pid_file.unlink()\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "bridge.pid"
            proc = sp.run(
                [sys.executable, "-c", code, str(pid_file)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertFalse(pid_file.exists())


if __name__ == "__main__":
    unittest.main()
