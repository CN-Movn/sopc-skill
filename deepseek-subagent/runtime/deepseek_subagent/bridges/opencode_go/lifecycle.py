"""Windows 用户级桥生命周期管理（Task Scheduler，无控制台窗口）。

原则：
- 桥只监听 127.0.0.1；进程由计划任务以 pythonw.exe 启动（无窗口、
  脱离当前终端）；OpenCode Go API Key 只存在于桥进程内存，不写入
  命令行、计划任务、日志或 manifest（计划任务命令行只含 pythonw、
  脚本、--workdir、--port、--pid-file，均为非敏感路径）。
- 运行时状态记录在 <state_root>/bridge-runtime.json：pid、port、
  workdir、started_at、任务名；pid 由桥进程写入 pid 文件后轮询获得。
- start 幂等：已运行且健康时不重复启动；残留 PID（进程已死）安全
  清理后重新启动。
- stop 只终止本项目记录的桥 PID（taskkill /PID <pid> /T 进程树），
  并删除本项目创建的计划任务。
- 异常退出：status 检测 PID 不存在 → stale，可安全 start 恢复；
  端口被其他进程占用 → 明确报 port_busy。
- 保留前台调试启动方式（bridge_standalone.py 不传 --pid-file 时行为
  不变）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from ...core.errors import ManagerError
from .token_store import TOKEN_FILE, describe_token, ensure_token, restore_token, rotate_token

TASK_NAME = "deepseek-subagent-bridge"
AUTO_START_TASK_NAME = "deepseek-subagent-bridge-autostart"
RUNTIME_FILE = "bridge-runtime.json"
HEALTH_TIMEOUT = 2.0
START_WAIT = 12.0
PID_WAIT = 15.0


def default_pythonw() -> str | None:
    if not sys.executable:
        return None
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else None


def short_path(path: str | Path) -> str:
    """转换为 Windows 8.3 短路径（schtasks /TR 有 261 字符限制）。"""
    import ctypes

    text = str(Path(path).resolve())
    buf = ctypes.create_unicode_buffer(512)
    length = ctypes.windll.kernel32.GetShortPathNameW(text, buf, 512)
    if length and length < 512:
        return buf.value
    return text


def default_workdir() -> str:
    return str(Path(os.environ.get("TEMP", ".")) / "deepseek-subagent-bridge-runtime")


class BridgeLifecycle:
    """管理本地兼容桥的后台运行与受管计划任务。"""

    def __init__(
        self,
        state_root: Path,
        pythonw: str | None = None,
        script: str | None = None,
        token_dir: str | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.runtime_file = self.state_root / RUNTIME_FILE
        if pythonw is None:
            pythonw = default_pythonw()
            if not pythonw or not Path(pythonw).is_file():
                raise ManagerError(
                    "pythonw_missing",
                    "没有找到 pythonw.exe（无窗口 Python）；请确认在虚拟环境中运行本命令。",
                )
        self.pythonw = str(Path(pythonw).resolve())
        script = script or _default_script()
        self.script = str(Path(script).resolve())
        skill_root = Path(self.script).parents[2]
        self.token_dir = Path(token_dir).resolve() if token_dir else skill_root / ".local"

    # ---------- 查询与健康 ----------

    def _read_runtime(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.runtime_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _write_runtime(self, payload: dict[str, Any]) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.runtime_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0 and f'"{pid}"' in (proc.stdout or "")

    def _health(self, port: int, workdir: Path) -> bool:
        token_file = self.token_dir / TOKEN_FILE
        token = ""
        if token_file.is_file():
            try:
                token = token_file.read_text(encoding="utf-8").strip()
            except OSError:
                token = ""
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/health",
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )
            with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT) as resp:
                return resp.status == 200
        except (OSError, urllib.error.URLError, ValueError):
            return False

    def _task_exists(self, task_name: str) -> bool:
        try:
            proc = subprocess.run(
                ["schtasks", "/Query", "/TN", task_name],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0

    # ---------- start ----------

    def start(
        self,
        workdir: str,
        port: int = 1981,
        auto_start: bool = False,
    ) -> dict[str, Any]:
        workdir_path = Path(workdir).expanduser().resolve()
        _token, token_state = ensure_token(self.token_dir, legacy_workdir=workdir_path)
        runtime = self._read_runtime()
        if runtime and self._pid_alive(runtime.get("pid")):
            if self._health(runtime.get("port") or port, workdir_path):
                return {
                    "status": "already_running",
                    "pid": runtime["pid"],
                    "port": runtime.get("port"),
                    "base_url": runtime.get("base_url"),
                    "started_at": runtime.get("started_at"),
                    **describe_token(self.token_dir),
                    "runtime_token_mismatch": self._runtime_token_mismatch(runtime, workdir_path),
                }
            return {
                "status": "unhealthy",
                "pid": runtime["pid"],
                "port": runtime.get("port"),
                "warning": "桥进程存在但 /v1/models 健康检查失败；请用 bridge stop 后重试。",
            }
        if runtime:
            self._clear_stale(runtime)
        pid_file = workdir_path / "bridge.pid"
        pid_file.unlink(missing_ok=True)
        workdir_path.mkdir(parents=True, exist_ok=True)

        self._delete_task(TASK_NAME)
        task_args: list[str] = [
            "schtasks",
            "/Create",
            "/TN",
            TASK_NAME,
            "/TR",
            self._quoted_command(workdir_path, port, pid_file),
            "/SC",
            "ONCE",
            "/ST",
            "23:59",
            "/F",
        ]
        self._run_task_admin(task_args)
        if auto_start:
            self._install_auto_start(workdir_path, port, pid_file)

        self._run_task_admin(["schtasks", "/Run", "/TN", TASK_NAME])

        pid = self._wait_pid(pid_file)
        if pid is None:
            busy = self._port_claimed_by_other(port)
            if busy:
                self._delete_task(TASK_NAME)
                raise ManagerError(
                    "port_busy",
                    f"端口 {port} 已被其他进程占用，桥无法启动；"
                    "请先停止占用进程或使用 --port 换端口。",
                )
            self._delete_task(TASK_NAME)
            raise ManagerError(
                "bridge_start_failed",
                "桥进程启动失败：未观察到受管 PID 与健康响应。",
            )
        if not self._wait_healthy(port, workdir_path):
            raise ManagerError(
                "bridge_unhealthy",
                "桥进程已启动（pid）但 /v1/models 健康检查未通过。",
                {"pid": pid},
            )

        payload = {
            "pid": pid,
            "port": port,
            "workdir": str(workdir_path),
            "base_url": f"http://127.0.0.1:{port}/v1",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "task_name": TASK_NAME,
            "auto_start_task": AUTO_START_TASK_NAME if auto_start else None,
            "pythonw": self.pythonw,
            "script": self.script,
            "token_file": str(self.token_dir / TOKEN_FILE),
            "token_version": token_state["token_version"],
            "token_generation": token_state["token_generation"],
            "token_fingerprint": token_state["token_fingerprint"],
        }
        self._write_runtime(payload)
        return {"status": "started", **payload}

    def _quoted_command(self, workdir: Path, port: int, pid_file: Path) -> str:
        parts = [f'"{short_path(self.pythonw)}"', f'"{short_path(self.script)}"']
        if str(workdir.resolve()) != default_workdir():
            parts += ["--workdir", f'"{short_path(workdir)}"']
        parts += ["--port", str(port)]
        if str(pid_file.resolve()) != str((workdir / "bridge.pid").resolve()):
            parts += ["--pid-file", f'"{short_path(pid_file)}"']
        return " ".join(parts)

    def _install_auto_start(self, workdir: Path, port: int, pid_file: Path) -> None:
        self._delete_task(AUTO_START_TASK_NAME)
        self._run_task_admin(
            [
                "schtasks",
                "/Create",
                "/TN",
                AUTO_START_TASK_NAME,
                "/TR",
                self._quoted_command(workdir, port, pid_file),
                "/SC",
                "ONLOGON",
                "/F",
            ]
        )

    def _run_task_admin(self, args: list[str]) -> None:
        try:
            proc = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ManagerError("task_scheduler_failed", f"计划任务命令失败：{exc}") from exc
        if proc.returncode != 0:
            raise ManagerError(
                "task_scheduler_failed",
                f"计划任务命令失败：{' '.join(args[1:4])}",
                {"stderr": (proc.stderr or "")[-400:]},
            )

    def _wait_pid(self, pid_file: Path) -> int | None:
        deadline = time.monotonic() + PID_WAIT
        while time.monotonic() < deadline:
            try:
                raw = pid_file.read_text(encoding="utf-8").strip()
                if raw.isdigit():
                    return int(raw)
            except OSError:
                pass
            time.sleep(0.5)
        return None

    def _wait_healthy(self, port: int, workdir: Path) -> bool:
        deadline = time.monotonic() + START_WAIT
        while time.monotonic() < deadline:
            if self._health(port, workdir):
                return True
            time.sleep(0.5)
        return False

    @staticmethod
    def _port_open(port: int) -> bool:
        import socket

        try:
            with socket.create_connection(("127.0.0.1", port), timeout=HEALTH_TIMEOUT):
                return True
        except OSError:
            return False

    def _port_claimed_by_other(self, port: int) -> bool:
        return self._port_open(port)

    def _clear_stale(self, runtime: dict[str, Any]) -> None:
        for pid_path in (
            Path(runtime.get("workdir") or default_workdir()) / "bridge.pid",
            self.state_root / "bridge.pid",
        ):
            pid_path.unlink(missing_ok=True)
        for task in (TASK_NAME, AUTO_START_TASK_NAME):
            self._delete_task(task)

    # ---------- status / stop / restart ----------

    def status(self, port: int = 1981) -> dict[str, Any]:
        runtime = self._read_runtime()
        if not runtime:
            return {"status": "not_started", "managed": False}
        pid = runtime.get("pid")
        alive = self._pid_alive(pid)
        healthy = alive and self._health(runtime.get("port") or port, Path(runtime["workdir"]))
        base = {
            "pid": pid,
            "port": runtime.get("port"),
            "base_url": runtime.get("base_url"),
            "started_at": runtime.get("started_at"),
            "workdir": runtime.get("workdir"),
            "task_name": runtime.get("task_name"),
            "auto_start_task": runtime.get("auto_start_task"),
            "managed": True,
            **describe_token(self.token_dir),
            "runtime_token_mismatch": self._runtime_token_mismatch(
                runtime, Path(runtime["workdir"])
            ),
        }
        if alive and healthy:
            return {"status": "running", **base}
        if alive:
            return {"status": "unhealthy", **base}
        return {"status": "stale", "warning": "记录中的 PID 已不存在；可安全执行 bridge start 恢复。", **base}

    def stop(self) -> dict[str, Any]:
        runtime = self._read_runtime()
        if not runtime:
            for task in (TASK_NAME, AUTO_START_TASK_NAME):
                if self._task_exists(task):
                    self._delete_task(task)
            return {"status": "not_running", "managed": False, "cleaned_tasks": True}
        pid = runtime.get("pid")
        stopped = False
        if self._pid_alive(pid):
            self._end_task(TASK_NAME)
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=15,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            stopped = not self._pid_alive(pid)
        for task in (TASK_NAME, AUTO_START_TASK_NAME):
            self._delete_task(task)
        for pid_path in (
            Path(runtime.get("workdir") or default_workdir()) / "bridge.pid",
            self.state_root / "bridge.pid",
        ):
            pid_path.unlink(missing_ok=True)
        self.runtime_file.unlink(missing_ok=True)
        return {"status": "stopped", "pid": pid, "stopped": stopped, "managed": True}

    def _end_task(self, task_name: str) -> None:
        try:
            subprocess.run(
                ["schtasks", "/End", "/TN", task_name],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def _delete_task(self, task_name: str) -> None:
        try:
            subprocess.run(
                ["schtasks", "/Delete", "/TN", task_name, "/F"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def restart(self, workdir: str, port: int = 1981, auto_start: bool = False) -> dict[str, Any]:
        workdir_path = Path(workdir).expanduser().resolve()
        token, token_state = ensure_token(self.token_dir, legacy_workdir=workdir_path)
        self.stop()
        # Restore exactly the same generation before starting the new process.
        restore_token(self.token_dir, token, token_state)
        return self.start(str(workdir_path), port=port, auto_start=auto_start)

    def rotate_token(self, workdir: str, port: int = 1981, auto_start: bool = False) -> dict[str, Any]:
        workdir_path = Path(workdir).expanduser().resolve()
        ensure_token(self.token_dir, legacy_workdir=workdir_path)
        self.stop()
        token_state = rotate_token(self.token_dir)
        started = self.start(str(workdir_path), port=port, auto_start=auto_start)
        return {
            **started,
            "status": "token_rotated",
            "token_version": token_state["token_version"],
            "token_generation": token_state["token_generation"],
            "token_fingerprint": token_state["token_fingerprint"],
            "codex_restart_required": True,
            "warning": "本地令牌已轮换；令牌正文未显示。必须完全重启 Codex 后再创建子 Agent。",
        }

    def _runtime_token_mismatch(self, runtime: dict[str, Any], workdir: Path) -> bool:
        current = describe_token(self.token_dir)
        expected = runtime.get("token_fingerprint")
        return bool(
            not current["token_state_consistent"]
            or (expected is not None and expected != current["token_fingerprint"])
        )

    def uninstall_cleanup(self) -> dict[str, Any]:
        """卸载清理：停止桥、删除受管计划任务与运行时记录（幂等）。"""
        result = self.stop()
        cleaned = True
        for task in (TASK_NAME, AUTO_START_TASK_NAME):
            if self._task_exists(task):
                cleaned = False
        return {**result, "tasks_cleaned": cleaned}


def _default_script() -> str:
    candidate = Path(__file__).resolve().parents[3] / "scripts" / "bridge_standalone.py"
    if candidate.is_file():
        return str(candidate)
    return str(Path(__file__).resolve().parents[2] / "bridge_standalone.py")
