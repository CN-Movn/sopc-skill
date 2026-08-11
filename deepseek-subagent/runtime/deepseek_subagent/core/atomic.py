"""原子写入与哈希工具。"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from .errors import ManagerError


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    stage = "create_parent"
    tmp_path: Path | None = None
    fd: int | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        stage = "create_sibling"
        fd = os.open(tmp_path, flags, mode)
        stage = "write_sibling"
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(data)
            handle.flush()
            stage = "fsync_sibling"
            os.fsync(handle.fileno())
        stage = "chmod_sibling"
        os.chmod(tmp_path, mode)
        stage = "replace_target"
        os.replace(tmp_path, path)
        tmp_path = None
    except PermissionError as exc:
        if fd is not None:
            os.close(fd)
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise ManagerError(
            "managed_write_permission_denied",
            f"Permission denied while atomically writing managed file: {path}",
            {"path": str(path), "stage": stage},
        ) from exc
    except Exception:
        if fd is not None:
            os.close(fd)
        if tmp_path is None:
            raise
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
