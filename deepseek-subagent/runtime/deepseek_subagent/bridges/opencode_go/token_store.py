"""Persistent localhost bridge-token lifecycle in the Skill's private .local directory."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ...core.atomic import atomic_write
from ...core.errors import ManagerError
from ...core.lock import FileLock, LockTimeoutError

TOKEN_VERSION = 1
TOKEN_FILE = "local-bridge-token.txt"
TOKEN_STATE_FILE = "local-bridge-token-state.json"
LEGACY_TOKEN_FILE = "token.txt"
LEGACY_TOKEN_STATE_FILE = "token-state.json"
ACL_PRINCIPALS_FILE = "local-bridge-acl-principals.json"
ACL_PRINCIPALS_SCHEMA = 1
TOKEN_LOCK_FILE = "local-bridge-token.lock"
TOKEN_LOCK_TIMEOUT = 15.0
_SID_PATTERN = re.compile(r"^S-\d-(?:\d+-)+\d+$", re.IGNORECASE)


def _principal_state_path(token_dir: Path) -> Path:
    return Path(token_dir) / ACL_PRINCIPALS_FILE


def _read_principal_state(token_dir: Path | None) -> dict[str, Any] | None:
    """Read the secret-free stable ACL principal state.

    Returns None only when the file does not exist (fresh install).  A corrupt,
    unreadable, or schema-invalid state file fails closed with
    ``local_bridge_acl_principal_state_invalid``: it is never trusted, never
    used to guess SIDs, and never silently replaced.
    """

    if token_dir is None:
        return None
    path = _principal_state_path(token_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagerError(
            "local_bridge_acl_principal_state_invalid",
            f"Stable ACL principal state is unreadable or corrupt: {path}",
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != ACL_PRINCIPALS_SCHEMA:
        raise ManagerError(
            "local_bridge_acl_principal_state_invalid",
            f"Stable ACL principal state has an unsupported schema: {path}",
        )
    profile = str(payload.get("profile_owner_sid") or "").strip()
    if not _SID_PATTERN.fullmatch(profile):
        raise ManagerError(
            "local_bridge_acl_principal_state_invalid",
            f"Stable ACL principal state has an invalid profile owner SID: {path}",
        )
    sandboxes = payload.get("sandbox_sids")
    if not isinstance(sandboxes, list):
        raise ManagerError(
            "local_bridge_acl_principal_state_invalid",
            f"Stable ACL principal state has an invalid sandbox_sids field: {path}",
        )
    for sid in sandboxes:
        if not isinstance(sid, str) or not _SID_PATTERN.fullmatch(sid.strip()):
            raise ManagerError(
                "local_bridge_acl_principal_state_invalid",
                f"Stable ACL principal state contains an invalid sandbox SID: {path}",
            )
    return {
        "schema_version": ACL_PRINCIPALS_SCHEMA,
        "profile_owner_sid": profile,
        "sandbox_sids": [sid.strip() for sid in sandboxes],
    }


def _write_principal_state(token_dir: Path, payload: dict[str, Any]) -> None:
    """Persist the secret-free stable ACL principal state (fail-closed on error).

    The principal state is part of the ACL security model: if the sandbox
    observed the sandbox SID but cannot persist it, the elevated repair must
    not proceed as if the stable set were saved.
    """

    try:
        atomic_write(
            _principal_state_path(token_dir),
            (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
    except OSError as exc:
        raise ManagerError(
            "local_bridge_acl_principal_state_write_failed",
            f"Stable ACL principal state could not be persisted: {_principal_state_path(token_dir)}",
        ) from exc


def _resolve_windows_principal_sids(path: Path, token_dir: Path | None = None) -> list[str]:
    """Resolve the stable ACL principal set for the token.

    The profile SID comes from the Windows ProfileList entry whose profile path
    contains the token.  The execution SID is merged into a persistent,
    secret-free principal state under ``.local``
    (``local-bridge-acl-principals.json``): once a distinct sandbox SID is
    observed (current != profile), it is recorded, and every later hardening or
    repair keeps it even when the current identity is the elevated profile
    owner.  This removes the failure mode where an elevated repair rewrote the
    ACL to the profile owner only, causing the next sandbox conversation to hit
    Access Denied again.
    """

    script = r"""
