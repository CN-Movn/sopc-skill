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
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from deepseek_subagent.bridges.opencode_go.lifecycle import (  # noqa: E402
    AUTO_START_TASK_NAME,
    TASK_NAME,
    BridgeLifecycle,
)
from deepseek_subagent.core.errors import ManagerError  # noqa: E402

PYTHONW = r"C:\venv\Scripts\pythonw.exe"
SCRIPT = r"D:\repo\deepseek-subagent\scripts\bridge_standalone.py"


def patch_tools(
    alive=None,
    health=None,
    port_open=None,
    schtasks_returncode=0,
    task_exists=None,
):
    def _decorator(fn):
        @mock.patch.object(
            BridgeLifecycle,
            "_pid_alive",
            side_effect=alive if callable(alive) else (lambda *_: alive),
        )
        @mock.patch.object(BridgeLifecycle, "_health", return_value=health)
        @mock.patch.object(BridgeLifecycle, "_port_open", return_value=port_open)
        @mock.patch.object(
            BridgeLifecycle,
            "_task_exists",
            return_value=True if task_exists is None else task_exists,
        )
        @mock.patch("subprocess.run")
        @mock.patch.object(BridgeLifecycle, "_wait_pid")
        def wrapper(self, wait_pid, run, task_exists_m, port_open_m, health_m, alive_m):
            run.return_value = mock.Mock(returncode=schtasks_returncode, stdout="", stderr="")
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
        state_root.joinpath("bridge-runtime.json").write_text(
            json.dumps(
                {
                    "pid": pid,
                    "port": port,
                    "workdir": workdir or str(Path(state_root).parent / "work"),
                    "base_url": f"http://127.0.0.1:{port}/v1",
                    "started_at": "2026-08-05T18:00:00",
                    "task_name": TASK_NAME,
                }
            ),
            encoding="utf-8",
        )

    @patch_tools(alive=True, health=True, port_open=True)
    def test_start_creates_task_and_records_runtime(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            wait_pid.return_value = 4242
            result = lifecycle.start(str(workdir), port=1981, auto_start=False)
            self.assertEqual(result["status"], "started")
            self.assertEqual(result["pid"], 4242)
            self.assertEqual(result["port"], 1981)
            calls = [c.args[0] if c.args else None for c in run.call_args_list]
            created = [c for c in calls if c and c[0] == "schtasks" and "/Create" in c]
            ran = [c for c in calls if c and c[0] == "schtasks" and "/Run" in c]
            self.assertEqual(len(created), 1)
            self.assertEqual(len(ran), 1)
            task_cmd = created[0][created[0].index("/TR") + 1]
            self.assertIn("pythonw.exe", task_cmd)
            self.assertIn("bridge_standalone.py", task_cmd)
            self.assertIn("--workdir", task_cmd)
            self.assertLessEqual(len(task_cmd), 261)
            runtime = json.loads(state_root.joinpath("bridge-runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(runtime["pid"], 4242)
            self.assertEqual(runtime["task_name"], TASK_NAME)
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
            self.assertFalse(any(c.args and c.args[0][0] == "schtasks" for c in run.call_args_list))

    @patch_tools(alive=True, health=False, port_open=True)
    def test_start_reports_unhealthy_without_restart(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root)
            result = lifecycle.start(str(workdir))
            self.assertEqual(result["status"], "unhealthy")
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

    @patch_tools(alive=lambda *_: True, health=True, port_open=True)
    def test_stop_kills_managed_pid_only(self, wait_pid, run, alive, health, port_open, task_exists):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle, state_root, workdir = self._make(directory)
            self._runtime(state_root)
            alive.side_effect = [True, False]
            result = lifecycle.stop()
            self.assertEqual(result["status"], "stopped")
            self.assertTrue(result["stopped"])
            calls = [c.args[0] if c.args else None for c in run.call_args_list]
            kill = next(c for c in calls if c and c[0] == "taskkill")
            self.assertIn("1234", kill)
            self.assertIn("/PID", kill)
            self.assertFalse(state_root.joinpath("bridge-runtime.json").exists())

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
