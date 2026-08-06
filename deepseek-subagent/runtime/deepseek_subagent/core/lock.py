"""跨平台进程文件锁。

接口为项目自己的 FileLock，核心代码不模拟 fcntl.flock 语义：

- POSIX：委托 fcntl.flock（LOCK_EX | LOCK_NB）。
- Windows：使用 msvcrt 字节锁（系统级字节区域锁，跨进程生效），
  并维护进程内占用表以模拟同进程多句柄冲突。

Windows 实现为开发版：跨进程互斥由操作系统字节锁保证，进程异常
退出后由操作系统自动释放；生产级实现（如 pywin32 LockFileEx）
留到后续轮次，不会用明文文件或注册表代替。
"""

from __future__ import annotations

import errno
import os
import sys
import time
from pathlib import Path


class LockTimeoutError(TimeoutError):
    pass


if sys.platform == "win32":
    import msvcrt

    _HELD: dict[tuple[int, int], int] = {}

    def _lock_key(handle) -> tuple[int, int]:
        stat = os.fstat(handle.fileno())
        return (stat.st_dev, stat.st_ino)

    def _os_lock(handle) -> None:
        fd = handle.fileno()
        key = _lock_key(handle)
        if key in _HELD:
            raise BlockingIOError(errno.EACCES, "lock already held in this process")
        handle.seek(0)
        if os.fstat(fd).st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError(exc.errno or errno.EACCES, str(exc)) from exc
        _HELD[key] = 1

    def _os_unlock(handle) -> None:
        key = _lock_key(handle)
        _HELD.pop(key, None)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _os_lock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _os_unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class FileLock:
    def __init__(self, path: Path | str, timeout: float = 5.0, poll: float = 0.05):
        self._path = Path(path)
        self._timeout = float(timeout)
        self._poll = float(poll)
        self._handle = None
        self._locked = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def locked(self) -> bool:
        return self._locked

    def acquire(self, timeout: float | None = None) -> None:
        limit = float(timeout) if timeout is not None else self._timeout
        deadline = time.monotonic() + limit
        handle = self._path.open("a+b")
        try:
            while True:
                try:
                    _os_lock(handle)
                    self._handle = handle
                    self._locked = True
                    return
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise LockTimeoutError(f"无法在 {limit:.1f}s 内获取文件锁：{self._path}") from None
                    time.sleep(min(self._poll, remaining))
        except Exception:
            handle.close()
            raise

    def release(self) -> None:
        if not self._locked:
            return
        try:
            _os_unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None
            self._locked = False

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