$ErrorActionPreference = 'Stop'
$target = [IO.Path]::GetFullPath($env:DEEPSEEK_TOKEN_ACL_TARGET)
$current = [Security.Principal.WindowsIdentity]::GetCurrent()
$currentSid = $current.User.Value
$currentName = $current.Name
$profileSid = $null
$profilePath = $null
$bestLength = -1
$profileRoot = 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList'
foreach ($entry in Get-ChildItem -LiteralPath $profileRoot -ErrorAction Stop) {
    $raw = (Get-ItemProperty -LiteralPath $entry.PSPath -Name ProfileImagePath -ErrorAction SilentlyContinue).ProfileImagePath
    if (-not $raw) { continue }
    $candidate = [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables([string]$raw)).TrimEnd('\')
    $prefix = $candidate + '\'
    if (($target -ieq $candidate -or $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) -and $candidate.Length -gt $bestLength) {
        $profileSid = $entry.PSChildName
        $profilePath = $candidate
        $bestLength = $candidate.Length
    }
}
if (-not $profileSid) { throw 'No ProfileList SID contains the token path.' }
$profileName = ([Security.Principal.SecurityIdentifier]$profileSid).Translate([Security.Principal.NTAccount]).Value
[pscustomobject]@{
    current_sid = $currentSid
    current_name = $currentName
    profile_sid = $profileSid
    profile_name = $profileName
    profile_path = $profilePath
} | ConvertTo-Json -Compress
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    env = {**os.environ, "DEEPSEEK_TOKEN_ACL_TARGET": str(Path(path).resolve())}
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            env=env,
        )
        payload = json.loads(proc.stdout) if proc.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise OSError("failed to resolve Windows token ACL principals") from exc
    current_sid = str(payload.get("current_sid") or "").strip()
    profile_sid = str(payload.get("profile_sid") or "").strip()
    if not _SID_PATTERN.fullmatch(current_sid) or not _SID_PATTERN.fullmatch(profile_sid):
        raise OSError("failed to resolve both execution and Windows profile SIDs")

    stable = _read_principal_state(token_dir)
    stable_sids: list[str] = []
    if stable is not None:
        stable_sids = [stable["profile_owner_sid"], *stable["sandbox_sids"]]
    sids = list(dict.fromkeys((*stable_sids, current_sid, profile_sid)))

    if token_dir is not None:
        if stable is None:
            payload_state = {
                "schema_version": ACL_PRINCIPALS_SCHEMA,
                "profile_owner_sid": profile_sid,
                "sandbox_sids": [current_sid] if current_sid != profile_sid else [],
            }
            _write_principal_state(token_dir, payload_state)
        elif current_sid != profile_sid and current_sid not in stable["sandbox_sids"]:
            updated = dict(stable)
            updated["sandbox_sids"] = [*stable["sandbox_sids"], current_sid]
            _write_principal_state(token_dir, updated)
    return sids


def token_fingerprint(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _restrict_to_current_user(path: Path, principals: list[str] | None = None) -> None:
    os.chmod(path, 0o600)
    if os.name != "nt":
        return
    principals = principals or _resolve_windows_principal_sids(path)
    proc = subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            *[f"*{sid}:(F)" for sid in principals],
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=15,
    )
    if proc.returncode != 0:
        raise OSError("failed to restrict local bridge token ACL")


def repair_token_acl(token_dir: Path) -> dict[str, Any]:
    """Repair existing token ACLs without reading or rewriting either file."""

    token_dir = Path(token_dir)
    paths = [token_dir / name for name in (TOKEN_FILE, TOKEN_STATE_FILE)]
    paths = [path for path in paths if path.is_file()]
    principals = (
        _resolve_windows_principal_sids(paths[0], token_dir) if os.name == "nt" and paths else None
    )
    repaired: list[str] = []
    for path in paths:
        _restrict_to_current_user(path, principals=principals)
        repaired.append(path.name)
    return {"status": "token_acl_repaired", "files_repaired": repaired}


