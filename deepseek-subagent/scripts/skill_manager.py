#!/usr/bin/env python3
"""Stable lifecycle entrypoint for the installed deepseek-subagent Skill."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = SKILL_DIR / "runtime"
sys.path.insert(0, str(RUNTIME_DIR))

from deepseek_subagent.bridges.opencode_go.credentials import (  # noqa: E402
    CredentialError,
    credential_status,
    discover_credential,
    key_file_path,
)
from deepseek_subagent.bridges.opencode_go.control import BRIDGE_ABI_VERSION  # noqa: E402
from deepseek_subagent.bridges.opencode_go.lifecycle import BridgeLifecycle  # noqa: E402
from deepseek_subagent.bridges.opencode_go.token_store import describe_token, purge_token  # noqa: E402
from deepseek_subagent.cli import run as run_cli  # noqa: E402
from deepseek_subagent.core.agent_role import make_role  # noqa: E402
from deepseek_subagent.core.agent_handoff import (  # noqa: E402
    continuity_key,
    default_handoff_root,
    handoff_path,
    initialize_handoff,
    issue_handoff_turn,
    verify_handoff_update,
)
from deepseek_subagent.core.agent_roster import (  # noqa: E402
    list_agents,
    register_agent,
    register_successor,
    retire_agent,
)
from deepseek_subagent.core.atomic import atomic_write, sha256_bytes  # noqa: E402
from deepseek_subagent.core.errors import ManagerError  # noqa: E402
from deepseek_subagent.core.lock import FileLock, LockTimeoutError  # noqa: E402
from deepseek_subagent.core.manifest import read_manifest, write_manifest  # noqa: E402
from deepseek_subagent.core.paths import PROJECT_NAME, state_paths  # noqa: E402
from deepseek_subagent.core.transaction import LOCK_WAIT_SECONDS  # noqa: E402
from deepseek_subagent.platforms.codex.paths import CodexPaths  # noqa: E402
from deepseek_subagent.platforms.codex.transport import (  # noqa: E402
    assess_v1_transport,
    inspect_current_task,
    inspect_thread_identity,
)
from deepseek_subagent.platforms.codex.verify import direct_test  # noqa: E402
from deepseek_subagent.providers import OpenCodeGoProvider  # noqa: E402

PORT = 1981


def _state():
    return state_paths(None)


def _local_key_file() -> Path:
    return key_file_path(SKILL_DIR)


def _published_token_file() -> Path:
    return SKILL_DIR / ".local" / "local-bridge-token.txt"


def _bridge_state_root() -> Path:
    """Canonical bridge mutable-state root under the installed Skill's `.local`.

    Normal ``prepare``/bridge lifecycle writes (runtime metadata, workdir,
    pid/json, recovery dirs) all live here so the sandbox never needs to write
    ``%LOCALAPPDATA%\\deepseek-subagent``.
    """

    return SKILL_DIR / ".local" / "bridge"


def _legacy_appdata_root(state) -> Path:
    """Legacy `%LOCALAPPDATA%\\deepseek-subagent` root for read-only discovery.

    Used only to find and safely retire pre-1.6.14 state (old bridge runtime
    metadata, old roster); it is never written by the normal path.
    """

    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / PROJECT_NAME
    return state.state_root


def _lifecycle(state) -> BridgeLifecycle:
    return BridgeLifecycle(
        _bridge_state_root(),
        script=str(RUNTIME_DIR / "scripts" / "bridge_standalone.py"),
        legacy_state_root=_legacy_appdata_root(state),
    )


def _roster_lock() -> FileLock:
    """Roster mutation lock, in the same `.local` domain as the canonical roster.

    Serializes read-modify-write of `<skill-root>\\.local\\agents.json`; it never
    depends on the AppData state root.  Slow native verification must happen
    before this lock is acquired.
    """

    lock_dir = _canonical_roster_file().parent / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(lock_dir / "agents.lock", timeout=LOCK_WAIT_SECONDS)


def _roster_mutation_timeout() -> ManagerError:
    return ManagerError("operation_in_progress", "roster 操作正在进行，请稍后重试。")


def _handoff_lock(project_root: str, stable_role: str, scope: str) -> FileLock:
    """Per-handoff lock under the canonical store's `.locks` directory.

    Independent handoffs (different project identity/role/scope) never contend;
    the same deterministic handoff is serialized.  The lock is runtime local
    state and is created idempotently.
    """

    key = continuity_key(project_root, stable_role, scope)
    locks_dir = default_handoff_root() / ".locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(locks_dir / f"{key}.lock", timeout=LOCK_WAIT_SECONDS)


ROSTER_MIGRATED_ARCHIVE_NAME = "agents.json.migrated-v1.6.8.bak"


def _roster_legacy_conflict_archive(legacy: Path) -> Path:
    digest = sha256_bytes(legacy.read_bytes())[:12]
    return legacy.with_name(f"agents.json.legacy-conflict-{digest}.bak")


def _canonical_roster_file() -> Path:
    """Canonical roster for this installation instance: `<skill-root>\\.local\\agents.json`."""

    return SKILL_DIR / ".local" / "agents.json"


def _roster_path(state) -> tuple[Path, dict[str, Any]]:
    """Resolve the canonical roster, consuming a legacy AppData roster once.

    The canonical roster lives with the handoffs under the installed Skill's
    private `.local`, so both are removed together when the user deletes the
    whole installation.  A legacy `%LOCALAPPDATA%\\deepseek-subagent\\agents.json`
    is migrated exactly once and then archived by rename
    (``agents.json.migrated-v1.6.8.bak``), so a later clean reinstall can never
    re-import it.  The migration is transactional: if the archive rename fails,
    the just-written canonical file is rolled back and the operation reports
    ``roster_migration_finalize_failed`` instead of leaving a half-migrated
    state.  A legacy file that still matches the canonical content (an
    unfinished v1.6.8 migration) is archived silently; a legacy file with
    different content is archived out of the discovery path as
    ``agents.json.legacy-conflict-<hash>.bak`` and reported as
    ``roster_legacy_conflict_archived``, so no divergent legacy can ever be
    re-imported by a later clean reinstall.
    """

    canonical = _canonical_roster_file()
    legacy = _legacy_appdata_root(state) / "agents.json"
    archive = legacy.with_name(ROSTER_MIGRATED_ARCHIVE_NAME)
    notes: dict[str, Any] = {}
    if canonical.is_file():
        if legacy.is_file():
            if canonical.read_bytes() == legacy.read_bytes():
                try:
                    legacy.replace(archive)
                except OSError as exc:
                    raise ManagerError(
                        "roster_migration_finalize_failed",
                        f"Legacy Agent roster could not be archived: {archive}",
                    ) from exc
                notes["roster_archived_to"] = str(archive)
            else:
                conflict_archive = _roster_legacy_conflict_archive(legacy)
                try:
                    legacy.replace(conflict_archive)
                except OSError as exc:
                    raise ManagerError(
                        "roster_migration_finalize_failed",
                        f"Divergent legacy Agent roster could not be archived: {conflict_archive}",
                    ) from exc
                notes["roster_legacy_conflict_archived"] = {
                    "legacy_file": str(legacy),
                    "archived_to": str(conflict_archive),
                    "message": "新旧 roster 内容不一致；canonical roster 已保留，旧 legacy 已归档移出 discovery path。",
                }
        return canonical, notes
    if legacy.is_file():
        try:
            data = legacy.read_bytes()
            atomic_write(canonical, data)
            legacy.replace(archive)
        except OSError as exc:
            canonical.unlink(missing_ok=True)
            raise ManagerError(
                "roster_migration_finalize_failed",
                "Legacy Agent roster could not be archived after migration; "
                "migration was rolled back and can be retried.",
            ) from exc
        notes["roster_migrated_from"] = str(legacy)
        notes["roster_archived_to"] = str(archive)
    return canonical, notes


def _handoff_identity(value: str | None) -> str:
    if not value:
        return ""
    return str(Path(value).expanduser().resolve()).casefold()


def _handoff_ownership_preflight(state, handoff_file: str, parent_thread_id: str) -> dict[str, Any]:
    """Read-only check that no other Codex root holds an open owner for the handoff.

    Preflight fails fast before a child is spawned; `register` remains the
    authoritative ownership boundary.  A small race between preflight and
    register is accepted by design.
    """

    roster, _notes = _roster_path(state)
    parent_bindings = _authoritative_parent_bindings(state)
    target = _handoff_identity(handoff_file)
    entries = list_agents(
        roster,
        parent_thread_id=None,
        include_retired=True,
        include_other_parents=True,
        parent_bindings=parent_bindings,
    )
    for entry in entries:
        if entry.get("state") != "open":
            continue
        if _handoff_identity(str(entry.get("handoff_file") or "")) != target:
            continue
        if entry.get("parent_thread_id") == parent_thread_id:
            return {"blocked": False, "reason": "current_parent_owner"}
        return {
            "blocked": True,
            "owner": {
                "agent_id": entry.get("agent_id"),
                "parent_thread_id": entry.get("parent_thread_id"),
                "stable_role": entry.get("stable_role"),
                "scope": entry.get("scope"),
                "handoff_generation": entry.get("handoff_generation"),
                "updated_at": entry.get("updated_at"),
            },
        }
    return {"blocked": False, "reason": "no_open_owner"}


def _reinstall_prep(state) -> dict[str, Any]:
    """Safely stop the managed bridge before the user removes the installation.

    Uses only the existing authenticated, identity-verified shutdown path and
    fails closed when identity cannot be verified.  Never deletes the Key,
    handoffs, the roster, or user data.
    """

    lifecycle = _lifecycle(state)
    current = lifecycle.status(port=PORT)
    if current.get("status") == "not_started":
        return {
            "status": "reinstall_prep_ready",
            "ready_for_reinstall": True,
            "bridge_stopped": False,
            "bridge_status": "not_started",
            "message": "没有运行中的受管 bridge，可以安全删除安装目录。",
        }
    result = lifecycle.stop()
    if result.get("status") in {"stopped", "not_running"}:
        return {
            "status": "reinstall_prep_ready",
            "ready_for_reinstall": True,
            "bridge_stopped": True,
            "bridge_status": "stopped",
            "pid": result.get("pid"),
            "identity_verified": result.get("identity_verified"),
            "control_status": result.get("control_status"),
            "message": "旧 bridge 已通过身份验证并安全停止；可以删除安装目录。",
        }
    return {
        "status": "reinstall_prep_blocked",
        "ready_for_reinstall": False,
        "bridge_stopped": False,
        "bridge_status": result.get("status"),
        "error_code": str(result.get("status") or "bridge_stop_failed"),
        "pid": result.get("pid"),
        "identity_verified": result.get("identity_verified"),
        "message": "旧 bridge 无法安全停止（身份未验证或停止失败）；请先手动结束已确认的 bridge PID 后再重装。",
    }


def _capture(argv: list[str]) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = run_cli(argv + ["--json"])
    text = output.getvalue().strip()
    try:
        payload = json.loads(text) if text else {"status": "no_output"}
    except json.JSONDecodeError:
        payload = {"status": "invalid_cli_output", "output": text[-2000:]}
    return code, payload


def _runtime_payload(lifecycle: BridgeLifecycle) -> dict[str, Any]:
    return lifecycle.status(port=PORT)


def _require_key() -> None:
    target = _local_key_file()
    try:
        credential = discover_credential(target)
    except CredentialError as exc:
        raise ManagerError(exc.code, str(exc), {"key_file": str(exc.path)}) from exc
    if credential is None:
        raise ManagerError(
            "upstream_key_missing",
            f"OpenCode Go key file is missing: {target}",
            {"key_file": str(target)},
        )


def _skill_version() -> str:
    return (SKILL_DIR / "VERSION").read_text(encoding="utf-8").strip()


def _managed_skill_version_changed(state) -> bool:
    manifest = read_manifest(state.state_root)
    return manifest.get("skill_version") != _skill_version()


def _bridge_runtime_ready(state, current: dict[str, Any]) -> bool:
    if (
        current.get("status") != "running"
        or current.get("identity_verified") is not True
        or current.get("bridge_abi_compatible") is not True
        or current.get("bridge_abi_version") != BRIDGE_ABI_VERSION
    ):
        return False
    runtime_file = _bridge_state_root() / "bridge-runtime.json"
    try:
        runtime = json.loads(runtime_file.read_text(encoding="utf-8"))
        workdir = Path(runtime["workdir"]).resolve()
        bridge_json = workdir / "bridge.json"
        bridge_info = json.loads(bridge_json.read_text(encoding="utf-8"))
    except (KeyError, OSError, json.JSONDecodeError):
        return False
    expected_script = str((RUNTIME_DIR / "scripts" / "bridge_standalone.py").resolve())
    runtime_matches = (
        runtime.get("script") == expected_script
        and runtime.get("workdir") == str(workdir)
        and runtime.get("bridge_abi_version") == BRIDGE_ABI_VERSION
    )
    auth_matches = (
        bridge_info.get("token_file") == str(_published_token_file().resolve())
        and bridge_info.get("token_script")
        == str((RUNTIME_DIR / "scripts" / "print_bridge_token.py").resolve())
    )
    token_matches = True
    recorded_fingerprint = bridge_info.get("token_fingerprint")
    if recorded_fingerprint:
        # A bridge started before a Skill reinstall holds the old token in
        # memory; its recorded fingerprint no longer matches the current file.
        # Such a bridge must not be reused (it would reject every request with
        # 401); the transition path stops it and starts a canonical bridge.
        token_matches = (
            describe_token(_published_token_file().parent).get("token_fingerprint")
            == recorded_fingerprint
        )
    return (
        runtime_matches
        and auth_matches
        and token_matches
        and bridge_info.get("bridge_abi_version") == BRIDGE_ABI_VERSION
    )


def _ensure_bridge(
    state,
    force_restart: bool = False,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _require_key()
    lifecycle = _lifecycle(state)
    expected_workdir = _bridge_state_root() / "runtime"
    current = current if current is not None else lifecycle.status(port=PORT)
    status = str(current.get("status") or "not_started")
    current_workdir = Path(current.get("workdir") or expected_workdir).expanduser().resolve()
    current_port = int(current.get("port") or PORT)

    def recover_or_replace(evidence: dict[str, Any]) -> dict[str, Any]:
        try:
            return lifecycle.restart(
                str(expected_workdir),
                port=current_port,
                auto_start=False,
            )
        except ManagerError as exc:
            if exc.code != "bridge_stop_failed":
                raise
            replacement_evidence = {
                **evidence,
                "stop_status": exc.details.get("stop_status"),
                "control_status": exc.details.get("control_status"),
            }
            return lifecycle.replace_unrecoverable(replacement_evidence)

    if status == "running" and _bridge_runtime_ready(state, current) and not force_restart:
        result = lifecycle.start(
            str(current_workdir),
            port=current_port,
            auto_start=False,
        )
    elif status in {"running", "unhealthy", "incompatible"} or force_restart:
        result = recover_or_replace(current)
    else:
        result = lifecycle.start(str(expected_workdir), port=PORT, auto_start=False)

    if result.get("status") in {"unhealthy", "incompatible"}:
        result = recover_or_replace(result)

    if result.get("status") not in {"started", "already_running", "replaced_unrecoverable"}:
        raise ManagerError(
            "bridge_ensure_failed",
            "The managed bridge did not reach a usable running state.",
            {"bridge_status": result.get("status")},
        )
    if not result.get("workdir"):
        result = {**result, "workdir": str(expected_workdir.resolve())}
    return result


def _annotate_manifest(state, bridge: dict[str, Any]) -> None:
    manifest = read_manifest(state.state_root)
    if not manifest:
        return
    baseline_backup = manifest.get("baseline_backup")
    if not baseline_backup:
        for candidate in sorted((state.state_root / "backups").glob("*")):
            backup_manifest = candidate / "backup_manifest.json"
            if not backup_manifest.is_file():
                continue
            try:
                entries = json.loads(backup_manifest.read_text(encoding="utf-8")).get("entries", [])
            except (OSError, json.JSONDecodeError):
                continue
            existed = {Path(item.get("target", "")).name: item.get("existed") for item in entries}
            if existed.get("models-with-deepseek.json") is False and existed.get("DeepSeek.toml") is False:
                baseline_backup = str(candidate.resolve())
                break
    manifest.update(
        {
            "skill_version": _skill_version(),
            "skill_root": str(SKILL_DIR.resolve()),
            "runtime_root": str(RUNTIME_DIR.resolve()),
            "bridge_script": str((RUNTIME_DIR / "scripts" / "bridge_standalone.py").resolve()),
            "bridge_workdir": bridge.get("workdir"),
            "baseline_backup": baseline_backup,
        }
    )
    write_manifest(state.state_root, manifest)


def _failed_doctor(payload: dict[str, Any], stage: str, code: str, message: str) -> dict[str, Any]:
    return {
        **payload,
        "status": "partial",
        "failure_stage": stage,
        "error_code": code,
        "message": message,
    }


def _transport_payload(state, static: dict[str, Any] | None = None) -> dict[str, Any]:
    if static is None:
        _static_code, static = _capture(["status"])
    codex_home = _codex_home(state)
    return assess_v1_transport(codex_home, OpenCodeGoProvider.model, static)


def _codex_home(state) -> Path:
    manifest = read_manifest(state.state_root)
    return Path(manifest.get("platform_home") or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def _current_task_evidence(state) -> dict[str, Any]:
    return inspect_current_task(_codex_home(state))


def _current_root_thread(state) -> tuple[str, dict[str, Any]]:
    evidence = _current_task_evidence(state)
    if not evidence.get("detected") or not evidence.get("thread_id"):
        raise ManagerError(
            "current_parent_unconfirmed",
            "The current Codex root thread could not be verified from its persisted rollout",
        )
    if evidence.get("parent_thread_id"):
        raise ManagerError(
            "current_task_is_child",
            "DeepSeek roster operations must be initiated by the root Codex thread",
        )
    return str(evidence["thread_id"]), evidence


def _authoritative_parent_bindings(state) -> dict[str, dict[str, str]]:
    """Resolve legacy roster parentage from native rollout evidence only."""

    bindings: dict[str, dict[str, str]] = {}
    codex_home = _codex_home(state)
    roster, _notes = _roster_path(state)
    entries = list_agents(
        roster,
        parent_thread_id=None,
        include_retired=True,
        include_other_parents=True,
    )
    for entry in entries:
        if entry.get("parent_thread_id"):
            continue
        identity = inspect_thread_identity(codex_home, str(entry.get("agent_id") or ""))
        if (
            identity.get("parent_verified")
            and identity.get("parent_thread_id")
            and identity.get("thread_source") == "subagent"
            and identity.get("agent_role") == "DeepSeek"
            and identity.get("model_provider") == "opencode-go-bridge"
        ):
            bindings[str(entry["agent_id"])] = {
                "parent_thread_id": str(identity["parent_thread_id"]),
                "parent_evidence": "rollout_session_meta",
            }
    return bindings


def _verify_child_parent(state, agent_id: str, parent_thread_id: str) -> dict[str, Any]:
    identity = inspect_thread_identity(_codex_home(state), agent_id)
    if not identity.get("detected"):
        raise ManagerError(
            str(identity.get("error_code") or "child_identity_not_found"),
            f"Native Codex child identity was not found for Agent {agent_id}",
        )
    if not identity.get("parent_verified"):
        raise ManagerError(
            str(identity.get("error_code") or "child_parent_unverified"),
            f"Native Codex parent evidence is incomplete for Agent {agent_id}",
        )
    if identity.get("parent_thread_id") != parent_thread_id:
        raise ManagerError(
            "agent_parent_mismatch",
            "The DeepSeek Agent belongs to a different Codex root thread",
        )
    if identity.get("thread_source") != "subagent" or identity.get("agent_role") != "DeepSeek":
        raise ManagerError("agent_identity_mismatch", "The native thread is not a DeepSeek child Agent")
    if identity.get("model_provider") != "opencode-go-bridge":
        raise ManagerError(
            "agent_provider_mismatch",
            "The native child does not use the managed opencode-go-bridge provider",
        )
    return identity


def _repair_static_configuration(
    state,
    bridge: dict[str, Any],
    current_model: str | None = None,
) -> tuple[int, dict[str, Any]]:
    bridge_json = Path(bridge.get("workdir", "")) / "bridge.json"
    argv = ["repair", "--bridge-json", str(bridge_json)]
    if current_model:
        argv.extend(["--parent-model", current_model])
    code, payload = _capture(argv)
    if code == 0:
        _annotate_manifest(state, bridge)
    return code, payload


def _prepare_for_deepseek(state) -> tuple[int, dict[str, Any]]:
    """Auto-heal managed prerequisites before any DeepSeek child operation.

    The routine is intentionally lighter than ``doctor --e2e``. It repairs
    Skill-owned Codex configuration and on-demand bridge state before the first
    child request, reuses a healthy ABI-compatible bridge across Skill-only
    version changes, and then
    applies the fail-closed V1 transport gate. An already initialized V2 task
    is never claimed to have changed in place.
    """

    evidence_before = _current_task_evidence(state)
    static_code, static_before = _capture(["status"])
    static_ready = static_code == 0 and static_before.get("status") == "configured"
    version_changed = _managed_skill_version_changed(state)

    lifecycle = _lifecycle(state)
    bridge_before = lifecycle.status(port=PORT)
    bridge_ready = _bridge_runtime_ready(state, bridge_before)

    actions: list[str] = []
    bridge = bridge_before
    repair_payload: dict[str, Any] | None = None

    # A real upstream Key is user-owned and cannot be auto-created. Stop before
    # spawning a child, but never read or echo the credential value.
    _require_key()

    needs_static_repair = (not static_ready) or version_changed
    needs_bridge_ensure = (not bridge_ready) or version_changed

    if needs_bridge_ensure or needs_static_repair:
        bridge = _ensure_bridge(
            state,
            force_restart=False,
            current=bridge_before,
        )
        if version_changed:
            actions.append(
                "skill_update_bridge_reuse"
                if bridge.get("status") == "already_running"
                else "skill_update_bridge_refresh"
            )
        elif not bridge_ready:
            actions.append("bridge_ensure")
        if bridge.get("status") == "replaced_unrecoverable":
            actions.append("bridge_unrecoverable_replaced")
        if bridge.get("provider_repair_required"):
            needs_static_repair = True

    if needs_static_repair:
        current_model = evidence_before.get("model") if evidence_before.get("detected") else None
        repair_code, repair_payload = _repair_static_configuration(state, bridge, current_model)
        if repair_code != 0:
            return 2, {
                "status": "automatic_preconfiguration_failed",
                "ready_for_deepseek": False,
                "automatic_preconfiguration": True,
                "changed": bool(actions),
                "actions": actions,
                "skill_version_changed": version_changed,
                "static_before": static_before,
                "repair": repair_payload,
                "current_task": evidence_before,
                "message": "DeepSeek 自动预配置未能修复受管 Codex 配置；未执行 spawn/send。",
            }
        actions.append("static_repair")

    static_code, static_after = _capture(["status"])
    if static_code != 0 or static_after.get("status") != "configured":
        return 2, {
            "status": "automatic_preconfiguration_failed",
            "ready_for_deepseek": False,
            "automatic_preconfiguration": True,
            "changed": bool(actions),
            "actions": actions,
            "skill_version_changed": version_changed,
            "static_before": static_before,
            "static_after": static_after,
            "repair": repair_payload,
            "current_task": evidence_before,
            "message": "自动预配置完成后 Codex 受管配置仍不完整；未执行 spawn/send。",
        }

    transport = _transport_payload(state, static_after)
    safe = transport.get("safe_to_spawn_send") is True
    payload: dict[str, Any] = {
        "status": "deepseek_ready" if safe else "v1_transport_blocked",
        "ready_for_deepseek": safe,
        "safe_to_spawn_send": safe,
        "automatic_preconfiguration": True,
        "changed": bool(actions),
        "actions": actions,
        "skill_version_changed": version_changed,
        "static_before_status": static_before.get("status"),
        "static_after_status": static_after.get("status"),
        "bridge": bridge,
        "transport": transport,
    }
    if repair_payload is not None:
        payload["repair"] = repair_payload

    if safe:
        payload["message"] = (
            "DeepSeek 受管配置、bridge 与当前 task 的 Multi-Agent V1 已在调用前自动确认；"
            "可以执行 spawn/send。"
        )
        return 0, payload

    payload["error_code"] = transport.get("error_code")
    payload["message"] = transport.get("message")

    if transport.get("error_code") == "current_task_multi_agent_v2":
        # A V2 task cannot be changed in place. If its actual UI-selected parent
        # model was not already part of the managed V1 repair, prepare that model
        # for the next task now so the user does not hit the same failure again.
        actual_model = (transport.get("current_task") or {}).get("model")
        repaired_models = {evidence_before.get("model")} if needs_static_repair else set()
        if actual_model and actual_model not in repaired_models:
            bridge = _ensure_bridge(state)
            repair_code, future_repair = _repair_static_configuration(state, bridge, str(actual_model))
            payload["future_task_repair"] = future_repair
            if repair_code == 0:
                actions.append("future_task_parent_v1_repair")
                payload["changed"] = True
                payload["actions"] = actions
        payload["status"] = "configured_new_task_required"
        payload["new_task_required"] = True
        payload["ready_for_deepseek"] = False
        payload["safe_to_spawn_send"] = False
    return 2, payload


def _json_request(url: str, token: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    request = urllib.request.Request(url, method="POST" if payload is not None else "GET", headers=headers)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
        request.data = json.dumps(payload).encode("utf-8")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=180 if payload is not None else 10) as response:
            status = int(response.status)
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", "replace")
    except (OSError, urllib.error.URLError, TimeoutError):
        return 0, {"error": {"code": "localhost_unreachable"}}
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        body = {"error": {"code": "invalid_bridge_response"}}
    return status, body if isinstance(body, dict) else {}


def _doctor(state) -> tuple[int, dict[str, Any]]:
    static_code, static = _capture(["status"])
    payload: dict[str, Any] = {
        "status": "partial",
        "checks": {
            "static_configuration": static.get("status") == "configured",
            "multi_agent_v1_enabled": False,
            "multi_agent_v2_disabled": False,
            "deepseek_model_v1": False,
            "configured_parent_v1": False,
            "current_task_v1": False,
            "current_parent_model_v1": False,
            "safe_to_spawn_send": False,
            "agent_roster": False,
            "key_file_present": False,
            "bridge_process": False,
            "bridge_abi": False,
            "bridge_on_demand": False,
            "auth_command": False,
            "localhost_authentication": False,
            "codex_auth_context": False,
            "end_to_end_inference": False,
        },
    }
    if static_code != 0 or static.get("status") != "configured":
        return 2, _failed_doctor(payload, "static_configuration", "codex_configuration_invalid", "Codex managed configuration is incomplete or inconsistent.")

    transport = _transport_payload(state, static)
    payload["transport"] = transport
    for name in (
        "multi_agent_v1_enabled",
        "multi_agent_v2_disabled",
        "deepseek_model_v1",
        "configured_parent_v1",
        "current_task_v1",
        "current_parent_model_v1",
    ):
        payload["checks"][name] = transport["checks"].get(name) is True
    payload["checks"]["safe_to_spawn_send"] = transport.get("safe_to_spawn_send") is True
    if not payload["checks"]["safe_to_spawn_send"]:
        return 2, _failed_doctor(
            payload,
            "current_task_transport",
            str(transport.get("error_code") or "current_task_v1_unconfirmed"),
            str(transport.get("message") or "Multi-Agent V1 transport is not confirmed."),
        )

    try:
        current_parent, _root_evidence = _current_root_thread(state)
        parent_bindings = _authoritative_parent_bindings(state)
        roster, _notes = _roster_path(state)
        persistent_agents = list_agents(
            roster,
            parent_thread_id=current_parent,
            parent_bindings=parent_bindings,
        )
        diagnostic_agents = list_agents(
            roster,
            parent_thread_id=current_parent,
            include_retired=True,
            include_other_parents=True,
            parent_bindings=parent_bindings,
        )
    except ManagerError as exc:
        return 2, _failed_doctor(payload, "agent_roster", exc.code, str(exc))
    payload["checks"]["agent_roster"] = True
    payload["roster_agent_count"] = len(persistent_agents)
    payload["global_diagnostic_agent_count"] = len(diagnostic_agents)
    payload["agent_operational_scope"] = "current_codex_process_only"
    payload["cross_restart_child_recovery_supported"] = False
    payload["agent_roster_purpose"] = "ownership_and_diagnostics"
    payload["agent_liveness_policy"] = "fresh_child_reply_required"
    payload["agent_context_policy"] = "fresh_reply_with_prior_verifiable_fact_required"
    payload["agent_handoff_policy"] = "verified_append_required_after_every_child_turn"
    handoff_issues = [
        item
        for item in persistent_agents
        if item.get("handoff_status") != "ready"
    ]
    payload["agent_handoff_issue_count"] = len(handoff_issues)
    if handoff_issues:
        payload["agent_handoff_warnings"] = [
            {
                "agent_id": item.get("agent_id"),
                "handoff_status": item.get("handoff_status"),
                "message": "Initialize and bind an explicit project handoff before routing new work.",
            }
            for item in handoff_issues
        ]

    key_state = credential_status(_local_key_file())
    payload["checks"]["key_file_present"] = key_state.get("status") == "credential_present"
    if not payload["checks"]["key_file_present"]:
        code = str(key_state.get("status") or "upstream_key_missing")
        return 2, _failed_doctor(payload, "upstream_key_file", code, f"OpenCode Go key file is unavailable: {_local_key_file()}")
    try:
        fixed_credential = discover_credential(_local_key_file())
    except CredentialError as exc:
        code = exc.code
        return 2, _failed_doctor(payload, "upstream_key_file", code, str(exc))
    if fixed_credential is None:
        return 2, _failed_doctor(payload, "upstream_key_file", "upstream_key_missing", f"OpenCode Go key file is unavailable: {_local_key_file()}")

    lifecycle = _lifecycle(state)
    bridge = lifecycle.status(port=PORT)
    payload["bridge"] = bridge
    payload["checks"]["bridge_process"] = bridge.get("status") == "running"
    if not payload["checks"]["bridge_process"]:
        return 2, _failed_doctor(payload, "bridge_process", "bridge_not_running", "The managed localhost bridge is not running.")
    payload["checks"]["bridge_abi"] = bridge.get("bridge_abi_compatible") is True
    if not payload["checks"]["bridge_abi"]:
        return 2, _failed_doctor(
            payload,
            "bridge_abi",
            "bridge_abi_incompatible",
            "The running localhost bridge does not expose the managed compatible bridge ABI.",
        )
    payload["checks"]["bridge_on_demand"] = bridge.get("launch_mode") == "on_demand"

    manifest = read_manifest(state.state_root)
    codex_home = Path(manifest.get("platform_home") or Path.home() / ".codex")
    try:
        parsed = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
        provider = (parsed.get("model_providers") or {}).get("opencode-go-bridge") or {}
        auth = provider.get("auth") or {}
        command = auth.get("command")
        args = auth.get("args") or []
        timeout = max(float(auth.get("timeout_ms") or 5000) / 1000.0, 1.0)
        if not isinstance(command, str) or not isinstance(args, list):
            raise ValueError("invalid auth.command")
        proc = subprocess.run(
            [command, *[str(item) for item in args]],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env={**os.environ, "CODEX_HOME": str(codex_home)},
        )
        token = proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, ValueError, tomllib.TOMLDecodeError, subprocess.SubprocessError):
        provider = {}
        token = ""
    payload["checks"]["auth_command"] = bool(token)
    if not token:
        return 2, _failed_doctor(payload, "auth_command", "auth_command_failed", "Codex auth.command did not return the localhost bridge token.")

    base_url = str(provider.get("base_url") or bridge.get("base_url") or "").rstrip("/")
    health_url = base_url[:-3] + "/health" if base_url.endswith("/v1") else base_url + "/health"
    health_status, health = _json_request(health_url, token)
    payload["checks"]["localhost_authentication"] = health_status == 200
    if health_status != 200:
        code = str(((health.get("error") or {}).get("code")) or "localhost_unreachable")
        return 2, _failed_doctor(payload, "localhost_authentication", code, "The configured auth.command could not authenticate to the localhost bridge.")

    codex_bin = str((static.get("checks") or {}).get("desktop_codex_path") or "")
    if not codex_bin:
        return 2, _failed_doctor(payload, "codex_auth_context", "codex_runtime_missing", "The Codex runtime required for the real auth.command check was not found.")
    try:
        role = make_role(OpenCodeGoProvider)
        direct_test(state, CodexPaths.from_home(codex_home, role.name), codex_bin, role)
    except ManagerError as exc:
        stage = "codex_auth_context" if exc.code == "auth_command_sandbox_failed" else "end_to_end_inference"
        return 2, _failed_doctor(payload, stage, exc.code, str(exc))
    payload["checks"]["codex_auth_context"] = True
    payload["checks"]["end_to_end_inference"] = True
    payload.update({"status": "configured", "failure_stage": None, "error_code": None})
    return 0, payload


def _purge_after_exit() -> None:
    command = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "Start-Sleep -Milliseconds 700; "
        f"Remove-Item -LiteralPath '{str(SKILL_DIR).replace(chr(39), chr(39) + chr(39))}' -Recurse -Force"
    )
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("setup", "prepare", "status", "doctor", "repair", "disable", "uninstall", "bridge", "credentials", "agents", "transport", "reinstall-prep"),
    )
    parser.add_argument(
        "bridge_action",
        nargs="?",
        choices=(
            "start",
            "status",
            "stop",
            "restart",
            "rotate-token",
            "list",
            "register",
            "successor-register",
            "retire",
            "handoff-init",
            "handoff-start",
            "handoff-check",
            "check",
        ),
    )
    parser.add_argument("--agent-id")
    parser.add_argument("--previous-agent-id")
    parser.add_argument("--stable-role")
    parser.add_argument("--scope")
    parser.add_argument("--nickname")
    parser.add_argument("--project-root")
    parser.add_argument("--turn-token")
    parser.add_argument("--after-size", type=int)
    parser.add_argument("--baseline-sha256")
    parser.add_argument("--include-retired", action="store_true")
    parser.add_argument("--all-parents", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--e2e", action="store_true", help="Run the real Codex-to-OpenCode-Go inference check.")
    parser.add_argument("--keep-skill", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    state = _state()

    if args.command == "agents":
        action = args.bridge_action or "list"
        result_code = 0
        root_thread_id, root_evidence = _current_root_thread(state)
        parent_bindings = _authoritative_parent_bindings(state)
        roster, roster_notes = _roster_path(state)
        if action == "list":
            agents = list_agents(
                roster,
                parent_thread_id=root_thread_id,
                include_retired=args.include_retired,
                include_other_parents=args.all_parents,
                parent_bindings=parent_bindings,
            )
            payload = {
                "status": "agent_roster_ready",
                "roster_file": str(roster),
                "current_parent_thread_id": root_thread_id,
                "current_parent_verified": root_evidence.get("detected") is True,
                "scope": "all_parents_diagnostic" if args.all_parents else "current_parent_only",
                "count": len(agents),
                "agents": agents,
                "liveness_policy": "fresh_child_reply_required",
                "context_policy": "fresh_reply_with_prior_verifiable_fact_required",
                "operational_scope": "current_codex_process_only",
                "cross_restart_child_recovery_supported": False,
                "roster_purpose": "ownership_and_diagnostics",
                "continuity_strategy": "project_handoff_log",
                "handoff_update_policy": "required_after_every_child_turn",
                **roster_notes,
            }
        elif action == "handoff-init":
            if not args.stable_role or not args.scope or not args.project_root:
                parser.error("agents handoff-init requires --stable-role, --scope, and --project-root")
            try:
                with _handoff_lock(args.project_root, args.stable_role, args.scope):
                    target = handoff_path(args.project_root, args.stable_role, args.scope)
                    preflight = _handoff_ownership_preflight(state, str(target), root_thread_id)
                    if preflight["blocked"]:
                        owner = preflight["owner"]
                        raise ManagerError(
                            "handoff_owned_by_other_parent",
                            "该 handoff 已被其他 Codex root 的 open Agent 占用；"
                            "不得通过修改 stable role/scope/project identity 绕过，"
                            "请与用户确认后选择 successor 或独立的新职责。",
                            {
                                "owner_agent_id": owner.get("agent_id"),
                                "owner_parent_thread_id": owner.get("parent_thread_id"),
                                "owner_stable_role": owner.get("stable_role"),
                                "owner_scope": owner.get("scope"),
                                "owner_handoff_generation": owner.get("handoff_generation"),
                                "handoff_file": str(target),
                            },
                        )
                    initialized = initialize_handoff(args.project_root, args.stable_role, args.scope)
                    turn = issue_handoff_turn(initialized["handoff_file"])
            except LockTimeoutError as exc:
                raise ManagerError(
                    "operation_in_progress",
                    "同一 handoff 操作仍在进行，请稍后重试。",
                ) from exc
            payload = {
                **initialized,
                **turn,
                "created": initialized["created"],
                "continuity_strategy": "project_handoff_log",
                "update_required_before_accepting_child_result": True,
            }
        elif action == "register":
            if not args.agent_id or not args.stable_role or not args.scope or not args.project_root:
                parser.error("agents register requires --agent-id, --stable-role, --scope, and --project-root")
            _verify_child_parent(state, args.agent_id, root_thread_id)
            try:
                with _roster_lock():
                    entry = register_agent(
                        roster,
                        args.agent_id,
                        args.stable_role,
                        args.scope,
                        root_thread_id,
                        args.project_root,
                        args.nickname,
                    )
            except LockTimeoutError as exc:
                raise _roster_mutation_timeout() from exc
            payload = {"status": "agent_registered", "roster_file": str(roster), "agent": entry}
        elif action == "successor-register":
            if (
                not args.agent_id
                or not args.previous_agent_id
                or not args.stable_role
                or not args.scope
                or not args.project_root
            ):
                parser.error(
                    "agents successor-register requires --agent-id, --previous-agent-id, "
                    "--stable-role, --scope, and --project-root"
                )
            _verify_child_parent(state, args.agent_id, root_thread_id)
            try:
                with _roster_lock():
                    entry = register_successor(
                        roster,
                        args.agent_id,
                        args.previous_agent_id,
                        args.stable_role,
                        args.scope,
                        root_thread_id,
                        args.project_root,
                        args.nickname,
                    )
            except LockTimeoutError as exc:
                raise _roster_mutation_timeout() from exc
            payload = {
                "status": "agent_successor_registered",
                "roster_file": str(roster),
                "ownership_transferred": True,
                "agent": entry,
            }
        elif action in {"handoff-start", "handoff-check"}:
            if not args.agent_id:
                parser.error(f"agents {action} requires --agent-id")
            entries = list_agents(
                roster,
                parent_thread_id=root_thread_id,
                parent_bindings=parent_bindings,
            )
            entry = next((item for item in entries if item.get("agent_id") == args.agent_id), None)
            if entry is None:
                raise ManagerError(
                    "agent_not_registered_for_current_parent",
                    "DeepSeek Agent is not registered to the current Codex root thread",
                )
            handoff_file = entry.get("handoff_file")
            if not handoff_file:
                raise ManagerError(
                    "handoff_legacy_unconfigured",
                    "This legacy Agent has no project handoff log; initialize and bind one before new work",
                )
            if action == "handoff-start":
                payload = {
                    **issue_handoff_turn(str(handoff_file)),
                    "agent_id": args.agent_id,
                    "stable_role": entry.get("stable_role"),
                    "update_required_before_accepting_child_result": True,
                }
            else:
                if args.turn_token is None or args.after_size is None or args.baseline_sha256 is None:
                    parser.error(
                        "agents handoff-check requires --agent-id, --turn-token, --after-size, "
                        "and --baseline-sha256"
                    )
                payload = {
                    **verify_handoff_update(
                        str(handoff_file),
                        args.after_size,
                        args.baseline_sha256,
                        args.turn_token,
                    ),
                    "agent_id": args.agent_id,
                }
                result_code = 0 if payload.get("updated") else 2
        elif action == "retire":
            if not args.agent_id:
                parser.error("agents retire requires --agent-id")
            try:
                with _roster_lock():
                    entry = retire_agent(roster, args.agent_id, root_thread_id)
            except LockTimeoutError as exc:
                raise _roster_mutation_timeout() from exc
            payload = {"status": "agent_retired", "roster_file": str(roster), "agent": entry}
        else:
            parser.error("unsupported agents action")
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload["status"])
        return result_code

    if args.command == "credentials":
        if (args.bridge_action or "status") != "status":
            parser.error("credentials supports only status")
        payload = credential_status(_local_key_file())
        code = 0 if payload.get("status") == "credential_present" else 2
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
        return code

    if args.command == "reinstall-prep":
        payload = _reinstall_prep(state)
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
        return 0 if payload.get("ready_for_reinstall") else 2

    lifecycle = _lifecycle(state)

    if args.command == "transport":
        if (args.bridge_action or "status") not in {"status", "check"}:
            parser.error("transport supports only check")
        payload = _transport_payload(state)
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
        return 0 if payload.get("safe_to_spawn_send") else 2

    if args.command == "prepare":
        code, payload = _prepare_for_deepseek(state)
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
        return code

    if args.command == "bridge":
        action = args.bridge_action or "status"
        if action == "status":
            payload = _runtime_payload(lifecycle)
        elif action == "start":
            payload = _ensure_bridge(state)
        elif action == "stop":
            payload = lifecycle.stop()
        elif action == "restart":
            _require_key()
            current = lifecycle.status(port=PORT)
            payload = lifecycle.restart(
                str(_bridge_state_root() / "runtime"),
                port=int(current.get("port") or PORT),
                auto_start=False,
            )
        else:
            _require_key()
            current = lifecycle.status(port=PORT)
            payload = lifecycle.rotate_token(
                str(_bridge_state_root() / "runtime"),
                port=int(current.get("port") or PORT),
                auto_start=False,
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
        return 0

    if args.command in {"setup", "repair"}:
        bridge = _ensure_bridge(state)
        bridge_json = Path(bridge.get("workdir", "")) / "bridge.json"
        command = [args.command, "--bridge-json", str(bridge_json)]
        current = _current_task_evidence(state)
        if current.get("detected") and current.get("model"):
            command.extend(["--parent-model", str(current["model"])])
        code, payload = _capture(command)
        if code == 0:
            _annotate_manifest(state, bridge)
            payload["bridge"] = bridge
            transport = _transport_payload(state)
            payload["transport"] = transport
            if not transport.get("safe_to_spawn_send"):
                payload["status"] = "configured_new_task_required"
                payload["new_task_required"] = True
                payload["message"] = transport.get("message")
        else:
            payload["bridge"] = lifecycle.status(port=PORT)
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
        return code

    if args.command in {"status", "doctor"}:
        code, payload = _doctor(state)
        payload["skill_root"] = str(SKILL_DIR.resolve())
        payload["runtime_root"] = str(RUNTIME_DIR.resolve())
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
        return code

    lifecycle.stop()
    code, payload = _capture([args.command])
    payload["bridge"] = lifecycle.status(port=PORT)
    if args.command == "uninstall" and code == 0 and not args.keep_skill:
        purge_token(SKILL_DIR / ".local")
        payload["skill_removal_scheduled"] = True
        _purge_after_exit()
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload.get("status", "unknown"))
    return code


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except ManagerError as exc:
        payload = {"status": exc.code, "message": str(exc), **exc.details}
        arguments = list(argv) if argv is not None else sys.argv[1:]
        print(json.dumps(payload, ensure_ascii=False, indent=2) if "--json" in arguments else payload["status"])
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
