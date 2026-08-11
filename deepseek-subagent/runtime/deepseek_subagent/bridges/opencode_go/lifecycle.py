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
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ...core.errors import ManagerError
from ...core.lock import FileLock, LockTimeoutError
from .control import BRIDGE_ABI_VERSION, SHUTDOWN_PATH
from .token_store import TOKEN_FILE, describe_token, ensure_token, restore_token, rotate_token

TASK_NAME = "deepseek-subagent-bridge"
AUTO_START_TASK_NAME = "deepseek-subagent-bridge-autostart"
RUNTIME_FILE = "bridge-runtime.json"
HEALTH_TIMEOUT = 2.0
START_WAIT = 12.0
PID_WAIT = 15.0
TASK_QUERY_TIMEOUT = 3.0
CONTROL_TIMEOUT = 3.0
STOP_WAIT = 8.0
LIFECYCLE_LOCK_TIMEOUT = 20.0
LEGACY_START_TOLERANCE_SECONDS = 30.0
WINDOWS_EPOCH_FILETIME = 116444736000000000


def process_creation_time(pid: int) -> int | None:
    """Return the Windows process creation FILETIME without opening for write."""

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            )
            if not ok:
                return None
            return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def process_image_path(pid: int) -> str | None:
    """Return the executable path for a Windows PID without write access."""

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def process_command_line(pid: int) -> str | None:
    """Read a Windows process command line for legacy adoption.

    ``ProcessCommandLineInformation`` is queried first with read-only process
    access.  CIM is a compatibility fallback for older Python/Windows builds.
    Failure is represented as ``None`` so the caller leaves the PID untouched.
    """

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", ctypes.c_void_p),
            ]

        process_query_limited_information = 0x1000
        process_command_line_information = 60
        kernel32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ntdll.NtQueryInformationProcess.argtypes = [
            wintypes.HANDLE,
            wintypes.ULONG,
            wintypes.LPVOID,
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
        ]
        ntdll.NtQueryInformationProcess.restype = wintypes.LONG
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if handle:
            try:
                needed = wintypes.ULONG()
                ntdll.NtQueryInformationProcess(
                    handle,
                    process_command_line_information,
                    None,
                    0,
                    ctypes.byref(needed),
                )
                if needed.value >= ctypes.sizeof(UnicodeString):
                    buffer = ctypes.create_string_buffer(needed.value)
                    status = ntdll.NtQueryInformationProcess(
                        handle,
                        process_command_line_information,
                        buffer,
                        needed.value,
                        ctypes.byref(needed),
                    )
                    if status >= 0:
                        value = UnicodeString.from_buffer(buffer)
                        if value.Buffer and value.Length:
                            command_line = ctypes.wstring_at(value.Buffer, value.Length // 2).strip()
                            if command_line:
                                return command_line
            finally:
                kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        pass

    command = (
        "$p=Get-CimInstance Win32_Process -Filter 'ProcessId="
        f"{int(pid)}' -ErrorAction SilentlyContinue;"
        "if($p){[Console]::Out.Write($p.CommandLine)}"
    )
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=TASK_QUERY_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    value = (proc.stdout or "").strip()
    return value if proc.returncode == 0 and value else None


def windows_command_line_args(command_line: str) -> list[str] | None:
    """Parse a Windows command line with the platform's native argv rules."""

    if os.name != "nt" or not command_line:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        count = ctypes.c_int()
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32
        shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        argv = shell32.CommandLineToArgvW(command_line, ctypes.byref(count))
        if not argv:
            return None
        try:
            return [argv[index] for index in range(count.value)]
        finally:
            kernel32.LocalFree(argv)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


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
    """管理本地兼容桥的后台运行与受管计划任务。

    ``state_root`` 是 canonical bridge mutable-state root（正常运行时唯一写入
    位置，位于安装 Skill 的 ``.local\\bridge``）。``legacy_state_root`` 只用于
    只读发现旧 AppData bridge（其 runtime metadata 位于
    ``%LOCALAPPDATA%\\deepseek-subagent``），保证 upgrade 时能够安全停止旧
    bridge 并转换到 canonical root；legacy 位置从不写入。
    """

    def __init__(
        self,
        state_root: Path,
        pythonw: str | None = None,
        script: str | None = None,
        token_dir: str | None = None,
        legacy_state_root: str | Path | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.runtime_file = self.state_root / RUNTIME_FILE
        self.legacy_state_root = Path(legacy_state_root) if legacy_state_root is not None else None
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
        """Read canonical runtime metadata, falling back to legacy AppData read-only."""

        data = self._read_json(self.runtime_file)
        if data:
            data["_runtime_source"] = "local"
            return data
        if self.legacy_state_root is not None:
            legacy = self._read_json(Path(self.legacy_state_root) / RUNTIME_FILE)
            if legacy:
                legacy["_runtime_source"] = "legacy"
                return legacy
        return None

    def _discover_runtime(self, port: int) -> dict[str, Any] | None:
        """Recover manager metadata from a strongly identified live bridge."""

        workdir = self.state_root / "runtime"
        info = self._read_json(workdir / "bridge.json")
        file_pid: int | None = None
        try:
            pid_text = (workdir / "bridge.pid").read_text(encoding="utf-8").strip()
        except OSError:
            pid_text = ""
        if pid_text.isdigit():
            file_pid = int(pid_text)
        candidate_port = int(info.get("port") or port)
        listener_pid = self._listener_pid(candidate_port) if candidate_port > 0 else None
        info_pid = int(info.get("pid") or 0)
        if listener_pid is not None and info_pid == listener_pid:
            candidate_pid = listener_pid
        elif file_pid is not None:
            candidate_pid = file_pid
        else:
            return None
        candidate = {
            **info,
            "pid": candidate_pid,
            "port": candidate_port,
            "workdir": str(workdir.resolve()),
            "base_url": info.get("base_url") or f"http://127.0.0.1:{port}/v1",
            "task_name": TASK_NAME,
            "auto_start_task": AUTO_START_TASK_NAME,
            "auto_start_workdir": str(workdir.resolve()),
            "auto_start_port": int(info.get("port") or port),
            "pythonw": info.get("pythonw") or self.pythonw,
            "script": info.get("script") or self.script,
            "recovered_at": datetime.now().isoformat(timespec="seconds"),
        }
        if not self._bridge_identity_matches(candidate, info):
            return None
        if file_pid != candidate_pid:
            try:
                (workdir / "bridge.pid").write_text(str(candidate_pid), encoding="utf-8")
            except OSError:
                pass
        self._write_runtime(candidate)
        return candidate

    def _write_runtime(self, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload.pop("_runtime_source", None)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.runtime_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _adopt_autostart_pid(self, runtime: dict[str, Any]) -> dict[str, Any]:
        """Adopt the PID written by the ONLOGON bridge after a machine reboot.

        ``bridge_standalone.py`` owns ``bridge.pid`` and ``bridge.json``.  The
        manager-owned runtime file survives a reboot and therefore still names
        the old PID until this reconciliation runs.
        """

        current_workdir = Path(runtime.get("workdir") or default_workdir())
        candidates = [(current_workdir, int(runtime.get("port") or 1981))]
        auto_start_workdir = runtime.get("auto_start_workdir")
        if auto_start_workdir:
            future = Path(auto_start_workdir)
            if future.resolve() != current_workdir.resolve():
                candidates.append((future, int(runtime.get("auto_start_port") or runtime.get("port") or 1981)))

        for workdir, candidate_port in candidates:
            bridge_info = self._read_json(workdir / "bridge.json")
            try:
                pid_text = (workdir / "bridge.pid").read_text(encoding="utf-8").strip()
            except OSError:
                pid_text = ""
            if not pid_text.isdigit():
                continue
            candidate = int(pid_text)
            candidate_runtime = {
                **runtime,
                "pid": candidate,
                "port": candidate_port,
                "workdir": str(workdir.resolve()),
            }
            if not self._bridge_identity_matches(candidate_runtime, bridge_info):
                continue
            if candidate == runtime.get("pid") and workdir.resolve() == current_workdir.resolve():
                return runtime
            recovered = {
                **candidate_runtime,
                "base_url": bridge_info.get("base_url") or f"http://127.0.0.1:{candidate_port}/v1",
                "recovered_at": datetime.now().isoformat(timespec="seconds"),
            }
            for key in ("token_version", "token_generation", "token_fingerprint"):
                if bridge_info.get(key) is not None:
                    recovered[key] = bridge_info[key]
            self._write_runtime(recovered)
            return recovered
        return runtime

    def _bridge_identity_matches(
        self,
        runtime: dict[str, Any],
        bridge_info: dict[str, Any] | None = None,
    ) -> bool:
        workdir = Path(runtime.get("workdir") or default_workdir())
        info = bridge_info if bridge_info is not None else self._read_json(workdir / "bridge.json")
        pid = runtime.get("pid")
        port = int(runtime.get("port") or 1981)
        expected_token = os.path.normcase(str((self.token_dir / TOKEN_FILE).resolve()))
        expected_token_script = os.path.normcase(
            str((Path(self.script).parents[2] / "runtime" / "scripts" / "print_bridge_token.py").resolve())
        )
        record = info or runtime
        pid_file_matches = False
        try:
            pid_file_matches = (workdir / "bridge.pid").read_text(encoding="utf-8").strip() == str(pid)
        except OSError:
            pass
        recorded_script = record.get("script") or runtime.get("script")
        recorded_pythonw = record.get("pythonw") or runtime.get("pythonw")
        script_matches = recorded_script is None or os.path.normcase(str(recorded_script)) == os.path.normcase(self.script)
        pythonw_matches = recorded_pythonw is None or os.path.normcase(str(recorded_pythonw)) == os.path.normcase(self.pythonw)
        recorded_workdir = record.get("workdir") or runtime.get("workdir")
        workdir_matches = recorded_workdir is None or os.path.normcase(
            str(Path(recorded_workdir).resolve())
        ) == os.path.normcase(str(workdir.resolve()))
        basic = bool(
            pid
            and int(record.get("pid") or 0) == int(pid)
            and int(record.get("port") or 0) == port
            and os.path.normcase(str(record.get("token_file") or runtime.get("token_file") or ""))
            == expected_token
            and os.path.normcase(str(record.get("token_script") or runtime.get("token_script") or ""))
            == expected_token_script
            and script_matches
            and pythonw_matches
            and workdir_matches
            and self._pid_alive(pid)
        )
        if not basic:
            return False

        recorded_creation = record.get("process_creation_time") or runtime.get("process_creation_time")
        actual_creation = process_creation_time(int(pid))
        if recorded_creation is not None and actual_creation is not None:
            return bool(recorded_script and recorded_pythonw) and int(recorded_creation) == actual_creation

        # Legacy 1.5.x bridges do not have a process creation marker.  Require
        # either a complete live-process proof or both the PID artifact and an
        # exact managed Task Scheduler command.  Health is deliberately not
        # identity evidence.
        if not pid_file_matches:
            return False
        if self._legacy_live_process_identity_matches(
            runtime,
            info,
            int(pid),
            workdir,
            port,
            actual_creation,
        ):
            return True
        expected = self._quoted_command(workdir, port, workdir / "bridge.pid")
        return self._task_command_matches(TASK_NAME, expected) or self._task_command_matches(
            AUTO_START_TASK_NAME, expected
        )

    def _legacy_live_process_identity_matches(
        self,
        runtime: dict[str, Any],
        bridge_info: dict[str, Any],
        pid: int,
        workdir: Path,
        port: int,
        actual_creation: int | None,
    ) -> bool:
        """Strongly bind a pre-marker 1.5.x bridge to its live process.

        This path exists only for in-place upgrades.  It requires independent
        manager and bridge artifacts, the exact managed executable and command
        line, and a process creation time matching the manager's recorded
        start.  A stale PID file or an unrelated Python process cannot satisfy
        the full conjunction.
        """

        if not bridge_info or actual_creation is None:
            return False
        runtime_pythonw = runtime.get("pythonw")
        runtime_script = runtime.get("script")
        started_at = runtime.get("started_at")
        if not runtime_pythonw or not runtime_script or not started_at:
            return False
        if not self._same_path(runtime_pythonw, self.pythonw):
            return False
        if not self._same_path(runtime_script, self.script):
            return False

        image = process_image_path(pid)
        if not image or not self._same_path(image, self.pythonw):
            return False
        command_line = process_command_line(pid)
        if not command_line or not self._command_line_matches_bridge(
            command_line, workdir, port, workdir / "bridge.pid"
        ):
            return False

        try:
            recorded_start = datetime.fromisoformat(str(started_at))
            if recorded_start.tzinfo is None:
                recorded_start = recorded_start.astimezone()
            actual_seconds = (int(actual_creation) - WINDOWS_EPOCH_FILETIME) / 10_000_000
            actual_start = datetime.fromtimestamp(actual_seconds).astimezone()
        except (OverflowError, OSError, TypeError, ValueError):
            return False
        return abs((recorded_start - actual_start).total_seconds()) <= LEGACY_START_TOLERANCE_SECONDS

    def _command_line_matches_bridge(
        self,
        command_line: str,
        workdir: Path,
        port: int,
        pid_file: Path,
    ) -> bool:
        args = windows_command_line_args(command_line)
        if not args or len(args) < 4:
            return False
        script_index = 1
        if args[script_index].casefold() == "-b":
            script_index += 1
        if script_index >= len(args):
            return False
        if not self._same_path(args[0], self.pythonw) or not self._same_path(
            args[script_index], self.script
        ):
            return False

        values: dict[str, str] = {}
        index = script_index + 1
        allowed = {"--workdir", "--port", "--pid-file"}
        while index < len(args):
            option = args[index]
            if option not in allowed or option in values or index + 1 >= len(args):
                return False
            values[option] = args[index + 1]
            index += 2

        if "--port" not in values:
            return False
        try:
            if int(values["--port"]) != int(port):
                return False
        except (TypeError, ValueError):
            return False
        if "--workdir" in values:
            if not self._same_path(values["--workdir"], workdir):
                return False
        elif str(workdir.resolve()) != default_workdir():
            return False
        if "--pid-file" in values and not self._same_path(values["--pid-file"], pid_file):
            return False
        return True

    @staticmethod
    def _same_path(left: str | Path, right: str | Path) -> bool:
        """Compare Windows long/8.3 aliases by file identity when possible."""

        try:
            return os.path.samefile(left, right)
        except (OSError, TypeError, ValueError):
            return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
                str(Path(right).resolve())
            )

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid:
            return False
        if os.name == "nt":
            try:
                import ctypes

                process_query_limited_information = 0x1000
                still_active = 259
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
                if handle:
                    try:
                        exit_code = ctypes.c_ulong()
                        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                            return exit_code.value == still_active
                    finally:
                        kernel32.CloseHandle(handle)
                elif ctypes.get_last_error() == 5:
                    # Access denied still proves that the PID exists.
                    return True
            except (AttributeError, OSError, ValueError):
                pass
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=TASK_QUERY_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0 and f'"{pid}"' in (proc.stdout or "")

    @staticmethod
    def _localhost_opener():
        """Return an opener that cannot route localhost control traffic via a proxy."""

        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _health_probe(self, port: int, workdir: Path) -> dict[str, Any]:
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
            with self._localhost_opener().open(req, timeout=HEALTH_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
                try:
                    body = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    body = {}
                return {
                    "healthy": resp.status == 200,
                    "http_status": int(resp.status),
                    "error_code": None if resp.status == 200 else "bridge_health_invalid",
                    "bridge_abi_version": body.get("bridge_abi_version"),
                    "bridge_instance_id": body.get("bridge_instance_id"),
                }
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8", "replace"))
            except (OSError, ValueError, json.JSONDecodeError):
                body = {}
            error = body.get("error") if isinstance(body, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
            return {
                "healthy": False,
                "http_status": int(exc.code),
                "error_code": str(code or "bridge_health_rejected"),
            }
        except (OSError, urllib.error.URLError, ValueError):
            return {
                "healthy": False,
                "http_status": 0,
                "error_code": "bridge_unreachable",
            }

    def _health(self, port: int, workdir: Path) -> bool:
        return self._health_probe(port, workdir).get("healthy") is True

    def _bridge_abi_version(self, runtime: dict[str, Any]) -> int | None:
        workdir = Path(runtime.get("workdir") or default_workdir())
        info = self._read_json(workdir / "bridge.json")
        value = info.get("bridge_abi_version", runtime.get("bridge_abi_version"))
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _bridge_abi_compatible(self, runtime: dict[str, Any]) -> bool:
        return self._bridge_abi_version(runtime) == BRIDGE_ABI_VERSION

    def _authenticated_shutdown(self, runtime: dict[str, Any]) -> dict[str, Any]:
        """Ask a compatible managed bridge to stop itself without OS process rights."""

        abi_version = self._bridge_abi_version(runtime)
        if abi_version != BRIDGE_ABI_VERSION:
            return {
                "status": "control_unsupported",
                "bridge_abi_version": abi_version,
            }
        workdir = Path(runtime.get("workdir") or default_workdir())
        info = self._read_json(workdir / "bridge.json")
        instance_id = info.get("bridge_instance_id") or runtime.get("bridge_instance_id")
        if not instance_id:
            return {"status": "control_identity_missing"}
        token_file = self.token_dir / TOKEN_FILE
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError:
            return {"status": "control_token_unavailable"}
        if not token:
            return {"status": "control_token_unavailable"}
        port = int(runtime.get("port") or info.get("port") or 1981)
        body = json.dumps({"bridge_instance_id": instance_id}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{SHUTDOWN_PATH}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._localhost_opener().open(request, timeout=CONTROL_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
                accepted = (
                    response.status == 202
                    and payload.get("status") == "shutdown_accepted"
                    and payload.get("bridge_instance_id") == instance_id
                )
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            accepted = False
        return {
            "status": "control_shutdown_accepted" if accepted else "control_unavailable",
            "accepted": accepted,
            "bridge_abi_version": abi_version,
        }

    def _wait_stopped(self, pid: int | None) -> bool:
        deadline = time.monotonic() + STOP_WAIT
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                return True
            time.sleep(0.2)
        return not self._pid_alive(pid)

    def _spawn_bridge(self, workdir: Path, port: int, pid_file: Path) -> None:
        command = [
            self.pythonw,
            "-B",
            self.script,
            "--workdir",
            str(workdir),
            "--port",
            str(port),
            "--pid-file",
            str(pid_file),
        ]
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise ManagerError("bridge_start_failed", f"Bridge process could not be launched: {exc}") from exc

    def _wait_bridge_info(self, workdir: Path, pid: int, requested_port: int) -> dict[str, Any]:
        deadline = time.monotonic() + START_WAIT
        while time.monotonic() < deadline:
            info = self._read_json(workdir / "bridge.json")
            if int(info.get("pid") or 0) == int(pid) and int(info.get("port") or 0) > 0:
                return info
            time.sleep(0.2)
        if requested_port > 0:
            return {"pid": pid, "port": requested_port}
        return {}

    def _task_exists(self, task_name: str) -> bool:
        try:
            proc = subprocess.run(
                ["schtasks", "/Query", "/TN", task_name],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=TASK_QUERY_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0

    @staticmethod
    def _normalize_task_command(value: str) -> str:
        return re.sub(r"\s+", " ", value.replace('"', "")).strip().casefold()

    def _task_command_matches(self, task_name: str, expected: str) -> bool:
        try:
            proc = subprocess.run(
                ["schtasks", "/Query", "/TN", task_name, "/XML"],
                capture_output=True,
                timeout=TASK_QUERY_TIMEOUT,
            )
            if proc.returncode != 0:
                return False
            xml_payload: bytes | str = proc.stdout or b""
            if isinstance(xml_payload, str):
                xml_payload = xml_payload.lstrip("\ufeff")
                xml_payload = re.sub(r"^\s*<\?xml[^>]*\?>", "", xml_payload, count=1)
            root = ElementTree.fromstring(xml_payload)
            command = root.find(".//{*}Exec/{*}Command")
            arguments = root.find(".//{*}Exec/{*}Arguments")
        except (OSError, subprocess.SubprocessError, ElementTree.ParseError, UnicodeError, ValueError):
            return False
        actual = " ".join(
            part for part in (
                command.text if command is not None else "",
                arguments.text if arguments is not None else "",
            ) if part
        )
        return self._normalize_task_command(actual) == self._normalize_task_command(expected)

    # ---------- start ----------

    def start(
        self,
        workdir: str,
        port: int = 1981,
        auto_start: bool = False,
    ) -> dict[str, Any]:
        workdir_path = Path(workdir).expanduser().resolve()
        ensure_token(self.token_dir, legacy_workdir=workdir_path)
        runtime = self._read_runtime()
        if runtime is None:
            runtime = self._discover_runtime(port)
        if runtime:
            runtime = self._adopt_autostart_pid(runtime)
        runtime_alive = bool(runtime and self._pid_alive(runtime.get("pid")))
        if runtime and not runtime_alive:
            discovered = self._discover_runtime(port)
            if discovered:
                discovered["_runtime_source"] = "local"
                runtime = discovered
                runtime_alive = self._pid_alive(runtime.get("pid"))
        if runtime and not runtime_alive and self.legacy_state_root is not None:
            legacy = self._read_json(Path(self.legacy_state_root) / RUNTIME_FILE)
            if legacy and self._pid_alive(legacy.get("pid")):
                legacy["_runtime_source"] = "legacy"
                runtime = self._adopt_autostart_pid(legacy)
                runtime_alive = True
        if runtime and runtime_alive:
            identity_verified = self._bridge_identity_matches(runtime)
            runtime_port = int(runtime.get("port") or port)
            health = self._health_probe(runtime_port, Path(runtime["workdir"]))
            healthy = health.get("healthy") is True
            listener_pid = self._listener_pid(runtime_port)
            listener_matches = listener_pid is None or listener_pid == runtime.get("pid")
            abi_version = self._bridge_abi_version(runtime)
            abi_compatible = abi_version == BRIDGE_ABI_VERSION
            if healthy and identity_verified and abi_compatible and listener_matches:
                legacy_auto_start_removed = False
                auto_start_repaired = False
                auto_start_valid: bool | None = None
                if auto_start:
                    expected = self._quoted_command(
                        Path(runtime["workdir"]),
                        int(runtime.get("port") or port),
                        Path(runtime["workdir"]) / "bridge.pid",
                    )
                    if not self._task_command_matches(AUTO_START_TASK_NAME, expected):
                        self._install_auto_start(
                            Path(runtime["workdir"]),
                            int(runtime.get("port") or port),
                            Path(runtime["workdir"]) / "bridge.pid",
                        )
                        auto_start_repaired = True
                    auto_start_valid = True
                elif self._task_exists(AUTO_START_TASK_NAME):
                    self._delete_task(AUTO_START_TASK_NAME)
                    legacy_auto_start_removed = True
                info = self._read_json(Path(runtime["workdir"]) / "bridge.json")
                for key in (
                    "bridge_instance_id",
                    "bridge_abi_version",
                    "launch_mode",
                    "token_version",
                    "token_generation",
                    "token_fingerprint",
                ):
                    if info.get(key) is not None:
                        runtime[key] = info[key]
                runtime["auto_start_task"] = AUTO_START_TASK_NAME if auto_start else None
                runtime["auto_start_workdir"] = str(Path(runtime["workdir"]).resolve()) if auto_start else None
                runtime["auto_start_port"] = int(runtime.get("port") or port) if auto_start else None
                self._write_runtime(runtime)
                return {
                    "status": "already_running",
                    "pid": runtime["pid"],
                    "port": runtime.get("port"),
                    "base_url": runtime.get("base_url"),
                    "started_at": runtime.get("started_at"),
                    "auto_start_task": runtime.get("auto_start_task"),
                    "auto_start_task_present": self._task_exists(AUTO_START_TASK_NAME),
                    "auto_start_task_valid": auto_start_valid,
                    "auto_start_repaired": auto_start_repaired,
                    "legacy_auto_start_removed": legacy_auto_start_removed,
                    "workdir": runtime.get("workdir"),
                    "identity_verified": True,
                    "bridge_abi_version": abi_version,
                    "bridge_abi_compatible": True,
                    "launch_mode": runtime.get("launch_mode") or "legacy_task_scheduler",
                    **describe_token(self.token_dir),
                    "runtime_token_mismatch": self._runtime_token_mismatch(runtime, workdir_path),
                    "listener_pid": listener_pid,
                    "listener_pid_matches_runtime": listener_pid == runtime.get("pid") if listener_pid is not None else None,
                }
            return {
                "status": "unhealthy" if not healthy else "incompatible",
                "pid": runtime["pid"],
                "port": runtime.get("port"),
                "workdir": runtime.get("workdir"),
                "identity_verified": identity_verified,
                "bridge_abi_version": abi_version,
                "bridge_abi_compatible": abi_compatible,
                "listener_pid": listener_pid,
                "listener_pid_matches_runtime": listener_pid == runtime.get("pid") if listener_pid is not None else None,
                "health_http_status": health.get("http_status"),
                "health_error_code": health.get("error_code"),
                "warning": (
                    "The recorded bridge process is unhealthy, incompatible, or its managed identity "
                    "cannot be verified; recovery will preserve unverified processes."
                ),
            }
        if runtime:
            self._clear_stale(runtime)
        return self._launch_bridge(workdir_path, port=port, auto_start=auto_start)

    def _launch_bridge(
        self,
        workdir_path: Path,
        port: int,
        auto_start: bool = False,
        previous_unrecoverable: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.state_root.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(
                self.state_root / "bridge-lifecycle.lock",
                timeout=LIFECYCLE_LOCK_TIMEOUT,
            ):
                return self._launch_bridge_locked(
                    workdir_path,
                    port,
                    auto_start=auto_start,
                    previous_unrecoverable=previous_unrecoverable,
                )
        except LockTimeoutError as exc:
            raise ManagerError(
                "bridge_operation_in_progress",
                "Another managed bridge startup is still in progress; retry prepare once.",
            ) from exc

    def _launch_bridge_locked(
        self,
        workdir_path: Path,
        port: int,
        auto_start: bool = False,
        previous_unrecoverable: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _token, token_state = ensure_token(self.token_dir, legacy_workdir=workdir_path)
        workdir_path = workdir_path.expanduser().resolve()
        pid_file = workdir_path / "bridge.pid"
        if port > 0:
            listener_pid = self._listener_pid(port)
            if listener_pid is not None:
                raise ManagerError(
                    "port_busy",
                    f"端口 {port} 已被 PID {listener_pid} 占用，桥未启动；现有运行时证据保持不变。",
                    {"port": port, "listener_pid": listener_pid},
                )
        pid_file.unlink(missing_ok=True)
        workdir_path.mkdir(parents=True, exist_ok=True)
        (workdir_path / "bridge.json").unlink(missing_ok=True)
        for task in (TASK_NAME, AUTO_START_TASK_NAME):
            self._delete_task(task)
        self._spawn_bridge(workdir_path, port, pid_file)
        if auto_start:
            self._install_auto_start(workdir_path, port, pid_file)
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
        bridge_info = self._wait_bridge_info(workdir_path, pid, port)
        actual_port = int(bridge_info.get("port") or port)
        if actual_port <= 0:
            raise ManagerError(
                "bridge_start_failed",
                "Bridge started but did not publish a usable localhost port.",
                {"pid": pid},
            )
        listener_pid = self._listener_pid(actual_port)
        if listener_pid is not None and listener_pid != pid:
            raise ManagerError(
                "bridge_listener_pid_mismatch",
                "桥进程写入的 PID 与实际 localhost 监听进程不一致；未终止任何未验证进程。",
                {"pid": pid, "listener_pid": listener_pid, "port": actual_port},
            )
        health = self._wait_health_probe(actual_port, workdir_path)
        if not health.get("healthy"):
            error_code = str(health.get("error_code") or "bridge_unhealthy")
            raise ManagerError(
                error_code,
                "桥进程已启动，但受保护的 /health 健康检查未通过。",
                {
                    "pid": pid,
                    "listener_pid": listener_pid,
                    "port": actual_port,
                    "http_status": health.get("http_status"),
                    "health_error_code": error_code,
                },
            )
        bridge_info = self._read_json(workdir_path / "bridge.json") or bridge_info
        payload = {
            "pid": pid,
            "port": actual_port,
            "workdir": str(workdir_path),
            "base_url": bridge_info.get("base_url") or f"http://127.0.0.1:{actual_port}/v1",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "task_name": None,
            "auto_start_task": AUTO_START_TASK_NAME if auto_start else None,
            "auto_start_workdir": str(workdir_path) if auto_start else None,
            "auto_start_port": actual_port if auto_start else None,
            "pythonw": self.pythonw,
            "script": self.script,
            "token_file": str(self.token_dir / TOKEN_FILE),
            "token_script": bridge_info.get("token_script"),
            "bridge_instance_id": bridge_info.get("bridge_instance_id"),
            "bridge_abi_version": bridge_info.get("bridge_abi_version"),
            "launch_mode": "on_demand",
            "process_creation_time": bridge_info.get("process_creation_time") or process_creation_time(pid),
            "token_version": token_state["token_version"],
            "token_generation": token_state["token_generation"],
            "token_fingerprint": token_state["token_fingerprint"],
        }
        if previous_unrecoverable:
            payload["previous_unrecoverable"] = previous_unrecoverable
        self._write_runtime(payload)
        return {
            "status": "started",
            **payload,
            "bridge_abi_compatible": payload.get("bridge_abi_version") == BRIDGE_ABI_VERSION,
            "provider_repair_required": port == 0 or actual_port != port,
        }

    def replace_unrecoverable(
        self,
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        """Launch one isolated replacement without terminating the inaccessible PID."""

        recovery_workdir = self.state_root / f"bridge-runtime-recovery-{time.time_ns()}"
        evidence = {
            "pid": previous.get("pid"),
            "status": previous.get("status"),
            "identity_verified": previous.get("identity_verified"),
            "bridge_abi_version": previous.get("bridge_abi_version"),
            "stop_status": previous.get("stop_status"),
        }
        try:
            result = self._launch_bridge(
                recovery_workdir,
                port=0,
                auto_start=False,
                previous_unrecoverable=evidence,
            )
        except ManagerError as exc:
            raise ManagerError(
                "bridge_process_unrecoverable",
                "The old bridge could not be stopped and an isolated on-demand replacement could not be started.",
                {
                    **evidence,
                    "replacement_error": exc.code,
                    "manual_action": "End only the reported verified bridge PID, then run prepare once.",
                },
            ) from exc
        return {
            **result,
            "status": "replaced_unrecoverable",
            "orphaned_pid": previous.get("pid"),
            "provider_repair_required": True,
            "manual_action_required": False,
        }

    def _quoted_command(self, workdir: Path, port: int, pid_file: Path) -> str:
        parts = [f'"{short_path(self.pythonw)}"', "-B", f'"{short_path(self.script)}"']
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

    def _wait_health_probe(self, port: int, workdir: Path) -> dict[str, Any]:
        deadline = time.monotonic() + START_WAIT
        latest = {"healthy": False, "http_status": 0, "error_code": "bridge_unreachable"}
        while time.monotonic() < deadline:
            latest = self._health_probe(port, workdir)
            if latest.get("healthy"):
                return latest
            if latest.get("error_code") == "local_bridge_token_invalid":
                return latest
            time.sleep(0.5)
        return latest

    def _wait_healthy(self, port: int, workdir: Path) -> bool:
        return self._wait_health_probe(port, workdir).get("healthy") is True

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

    @staticmethod
    def _listener_pid(port: int) -> int | None:
        """Return the IPv4 loopback listener PID using the Windows TCP owner table."""

        if os.name != "nt" or int(port) <= 0:
            return None
        try:
            import ctypes
            import socket
            from ctypes import wintypes

            af_inet = 2
            tcp_table_owner_pid_listener = 3
            insufficient_buffer = 122

            class MibTcpRowOwnerPid(ctypes.Structure):
                _fields_ = [
                    ("dwState", wintypes.DWORD),
                    ("dwLocalAddr", wintypes.DWORD),
                    ("dwLocalPort", wintypes.DWORD),
                    ("dwRemoteAddr", wintypes.DWORD),
                    ("dwRemotePort", wintypes.DWORD),
                    ("dwOwningPid", wintypes.DWORD),
                ]

            size = wintypes.ULONG(0)
            api = ctypes.windll.iphlpapi.GetExtendedTcpTable
            api.argtypes = [
                wintypes.LPVOID,
                ctypes.POINTER(wintypes.ULONG),
                wintypes.BOOL,
                wintypes.ULONG,
                wintypes.ULONG,
                wintypes.ULONG,
            ]
            api.restype = wintypes.DWORD
            result = api(None, ctypes.byref(size), False, af_inet, tcp_table_owner_pid_listener, 0)
            if result != insufficient_buffer or size.value <= ctypes.sizeof(wintypes.DWORD):
                return None
            buffer = ctypes.create_string_buffer(size.value)
            result = api(buffer, ctypes.byref(size), False, af_inet, tcp_table_owner_pid_listener, 0)
            if result != 0:
                return None
            count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
            offset = ctypes.sizeof(wintypes.DWORD)
            row_size = ctypes.sizeof(MibTcpRowOwnerPid)
            for index in range(count):
                row = MibTcpRowOwnerPid.from_buffer_copy(buffer, offset + index * row_size)
                local_port = socket.ntohs(int(row.dwLocalPort) & 0xFFFF)
                local_addr = socket.inet_ntoa(int(row.dwLocalAddr).to_bytes(4, "little"))
                if local_port == int(port) and local_addr == "127.0.0.1":
                    return int(row.dwOwningPid)
        except (AttributeError, OSError, TypeError, ValueError):
            return None
        return None

    def _clear_stale(self, runtime: dict[str, Any]) -> None:
        for pid_path in (
            Path(runtime.get("workdir") or default_workdir()) / "bridge.pid",
            self.state_root / "bridge.pid",
        ):
            try:
                pid_path.unlink(missing_ok=True)
            except OSError:
                # Legacy AppData workdirs may not be writable by the sandbox;
                # inability to clean history is not an error.
                pass
        for task in (TASK_NAME, AUTO_START_TASK_NAME):
            self._delete_task(task)

    # ---------- status / stop / restart ----------

    def status(self, port: int = 1981) -> dict[str, Any]:
        runtime = self._read_runtime()
        if not runtime:
            runtime = self._discover_runtime(port)
        if not runtime:
            return {"status": "not_started", "managed": False}
        runtime = self._adopt_autostart_pid(runtime)
        pid = runtime.get("pid")
        alive = self._pid_alive(pid)
        if not alive:
            discovered = self._discover_runtime(port)
            if discovered:
                discovered["_runtime_source"] = "local"
                runtime = discovered
                pid = runtime.get("pid")
                alive = self._pid_alive(pid)
        if not alive and self.legacy_state_root is not None:
            legacy = self._read_json(Path(self.legacy_state_root) / RUNTIME_FILE)
            if legacy and self._pid_alive(legacy.get("pid")):
                legacy["_runtime_source"] = "legacy"
                runtime = self._adopt_autostart_pid(legacy)
                pid = runtime.get("pid")
                alive = True
        runtime_port = int(runtime.get("port") or port)
        health = (
            self._health_probe(runtime_port, Path(runtime["workdir"]))
            if alive
            else {"healthy": False, "http_status": 0, "error_code": "bridge_not_running"}
        )
        healthy = health.get("healthy") is True
        listener_pid = self._listener_pid(runtime_port)
        workdir = Path(runtime["workdir"])
        auto_start_workdir = Path(runtime.get("auto_start_workdir") or workdir)
        auto_start_port = int(runtime.get("auto_start_port") or runtime.get("port") or port)
        expected_auto_start = self._quoted_command(
            auto_start_workdir,
            auto_start_port,
            auto_start_workdir / "bridge.pid",
        )
        auto_start_present = self._task_exists(AUTO_START_TASK_NAME)
        auto_start_valid = auto_start_present and self._task_command_matches(
            AUTO_START_TASK_NAME, expected_auto_start
        )
        base = {
            "pid": pid,
            "port": runtime.get("port"),
            "base_url": runtime.get("base_url"),
            "started_at": runtime.get("started_at"),
            "workdir": runtime.get("workdir"),
            "task_name": runtime.get("task_name"),
            "auto_start_task": runtime.get("auto_start_task"),
            "task_present": self._task_exists(TASK_NAME),
            "auto_start_task_present": auto_start_present,
            "auto_start_task_valid": auto_start_valid,
            "managed": True,
            **describe_token(self.token_dir),
            "runtime_token_mismatch": self._runtime_token_mismatch(
                runtime, Path(runtime["workdir"])
            ),
            "identity_verified": self._bridge_identity_matches(runtime),
            "bridge_abi_version": self._bridge_abi_version(runtime),
            "bridge_abi_compatible": self._bridge_abi_compatible(runtime),
            "launch_mode": runtime.get("launch_mode") or "legacy_task_scheduler",
            "runtime_source": runtime.get("_runtime_source") or "local",
            "listener_pid": listener_pid,
            "listener_pid_matches_runtime": listener_pid == pid if listener_pid is not None else None,
            "health_http_status": health.get("http_status"),
            "health_error_code": health.get("error_code"),
        }
        if alive and healthy and (listener_pid is None or listener_pid == pid):
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
        runtime = self._adopt_autostart_pid(runtime)
        pid = runtime.get("pid")
        stopped = False
        identity_verified = self._bridge_identity_matches(runtime)
        if not identity_verified and self._pid_alive(pid):
            return {
                "status": "stop_identity_unverified",
                "pid": pid,
                "stopped": False,
                "managed": True,
                "identity_verified": False,
                "warning": (
                    "The recorded PID was not terminated because bridge identity "
                    "could not be verified; runtime evidence was preserved."
                ),
            }
        control = {"status": "control_not_attempted", "accepted": False}
        taskkill: dict[str, Any] = {"attempted": False}
        if identity_verified:
            control = self._authenticated_shutdown(runtime)
            if control.get("accepted"):
                stopped = self._wait_stopped(pid)
        if identity_verified and not stopped:
            self._end_task(TASK_NAME)
            try:
                proc = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=15,
                )
                taskkill = {
                    "attempted": True,
                    "returncode": proc.returncode,
                    "stderr": (proc.stderr or "")[-400:],
                }
            except (OSError, subprocess.SubprocessError) as exc:
                taskkill = {"attempted": True, "error": str(exc)}
            stopped = self._wait_stopped(pid)
            if not stopped:
                return {
                    "status": "stop_failed",
                    "pid": pid,
                    "stopped": False,
                    "managed": True,
                    "identity_verified": True,
                    "control_status": control.get("status"),
                    "taskkill": taskkill,
                    "warning": (
                        "The verified bridge process is still alive; runtime evidence "
                        "and scheduled tasks were preserved. Use the reported PID only as the final manual target."
                    ),
                }
        for task in (TASK_NAME, AUTO_START_TASK_NAME):
            self._delete_task(task)
        for pid_path in (
            Path(runtime.get("workdir") or default_workdir()) / "bridge.pid",
            self.state_root / "bridge.pid",
        ):
            try:
                pid_path.unlink(missing_ok=True)
            except OSError:
                # Legacy AppData workdirs may not be writable by the sandbox;
                # inability to clean history is not an error.
                pass
        self.runtime_file.unlink(missing_ok=True)
        return {
            "status": "stopped",
            "pid": pid,
            "stopped": stopped,
            "managed": True,
            "identity_verified": identity_verified,
            "control_status": control.get("status"),
            "taskkill": taskkill,
        }

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
        stopped = self.stop()
        if stopped["status"] not in {"stopped", "not_running"}:
            raise ManagerError(
                "bridge_stop_failed",
                "桥未被安全停止，已中止 restart；运行时证据与计划任务保持不变。",
                {
                    "stop_status": stopped["status"],
                    "pid": stopped.get("pid"),
                    "identity_verified": stopped.get("identity_verified"),
                    "control_status": stopped.get("control_status"),
                    "taskkill": stopped.get("taskkill"),
                },
            )
        # Restore exactly the same generation before starting the new process.
        restore_token(self.token_dir, token, token_state)
        return self.start(str(workdir_path), port=port, auto_start=auto_start)

    def rotate_token(self, workdir: str, port: int = 1981, auto_start: bool = False) -> dict[str, Any]:
        workdir_path = Path(workdir).expanduser().resolve()
        ensure_token(self.token_dir, legacy_workdir=workdir_path)
        stopped = self.stop()
        if stopped["status"] not in {"stopped", "not_running"}:
            raise ManagerError(
                "bridge_stop_failed",
                "桥未被安全停止，已中止令牌轮换；令牌与运行时证据保持不变。",
                {"stop_status": stopped["status"], "pid": stopped.get("pid")},
            )
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
