"""配置事务：备份（带映射 manifest）、回滚与进程锁。

备份目录结构：
  <state_root>/backups/<stamp>/
    backup_manifest.json   备份条目映射
    files/0000-config.toml 按编号存储的文件副本

映射 manifest 记录目标绝对路径、备份相对路径、原文件 mode 与 existed，
恢复时按映射执行，不依赖文件 basename（同名文件可来自不同平台）。
通用层不识别任何 Agent 文件名；新文件 mode 由平台适配器经 new_modes 指定。
"""

from __future__ import annotations

import json
import shutil
import stat
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .atomic import atomic_write
from .errors import ManagerError
from .lock import FileLock, LockTimeoutError
from .paths import ProjectStatePaths

LOCK_WAIT_SECONDS = 5.0
BACKUP_MANIFEST_FILE = "backup_manifest.json"
BACKUP_FILES_DIR = "files"


class BackupEntry:
    def __init__(self, target: Path, backup: str | None, mode: int | None, existed: bool):
        self.target = target
        self.backup = backup
        self.mode = mode
        self.existed = existed

    def to_dict(self) -> dict:
        return {
            "target": str(self.target),
            "backup": self.backup,
            "mode": self.mode,
            "existed": self.existed,
        }

    @staticmethod
    def from_dict(data: dict) -> "BackupEntry":
        return BackupEntry(
            target=Path(data["target"]),
            backup=data.get("backup"),
            mode=data.get("mode"),
            existed=bool(data.get("existed")),
        )


def make_backup(
    state: ProjectStatePaths,
    targets: Iterable[Path],
    new_modes: dict[Path, int] | None = None,
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = state.backups / stamp
    files_dir = backup_dir / BACKUP_FILES_DIR
    files_dir.mkdir(parents=True, exist_ok=False)
    new_modes = new_modes or {}
    entries: list[BackupEntry] = []
    for index, target in enumerate(targets):
        if target.is_file():
            rel = f"{BACKUP_FILES_DIR}/{index:04d}-{target.name}"
            shutil.copy2(target, backup_dir / rel)
            entries.append(BackupEntry(target=target, backup=rel, mode=_file_mode(target), existed=True))
        else:
            entries.append(BackupEntry(target=target, backup=None, mode=new_modes.get(target), existed=False))
    _write_entries(backup_dir, entries)
    return backup_dir


def _file_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _write_entries(backup_dir: Path, entries: list[BackupEntry]) -> None:
    payload = {"version": 1, "entries": [entry.to_dict() for entry in entries]}
    atomic_write(backup_dir / BACKUP_MANIFEST_FILE, (json.dumps(payload, indent=2) + "\n").encode())


def restore_backup(backup_dir: Path, targets: Iterable[Path] | None = None) -> None:
    entries = _load_entries(backup_dir, targets)
    for entry in entries:
        source = backup_dir / entry.backup if entry.backup else None
        if source is not None and source.is_file():
            atomic_write(entry.target, source.read_bytes(), mode=entry.mode or 0o600)
        elif entry.target.is_file():
            entry.target.unlink()


def _load_entries(backup_dir: Path, targets: Iterable[Path] | None) -> list[BackupEntry]:
    manifest = backup_dir / BACKUP_MANIFEST_FILE
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            return [BackupEntry.from_dict(item) for item in payload.get("entries", [])]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass
    if targets is None:
        return []
    entries: list[BackupEntry] = []
    for target in targets:
        source = backup_dir / target.name
        if source.is_file():
            entries.append(BackupEntry(target=target, backup=target.name, mode=_file_mode(source), existed=True))
        else:
            entries.append(BackupEntry(target=target, backup=None, mode=None, existed=False))
    return entries


@contextmanager
def operation_lock(state: ProjectStatePaths, timeout_seconds: float = LOCK_WAIT_SECONDS):
    state.state_root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(state.lock, timeout=timeout_seconds)
    try:
        lock.acquire()
    except LockTimeoutError as exc:
        raise ManagerError(
            "operation_in_progress",
            "另一个配置操作仍在进行，请稍后重试。",
        ) from exc
    try:
        yield
    finally:
        lock.release()
