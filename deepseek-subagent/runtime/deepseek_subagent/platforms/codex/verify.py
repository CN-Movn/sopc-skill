"""Codex 验收能力：SQLite 路由验真与 spawn_agent 原生派发验收。

期望值与直连参数全部由 AgentRoleDefinition 驱动。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from ...core.agent_role import AgentRoleDefinition
from ...core.errors import ManagerError
from ...core.paths import ProjectStatePaths
from .paths import CodexPaths

MAX_STATE_DATABASES = 32
METADATA_WAIT_SECONDS = 5.0


def query_child_metadata(
    state: ProjectStatePaths,
    codex_paths: CodexPaths,
    child_id: str,
    deadline: float | None = None,
) -> dict[str, Any] | None:
    candidates: list[tuple[float, Path]] = []
    for state_db in codex_paths.home.glob("state_*.sqlite"):
        try:
            candidates.append((state_db.stat().st_mtime, state_db))
        except OSError:
            continue
    for _, state_db in sorted(candidates, reverse=True)[:MAX_STATE_DATABASES]:
        if deadline is not None and time.monotonic() >= deadline:
            return None
        try:
            with sqlite3.connect(
                f"file:{state_db}?mode=ro",
                uri=True,
                timeout=0.05,
            ) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)").fetchall()}
                required = {"id", "model_provider", "model", "reasoning_effort", "agent_role"}
                if not required.issubset(columns):
                    continue
                row = connection.execute(
                    "SELECT model_provider, model, reasoning_effort, agent_role FROM threads WHERE id = ?",
                    (child_id,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            continue
        if row:
            return {
                "model_provider": row[0],
                "model": row[1],
                "reasoning_effort": row[2],
                "agent_role": row[3],
            }
    return None


def wait_for_child_metadata(
    state: ProjectStatePaths,
    codex_paths: CodexPaths,
    child_id: str,
    timeout_seconds: float = METADATA_WAIT_SECONDS,
    poll_interval: float = 0.2,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        metadata = query_child_metadata(state, codex_paths, child_id, deadline)
        if metadata is not None:
            return metadata
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(poll_interval, remaining))


def direct_test(state: ProjectStatePaths, codex_paths: CodexPaths, codex_bin: str, role: AgentRoleDefinition) -> dict[str, Any]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_paths.home)
    prompt = "Reply exactly DEEPSEEK_DIRECT_OK and nothing else."
    try:
        proc = subprocess.run(
            [
                codex_bin,
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--json",
                "-s",
                "read-only",
                "-C",
                str(codex_paths.home),
                "-m",
                role.model,
                "-c",
                f'model_provider="{role.provider_id}"',
                "-c",
                f'model_reasoning_effort="{role.reasoning_effort}"',
                prompt,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise ManagerError(
            "codex_end_to_end_timeout",
            "The real Codex provider path timed out during minimal DeepSeek inference.",
        ) from exc
    except OSError as exc:
        raise ManagerError(
            "codex_runtime_unavailable",
            "The discovered Codex runtime could not be executed.",
        ) from exc
    if proc.returncode != 0 or "DEEPSEEK_DIRECT_OK" not in proc.stdout:
        combined = (proc.stdout + "\n" + proc.stderr).lower()
        stable_codes = (
            "local_bridge_token_invalid",
            "upstream_key_invalid",
            "upstream_waf_blocked",
            "upstream_forbidden",
            "upstream_rate_limited",
            "upstream_service_unavailable",
            "upstream_network_error",
        )
        if "print_bridge_token.py" in combined and (
            "permission denied" in combined
            or "access is denied" in combined
            or "local bridge token is unavailable" in combined
        ):
            code = "auth_command_sandbox_failed"
        else:
            code = next((item for item in stable_codes if item in combined), "codex_end_to_end_failed")
        raise ManagerError(
            code,
            "The real Codex provider path did not complete minimal DeepSeek inference.",
        )
    return {"direct": True}


def choose_parent_model(codex_paths: CodexPaths) -> str:
    from .config import parse_toml_text
    from .models import configured_parent_model

    parsed = parse_toml_text(codex_paths.config.read_text(encoding="utf-8"))
    parent_model = configured_parent_model(parsed)
    if not parent_model:
        raise ManagerError("parent_model_unconfigured", "桌面配置中没有明确的非 DeepSeek 父模型。")
    return parent_model


def native_test(
    state: ProjectStatePaths,
    codex_paths: CodexPaths,
    codex_bin: str,
    role: AgentRoleDefinition,
) -> dict[str, Any]:
    parent_model = choose_parent_model(codex_paths)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_paths.home)
    prompt = (
        f'Use the native spawn_agent tool exactly once. Set agent_type to {role.name} and fork_turns to none. '
        'Give it this task: Reply exactly NATIVE_DEEPSEEK_OK. '
        "Then wait for that subagent and return only its final response."
    )
    proc = subprocess.run(
        [
            codex_bin,
            "exec",
            "--skip-git-repo-check",
            "--json",
            "-s",
            "read-only",
            "-C",
            str(codex_paths.home),
            "-m",
            parent_model,
            prompt,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    if proc.returncode != 0:
        raise ManagerError(
            "native_test_failed",
            "新 Codex 任务中的原生 spawn_agent 测试失败。",
            {"stderr": proc.stderr[-1200:]},
        )
    child_ids: list[str] = []
    child_messages: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "collab_tool_call"
            and item.get("tool") == "spawn_agent"
        ):
            child_ids.extend(item.get("receiver_thread_ids") or [])
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "collab_tool_call"
            and item.get("tool") == "wait"
        ):
            for receiver_id, cstate in (item.get("agents_states") or {}).items():
                if not isinstance(cstate, dict):
                    continue
                message = cstate.get("message")
                if cstate.get("status") == "completed" and isinstance(message, str):
                    child_messages[receiver_id] = message.strip()
    child_id = child_ids[0] if len(child_ids) == 1 else None
    child_message = child_messages.get(child_id) if child_id else None
    metadata = wait_for_child_metadata(state, codex_paths, child_id) if child_id else None
    expected = role.summary()
    if len(child_ids) != 1 or child_message != "NATIVE_DEEPSEEK_OK" or metadata != expected:
        raise ManagerError(
            "native_route_mismatch",
            "原生子 Agent 路由验收证据不完整或不符合配置。",
            {
                "child_ids": child_ids,
                "child_message": child_message,
                "metadata": metadata,
                "expected": expected,
            },
        )
    return {"desktop_fresh_session_native": True, "child_id": child_id, **expected}