def _atomic_secret_write(path: Path, data: bytes, principals: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # Secure the temporary inode before replacement.  If ACL setup fails,
        # the existing secret remains untouched.
        _restrict_to_current_user(tmp_path, principals=principals)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_pair(token_dir: Path, token: str, state: dict[str, Any]) -> None:
    token_path = token_dir / TOKEN_FILE
    principals = _resolve_windows_principal_sids(token_path, token_dir) if os.name == "nt" else None
    _atomic_secret_write(token_path, (token + "\n").encode("utf-8"), principals=principals)
    payload = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    _atomic_secret_write(token_dir / TOKEN_STATE_FILE, payload, principals=principals)


def _token_lock(token_dir: Path) -> FileLock:
    token_dir = Path(token_dir)
    token_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(token_dir / TOKEN_LOCK_FILE, timeout=TOKEN_LOCK_TIMEOUT)


def ensure_token(token_dir: Path, legacy_workdir: Path | None = None) -> tuple[str, dict[str, Any]]:
    try:
        with _token_lock(Path(token_dir)):
            return _ensure_token_unlocked(Path(token_dir), legacy_workdir=legacy_workdir)
    except LockTimeoutError as exc:
        raise ManagerError(
            "local_bridge_token_operation_in_progress",
            "Another process is updating the managed localhost bridge token; retry prepare once.",
        ) from exc


def _ensure_token_unlocked(token_dir: Path, legacy_workdir: Path | None = None) -> tuple[str, dict[str, Any]]:
    """Return the stable token, migrating an old runtime token without rotation.

    An existing, readable, consistent token takes a fast path that never
    touches the ACL: repair happens only on fresh creation, explicit
    rotate/restore/migration, metadata mismatch, or a failed read.  A failed
    read (Access Denied) repairs the ACL once, re-reads once, and then fails
    closed without looping or rotating.
    """

    token_dir = Path(token_dir)
    token_path = token_dir / TOKEN_FILE
    state_path = token_dir / TOKEN_STATE_FILE
    token_exists = token_path.exists()

    token = ""
    if token_exists:
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except PermissionError:
            try:
                repair_token_acl(token_dir)
            except OSError as exc:
                raise ManagerError(
                    "local_bridge_token_acl_repair_failed",
                    f"The managed localhost bridge token ACL could not be repaired safely: {token_path}",
                ) from exc
            try:
                token = token_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ManagerError(
                    "local_bridge_token_unreadable",
                    f"The managed localhost bridge token cannot be read: {token_path}",
                ) from exc
        except OSError as exc:
            raise ManagerError(
                "local_bridge_token_unreadable",
                f"The managed localhost bridge token cannot be read: {token_path}",
            ) from exc
        if not token:
            raise ManagerError(
                "local_bridge_token_invalid",
                f"The managed localhost bridge token file is empty: {token_path}",
            )
    state = _read_json(state_path) or {}
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
        if state_path.exists():
            raise ManagerError(
                "local_bridge_token_missing",
                "The token metadata exists but the managed localhost bridge token is missing; "
                "implicit token rotation was refused.",
            )
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
    if state == expected and token_path.is_file() and not migrated:
        # Fast path: existing token is fully consistent, so no ACL rewrite.
        return token, expected
    _write_pair(token_dir, token, expected)
    if migrated and legacy_workdir is not None:
        (Path(legacy_workdir) / LEGACY_TOKEN_FILE).unlink(missing_ok=True)
        (Path(legacy_workdir) / LEGACY_TOKEN_STATE_FILE).unlink(missing_ok=True)
    return token, expected


def restore_token(token_dir: Path, token: str, state: dict[str, Any]) -> None:
    try:
        with _token_lock(Path(token_dir)):
            expected = dict(state)
            expected["token_version"] = TOKEN_VERSION
            expected["token_fingerprint"] = token_fingerprint(token)
            _write_pair(Path(token_dir), token, expected)
    except LockTimeoutError as exc:
        raise ManagerError(
            "local_bridge_token_operation_in_progress",
            "Another process is updating the managed localhost bridge token; retry the operation once.",
        ) from exc


def rotate_token(token_dir: Path) -> dict[str, Any]:
    try:
        with _token_lock(Path(token_dir)):
            _, previous = _ensure_token_unlocked(Path(token_dir))
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
    except LockTimeoutError as exc:
        raise ManagerError(
            "local_bridge_token_operation_in_progress",
            "Another process is updating the managed localhost bridge token; retry the operation once.",
        ) from exc


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
