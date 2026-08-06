"""Persistent localhost bridge-token lifecycle in the Skill's private .local directory."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

TOKEN_VERSION = 1
TOKEN_FILE = "local-bridge-token.txt"
TOKEN_STATE_FILE = "local-bridge-token-state.json"
LEGACY_TOKEN_FILE = "token.txt"
LEGACY_TOKEN_STATE_FILE = "token-state.json"


def token_fingerprint(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _restrict_to_current_user(path: Path) -> None:
    os.chmod(path, 0o600)
    if os.name != "nt":
        return
    identity = subprocess.run(["whoami"], capture_output=True, text=True, errors="replace", timeout=10)
    principal = identity.stdout.strip() if identity.returncode == 0 else ""
    if not principal:
        username = os.environ.get("USERNAME") or getpass.getuser()
        domain = os.environ.get("USERDOMAIN")
        principal = f"{domain}\\{username}" if domain and "\\" not in username else username
    proc = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:F"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=15,
    )
    if proc.returncode != 0:
        raise OSError("failed to restrict local bridge token ACL")


def _atomic_secret_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _restrict_to_current_user(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_pair(token_dir: Path, token: str, state: dict[str, Any]) -> None:
    _atomic_secret_write(token_dir / TOKEN_FILE, (token + "\n").encode("utf-8"))
    payload = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    _atomic_secret_write(token_dir / TOKEN_STATE_FILE, payload)


def ensure_token(token_dir: Path, legacy_workdir: Path | None = None) -> tuple[str, dict[str, Any]]:
    """Return the stable token, migrating an old runtime token without rotation."""

    token_dir = Path(token_dir)
    token_path = token_dir / TOKEN_FILE
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    state = _read_json(token_dir / TOKEN_STATE_FILE) or {}
    migrated = False
    if not token and legacy_workdir is not None:
        legacy = Path(legacy_workdir)
        try:
            token = (legacy / LEGACY_TOKEN_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        if token:
            state = _read_json(legacy / LEGACY_TOKEN_STATE_FILE) or {}
            migrated = True
    if not token:
        token = secrets.token_urlsafe(32)
        generation = max(int(state.get("token_generation") or 0) + 1, 1)
        created_at = _now()
    else:
        generation = max(int(state.get("token_generation") or 1), 1)
        created_at = str(state.get("created_at") or _now())
    expected = {
        "token_version": TOKEN_VERSION,
        "token_generation": generation,
        "token_fingerprint": token_fingerprint(token),
        "created_at": created_at,
        "rotated_at": state.get("rotated_at"),
    }
    if state != expected or not token_path.is_file():
        _write_pair(token_dir, token, expected)
    else:
        _restrict_to_current_user(token_path)
        _restrict_to_current_user(token_dir / TOKEN_STATE_FILE)
    if migrated and legacy_workdir is not None:
        (Path(legacy_workdir) / LEGACY_TOKEN_FILE).unlink(missing_ok=True)
        (Path(legacy_workdir) / LEGACY_TOKEN_STATE_FILE).unlink(missing_ok=True)
    return token, expected


def restore_token(token_dir: Path, token: str, state: dict[str, Any]) -> None:
    expected = dict(state)
    expected["token_version"] = TOKEN_VERSION
    expected["token_fingerprint"] = token_fingerprint(token)
    _write_pair(Path(token_dir), token, expected)


def rotate_token(token_dir: Path) -> dict[str, Any]:
    _, previous = ensure_token(Path(token_dir))
    token = secrets.token_urlsafe(32)
    state = {
        "token_version": TOKEN_VERSION,
        "token_generation": int(previous["token_generation"]) + 1,
        "token_fingerprint": token_fingerprint(token),
        "created_at": previous.get("created_at") or _now(),
        "rotated_at": _now(),
    }
    _write_pair(Path(token_dir), token, state)
    return state


def describe_token(token_dir: Path) -> dict[str, Any]:
    token_dir = Path(token_dir)
    state = _read_json(token_dir / TOKEN_STATE_FILE)
    try:
        token = (token_dir / TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    fingerprint = token_fingerprint(token) if token else None
    return {
        "token_version": state.get("token_version") if state else None,
        "token_generation": state.get("token_generation") if state else None,
        "token_fingerprint": fingerprint,
        "token_file_present": bool(token),
        "token_state_consistent": bool(
            token and state and state.get("token_version") == TOKEN_VERSION and state.get("token_fingerprint") == fingerprint
        ),
    }


def purge_token(token_dir: Path) -> None:
    for name in (TOKEN_FILE, TOKEN_STATE_FILE):
        (Path(token_dir) / name).unlink(missing_ok=True)
