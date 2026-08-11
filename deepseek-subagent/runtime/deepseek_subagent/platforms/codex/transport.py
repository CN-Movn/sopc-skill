"""Multi-Agent V1 transport gate for external DeepSeek child agents.

Static configuration is necessary but not sufficient: a Codex task keeps the
multi-agent version with which it was initialized.  The current task evidence
therefore comes from its persisted rollout ``turn_context`` and is never
inferred from config.toml alone.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any


ALLOWED_MULTI_AGENT_VERSION = "v1"
V2_TASK_ERROR = (
    "当前 Codex task 已经处于 Multi-Agent V2，不能安全地向 DeepSeek 外部 Provider 发送任务；"
    "请先让 V1 配置生效并创建新的 Codex task；只有用户明确要求时，才可在新 task 创建新的 DeepSeek Agent。"
    "旧 root 的 Agent roster 条目仅供诊断，不能被新 task 采用。"
    "repair 或 bridge restart 不能把已经初始化的当前 task 原地改回 V1。"
)
UNCONFIRMED_TASK_ERROR = (
    "无法确认当前 Codex task 正在使用 Multi-Agent V1，已拒绝向 DeepSeek 外部 Provider 发送任务；"
    "请在 V1 配置生效后创建新的 Codex task，并仅在用户明确要求时创建新的 DeepSeek Agent。"
)


def _rollout_candidates(codex_home: Path, thread_id: str) -> list[Path]:
    sessions = codex_home / "sessions"
    if not sessions.is_dir():
        return []
    return sorted(
        sessions.rglob(f"*{thread_id}.jsonl"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )


def inspect_thread_identity(codex_home: Path, thread_id: str) -> dict[str, Any]:
    """Read the persisted root/child identity for one Codex thread.

    ``session_meta.parent_thread_id`` and the nested native
    ``source.subagent.thread_spawn.parent_thread_id`` are authoritative Codex
    evidence.  A child is operable only when those fields agree.  Missing
    legacy fields stay unverified; this function never guesses parentage from
    cwd, project scope, nickname, or roster proximity.
    """

    evidence: dict[str, Any] = {
        "detected": False,
        "thread_id": thread_id,
        "parent_thread_id": None,
        "parent_verified": False,
        "thread_source": None,
        "agent_role": None,
        "agent_nickname": None,
        "model_provider": None,
        "source": None,
        "error_code": None,
    }
    for rollout in _rollout_candidates(codex_home, thread_id):
        session_meta: dict[str, Any] | None = None
        try:
            with rollout.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = record.get("payload") if isinstance(record, dict) else None
                    if (
                        record.get("type") == "session_meta"
                        and isinstance(payload, dict)
                        and payload.get("id") == thread_id
                    ):
                        session_meta = payload
                        break
        except OSError:
            continue
        if session_meta is None:
            continue

        nested_parent = None
        source_payload = session_meta.get("source")
        if isinstance(source_payload, dict):
            subagent = source_payload.get("subagent")
            if isinstance(subagent, dict):
                spawn = subagent.get("thread_spawn")
                if isinstance(spawn, dict):
                    nested_parent = spawn.get("parent_thread_id")
        direct_parent = session_meta.get("parent_thread_id")
        parents = {str(item) for item in (direct_parent, nested_parent) if item}
        parent_id = next(iter(parents)) if len(parents) == 1 else None
        thread_source = session_meta.get("thread_source")
        error_code = None
        if len(parents) > 1:
            error_code = "child_parent_evidence_conflict"
        elif thread_source == "subagent" and not parent_id:
            error_code = "child_parent_unverified"

        evidence.update(
            {
                "detected": True,
                "parent_thread_id": parent_id,
                "parent_verified": bool(parent_id) and thread_source == "subagent" and error_code is None,
                "thread_source": thread_source,
                "agent_role": session_meta.get("agent_role"),
                "agent_nickname": session_meta.get("agent_nickname"),
                "model_provider": session_meta.get("model_provider"),
                "source": str(rollout),
                "error_code": error_code,
            }
        )
        return evidence

    evidence["error_code"] = "thread_identity_not_found"
    return evidence


def inspect_current_task(codex_home: Path, thread_id: str | None = None) -> dict[str, Any]:
    """Read authoritative transport evidence for the current Codex task."""

    selected_id = thread_id or os.environ.get("CODEX_THREAD_ID")
    identity = inspect_thread_identity(codex_home, selected_id) if selected_id else {}
    evidence: dict[str, Any] = {
        "detected": False,
        "thread_id": selected_id,
        "model": None,
        "model_provider": None,
        "multi_agent_version": None,
        "source": None,
        "parent_thread_id": identity.get("parent_thread_id"),
        "parent_verified": identity.get("parent_verified", False),
        "thread_source": identity.get("thread_source"),
        "agent_role": identity.get("agent_role"),
    }
    if not selected_id:
        evidence["reason"] = "CODEX_THREAD_ID is unavailable"
        return evidence

    for rollout in _rollout_candidates(codex_home, selected_id):
        session_matches = False
        latest_context: dict[str, Any] | None = None
        session_meta: dict[str, Any] = {}
        try:
            with rollout.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = record.get("payload") if isinstance(record, dict) else None
                    if not isinstance(payload, dict):
                        continue
                    if record.get("type") == "session_meta":
                        if payload.get("id") == selected_id:
                            session_matches = True
                            session_meta = payload
                    elif record.get("type") == "turn_context":
                        latest_context = payload
        except OSError:
            continue
        if not session_matches or latest_context is None:
            continue
        evidence.update(
            {
                "detected": True,
                "model": latest_context.get("model"),
                "model_provider": latest_context.get("model_provider") or session_meta.get("model_provider"),
                "multi_agent_version": latest_context.get("multi_agent_version"),
                "source": str(rollout),
            }
        )
        return evidence

    evidence["reason"] = "matching rollout turn_context was not found"
    return evidence


def assess_v1_transport(
    codex_home: Path,
    deepseek_model: str,
    static_status: dict[str, Any],
    task_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a fail-closed assessment for current-process spawn/send operations."""

    static_checks = static_status.get("checks") or {}
    evidence = task_evidence or inspect_current_task(codex_home)
    checks: dict[str, Any] = {
        "static_configuration": static_status.get("status") == "configured",
        "multi_agent_v1_enabled": static_checks.get("desktop_multi_agent_enabled") is True,
        "multi_agent_v2_disabled": static_checks.get("desktop_multi_agent_v2_disabled") is True,
        "deepseek_model_v1": static_checks.get("role_model_uses_plaintext_v1") is True,
        "configured_parent_v1": static_checks.get("parent_uses_plaintext_v1") is True,
        "cross_provider_v1": static_checks.get("compatibility_mode_ok") is True,
        "current_task_detected": evidence.get("detected") is True,
        "current_task_v1": evidence.get("multi_agent_version") == ALLOWED_MULTI_AGENT_VERSION,
        "current_parent_model_v1": False,
    }

    catalog_path = codex_home / "models-with-deepseek.json"
    actual_model = evidence.get("model")
    try:
        config = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
        selected_catalog = Path(config.get("model_catalog_json", "")).expanduser()
        if selected_catalog.is_file():
            catalog_path = selected_catalog
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        actual_entry = next(
            (item for item in catalog.get("models", []) if item.get("slug") == actual_model),
            None,
        )
        checks["current_parent_model_v1"] = bool(actual_entry) and (
            actual_entry.get("multi_agent_version") == ALLOWED_MULTI_AGENT_VERSION
        )
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        checks["current_parent_model_v1"] = False

    safe = all(checks.values())
    error_code: str | None = None
    message = "当前 Codex task 已确认使用 Multi-Agent V1，可以安全 spawn/send DeepSeek Agent。"
    if not safe:
        if evidence.get("multi_agent_version") == "v2":
            error_code = "current_task_multi_agent_v2"
            message = V2_TASK_ERROR
        elif not evidence.get("detected") or evidence.get("multi_agent_version") != ALLOWED_MULTI_AGENT_VERSION:
            error_code = "current_task_v1_unconfirmed"
            message = UNCONFIRMED_TASK_ERROR
        elif not checks["current_parent_model_v1"]:
            error_code = "current_parent_model_not_v1"
            message = (
                f"当前 task 的实际 parent model {actual_model!r} 未被确认配置为 Multi-Agent V1；"
                "已拒绝向 DeepSeek 派发任务。请修复 V1 配置后创建新的 Codex task。"
            )
        else:
            error_code = "cross_provider_v1_not_configured"
            message = "DeepSeek cross-provider V1 配置不完整；已拒绝 spawn/send，且不会回退到 V2。"

    return {
        "status": "v1_transport_ready" if safe else "v1_transport_blocked",
        "safe_to_spawn_send": safe,
        "operation_scope": "current_codex_process_only",
        "cross_restart_child_recovery_supported": False,
        "allowed_transport": ALLOWED_MULTI_AGENT_VERSION,
        "fallback_to_v2": False,
        "repair_changes_current_task": False,
        "deepseek_model": deepseek_model,
        "current_task": evidence,
        "checks": checks,
        "error_code": error_code,
        "message": message,
    }
