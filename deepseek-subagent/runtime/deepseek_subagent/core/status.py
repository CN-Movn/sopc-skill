"""Codex + OpenCode Go 安装状态组合。"""

from __future__ import annotations

from typing import Any

from .manifest import legacy_manifest_candidates, read_manifest_with_source, upgrade_payload
from .paths import PROJECT_NAME, ProjectStatePaths


def generic_checks(state: ProjectStatePaths, backend) -> dict[str, Any]:
    manifest_payload, manifest_source, legacy_manifest = read_manifest_with_source(state.state_root)
    view = upgrade_payload(manifest_payload) if manifest_payload else {}
    checks: dict[str, Any] = {
        "project_name": PROJECT_NAME,
        "state_root": str(state.state_root),
        "manifest_exists": state.manifest.is_file() or any(
            p.is_file() for p in legacy_manifest_candidates(state.state_root)
        ),
        "manifest_legacy": legacy_manifest,
        "manifest_schema_version": manifest_payload.get("schema_version") if manifest_payload else None,
        "manifest_platform": view.get("platform") if view else None,
        "manifest_provider": view.get("provider") if view else None,
        "credential_backend": backend.id,
        "credential_backend_persistent": _persistent(backend),
        "credential_present": backend.has_key(),
    }
    return checks


def _persistent(backend) -> bool:
    return bool(backend.persistent()) if hasattr(backend, "persistent") else False


def compose_status(project_checks: dict[str, Any], adapter_checks: dict[str, Any]) -> dict[str, Any]:
    checks = {**project_checks, **adapter_checks.get("checks", {})}
    errors = list(adapter_checks.get("errors", []))
    required = adapter_checks.get("required", [])
    ready = all(checks.get(key) is True for key in required)
    forced = adapter_checks.get("status")
    status = forced if forced is not None else ("configured" if ready else "partial")
    return {
        "status": status,
        "checks": checks,
        "errors": errors,
    }
