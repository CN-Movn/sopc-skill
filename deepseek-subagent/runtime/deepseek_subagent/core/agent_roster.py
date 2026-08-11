"""Durable DeepSeek child-agent ownership and diagnostic roster.

The native Codex multi-agent registry is process-local.  This roster preserves
only non-secret ownership metadata for isolation and diagnostics; it is not an
operability registry and never offers a cross-process recovery action.  It
never stores prompts, source text, tool output, credentials, or tokens.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic import atomic_write
from .agent_handoff import initialize_handoff
from .errors import ManagerError

ROSTER_SCHEMA_VERSION = 3
LEGACY_ROSTER_SCHEMA_VERSION = 1
PRE_HANDOFF_ROSTER_SCHEMA_VERSION = 2
ACTIVE_STATE = "open"
RETIRED_STATE = "retired"
SUPERSEDED_STATE = "superseded"
UNVERIFIED_PARENT = "legacy_parent_unverified"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_agent_id(agent_id: str) -> str:
    value = str(agent_id or "").strip()
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ManagerError("agent_id_invalid", f"Invalid DeepSeek Agent id: {value!r}") from exc
    return str(parsed)


def _required_text(value: str, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ManagerError("agent_roster_field_missing", f"Agent roster field is required: {field}")
    if len(text) > maximum:
        raise ManagerError("agent_roster_field_too_long", f"Agent roster field is too long: {field}")
    return text


def _optional_text(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise ManagerError("agent_roster_field_too_long", "Agent roster nickname is too long")
    return text


def read_roster(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": ROSTER_SCHEMA_VERSION, "agents": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagerError("agent_roster_invalid", f"DeepSeek Agent roster cannot be parsed: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") not in {
        LEGACY_ROSTER_SCHEMA_VERSION,
        PRE_HANDOFF_ROSTER_SCHEMA_VERSION,
        ROSTER_SCHEMA_VERSION,
    }:
        raise ManagerError("agent_roster_schema_unsupported", "DeepSeek Agent roster schema is unsupported")
    agents = payload.get("agents")
    if not isinstance(agents, list) or not all(isinstance(item, dict) for item in agents):
        raise ManagerError("agent_roster_invalid", "DeepSeek Agent roster is missing its agents list")
    if payload.get("schema_version") == LEGACY_ROSTER_SCHEMA_VERSION:
        payload["agents"] = [
                {
                    **item,
                    "parent_thread_id": None,
                    "parent_evidence": UNVERIFIED_PARENT,
                }
                for item in agents
            ]
    payload["schema_version"] = ROSTER_SCHEMA_VERSION
    return payload


def _write_roster(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write(path, encoded)


def _handoff_identity(value: str | Path | None) -> str:
    if not value:
        return ""
    return str(Path(value).expanduser().resolve()).casefold()


def _handoff_generation(item: dict[str, Any]) -> int:
    try:
        value = int(item.get("handoff_generation") or 1)
    except (TypeError, ValueError):
        value = 1
    return max(value, 1)


def register_agent(
    path: Path,
    agent_id: str,
    stable_role: str,
    scope: str,
    parent_thread_id: str,
    project_root: str | Path,
    nickname: str | None = None,
    parent_evidence: str = "rollout_session_meta",
    handoff_root: str | Path | None = None,
) -> dict[str, Any]:
    normalized_id = _validate_agent_id(agent_id)
    role = _required_text(stable_role, "stable_role", 200)
    normalized_scope = _required_text(scope, "scope", 2000)
    normalized_parent = _validate_agent_id(parent_thread_id)
    normalized_parent_evidence = _required_text(parent_evidence, "parent_evidence", 200)
    normalized_nickname = _optional_text(nickname, 200)
    payload = read_roster(path)
    now = _now()
    agents = payload["agents"]
    current = next((item for item in agents if item.get("agent_id") == normalized_id), None)
    if current is not None and current.get("parent_thread_id") not in {None, normalized_parent}:
        raise ManagerError(
            "agent_parent_conflict",
            "DeepSeek Agent is already registered to a different Codex root thread",
        )
    handoff = initialize_handoff(project_root, role, normalized_scope, root=handoff_root)
    target_handoff = _handoff_identity(handoff["handoff_file"])
    active_owner = next(
        (
            item
            for item in agents
            if item.get("agent_id") != normalized_id
            and item.get("state") == ACTIVE_STATE
            and _handoff_identity(item.get("handoff_file")) == target_handoff
        ),
        None,
    )
    if active_owner is not None:
        other_parent = active_owner.get("parent_thread_id") != normalized_parent
        raise ManagerError(
            "handoff_owned_by_other_parent" if other_parent else "handoff_active_owner_conflict",
            (
                "The handoff is owned by an open Agent from another Codex root; "
                "normal registration cannot take over its append lease"
                if other_parent
                else "Another open Agent in the current root already owns this handoff; "
                "serialize the work or use the explicit successor workflow after authorization"
            ),
            {
                "agent_id": active_owner.get("agent_id"),
                "owner_parent_thread_id": active_owner.get("parent_thread_id"),
                "handoff_file": handoff["handoff_file"],
            },
        )
    previous_generation = max(
        (
            _handoff_generation(item)
            for item in agents
            if _handoff_identity(item.get("handoff_file")) == target_handoff
            and item.get("agent_id") != normalized_id
        ),
        default=0,
    )
    if current is None:
        current = {
            "agent_id": normalized_id,
            "created_at": now,
        }
        agents.append(current)
    same_open_lease = (
        current.get("state") == ACTIVE_STATE
        and _handoff_identity(current.get("handoff_file")) == target_handoff
    )
    current_history_generation = (
        _handoff_generation(current)
        if _handoff_identity(current.get("handoff_file")) == target_handoff
        else 0
    )
    generation = (
        _handoff_generation(current)
        if same_open_lease
        else max(previous_generation, current_history_generation) + 1
    )
    current.update(
        {
            "stable_role": role,
            "scope": normalized_scope,
            "parent_thread_id": normalized_parent,
            "parent_evidence": normalized_parent_evidence,
            "nickname": normalized_nickname,
            "handoff_file": handoff["handoff_file"],
            "handoff_key": handoff["handoff_key"],
            "handoff_schema_version": handoff["handoff_schema_version"],
            "handoff_generation": generation,
            "state": ACTIVE_STATE,
            "updated_at": now,
        }
    )
    agents.sort(key=lambda item: (str(item.get("stable_role") or ""), str(item.get("agent_id") or "")))
    _write_roster(path, payload)
    return {**current, "handoff_created": handoff["created"]}


def register_successor(
    path: Path,
    agent_id: str,
    previous_agent_id: str,
    stable_role: str,
    scope: str,
    parent_thread_id: str,
    project_root: str | Path,
    nickname: str | None = None,
    parent_evidence: str = "rollout_session_meta",
    handoff_root: str | Path | None = None,
) -> dict[str, Any]:
    """Atomically transfer one handoff append lease to an authorized successor."""

    normalized_id = _validate_agent_id(agent_id)
    previous_id = _validate_agent_id(previous_agent_id)
    if normalized_id == previous_id:
        raise ManagerError("handoff_successor_same_agent", "A successor must have a new Agent id")
    role = _required_text(stable_role, "stable_role", 200)
    normalized_scope = _required_text(scope, "scope", 2000)
    normalized_parent = _validate_agent_id(parent_thread_id)
    normalized_parent_evidence = _required_text(parent_evidence, "parent_evidence", 200)
    normalized_nickname = _optional_text(nickname, 200)
    payload = read_roster(path)
    agents = payload["agents"]
    previous = next((item for item in agents if item.get("agent_id") == previous_id), None)
    if previous is None:
        raise ManagerError("handoff_previous_owner_not_found", "The previous handoff owner is not registered")
    if previous.get("state") != ACTIVE_STATE:
        raise ManagerError("handoff_previous_owner_not_open", "The previous handoff owner is not open")

    handoff = initialize_handoff(project_root, role, normalized_scope, root=handoff_root)
    target_handoff = _handoff_identity(handoff["handoff_file"])
    if _handoff_identity(previous.get("handoff_file")) != target_handoff:
        raise ManagerError(
            "handoff_successor_identity_mismatch",
            "The requested successor role, scope, and project root do not identify the previous owner's handoff",
        )
    competing_owner = next(
        (
            item
            for item in agents
            if item.get("state") == ACTIVE_STATE
            and item.get("agent_id") not in {previous_id, normalized_id}
            and _handoff_identity(item.get("handoff_file")) == target_handoff
        ),
        None,
    )
    if competing_owner is not None:
        raise ManagerError(
            "handoff_multiple_open_owners",
            "The roster already contains another open owner for this handoff; no transfer was performed",
            {"agent_id": competing_owner.get("agent_id"), "handoff_file": handoff["handoff_file"]},
        )
    current = next((item for item in agents if item.get("agent_id") == normalized_id), None)
    if current is not None and current.get("parent_thread_id") not in {None, normalized_parent}:
        raise ManagerError(
            "agent_parent_conflict",
            "The successor Agent is already registered to a different Codex root thread",
        )

    now = _now()
    previous_generation = _handoff_generation(previous)
    previous.update(
        {
            "state": SUPERSEDED_STATE,
            "superseded_by": normalized_id,
            "superseded_at": now,
            "updated_at": now,
        }
    )
    if current is None:
        current = {"agent_id": normalized_id, "created_at": now}
        agents.append(current)
    current.update(
        {
            "stable_role": role,
            "scope": normalized_scope,
            "parent_thread_id": normalized_parent,
            "parent_evidence": normalized_parent_evidence,
            "nickname": normalized_nickname,
            "handoff_file": handoff["handoff_file"],
            "handoff_key": handoff["handoff_key"],
            "handoff_schema_version": handoff["handoff_schema_version"],
            "handoff_generation": previous_generation + 1,
            "successor_of": previous_id,
            "state": ACTIVE_STATE,
            "updated_at": now,
        }
    )
    agents.sort(key=lambda item: (str(item.get("stable_role") or ""), str(item.get("agent_id") or "")))
    _write_roster(path, payload)
    return {**current, "handoff_created": handoff["created"], "previous_owner_state": SUPERSEDED_STATE}


def retire_agent(path: Path, agent_id: str, parent_thread_id: str) -> dict[str, Any]:
    normalized_id = _validate_agent_id(agent_id)
    normalized_parent = _validate_agent_id(parent_thread_id)
    payload = read_roster(path)
    current = next((item for item in payload["agents"] if item.get("agent_id") == normalized_id), None)
    if current is None:
        raise ManagerError("agent_not_registered", f"DeepSeek Agent is not registered: {normalized_id}")
    if current.get("parent_thread_id") != normalized_parent:
        raise ManagerError(
            "agent_parent_mismatch",
            "The DeepSeek Agent does not belong to the current Codex root thread",
        )
    current["state"] = RETIRED_STATE
    current["updated_at"] = _now()
    _write_roster(path, payload)
    return dict(current)


def list_agents(
    path: Path,
    parent_thread_id: str | None,
    include_retired: bool = False,
    include_other_parents: bool = False,
    parent_bindings: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    agents = [dict(item) for item in read_roster(path)["agents"]]
    bindings = parent_bindings or {}
    for item in agents:
        binding = bindings.get(str(item.get("agent_id") or ""))
        if item.get("parent_thread_id") is None and binding:
            item["parent_thread_id"] = binding.get("parent_thread_id")
            item["parent_evidence"] = binding.get("parent_evidence")
    if not include_retired:
        agents = [item for item in agents if item.get("state") == ACTIVE_STATE]
    if not include_other_parents:
        agents = [item for item in agents if item.get("parent_thread_id") == parent_thread_id]
    for item in agents:
        parent_match = bool(parent_thread_id) and item.get("parent_thread_id") == parent_thread_id
        item["parent_match"] = parent_match
        item["owned_by_current_parent"] = parent_match and item.get("state") == ACTIVE_STATE
        handoff_file = item.get("handoff_file")
        if handoff_file:
            item["handoff_status"] = "ready" if Path(str(handoff_file)).is_file() else "missing"
        else:
            item["handoff_status"] = "legacy_unconfigured"
    return agents
