"""Codex 专用 Adapter。

负责 DeepSeek.toml、Codex config.toml、模型目录、manifest、事务、回滚，
以及固定的 cross-provider-v1 兼容设置。模型服务固定为 OpenCode Go。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ...core.agent_role import AgentRoleDefinition
from ...core.atomic import atomic_write, sha256_bytes
from ...core.errors import ManagerError
from ...core.manifest import read_manifest_with_source, write_manifest
from ...core.paths import PROJECT_NAME, ProjectStatePaths
from ...core.provider import ProviderAuthSpec, ProviderDefinition
from ...core.transaction import make_backup, restore_backup
from ...core.tomlutil import toml_unescape
from . import verify
from .agents import expected_agent_text
from .config import (
    DESKTOP_MULTI_AGENT_V1,
    DESKTOP_MULTI_AGENT_V2,
    LEGACY_PROVIDER_BEGIN,
    LEGACY_PROVIDER_END,
    LEGACY_ROLE_BEGIN,
    LEGACY_ROLE_END,
    PROVIDER_BEGIN,
    PROVIDER_END,
    ROLE_BEGIN,
    ROLE_END,
    managed_provider_block,
    parse_toml_text,
    provider_conflicts,
    remove_marked_block,
    remove_managed_blocks,
    remove_table_bool_if_value,
    remove_toml_table,
    remove_top_level_key_if_value,
    set_table_bool,
    set_top_level_key,
    top_level_key,
)
from .models import (
    MANAGED_MODEL_MULTI_AGENT_VERSION,
    PARENT_MULTI_AGENT_VERSION,
    clone_catalog,
    configured_parent_model,
    load_base_catalog,
    merged_catalog,
    restore_managed_model_versions,
    restore_previous_parent,
)
from .paths import CodexPaths
from .runtime import CodexRuntimeDetector


class CodexAdapter:
    id = "codex"
    name = "Codex"
    adapter_version = 1
    compatibility_mode = "cross-provider-v1"

    def capability(self) -> dict[str, Any]:
        return {
            "implemented": True,
            "status": "available",
            "compatibility_mode": self.compatibility_mode,
            "runtime_candidates": [str(p) for p in CodexRuntimeDetector.candidates()],
        }

    def resolve_paths(self, platform_home: str | None = None, role: AgentRoleDefinition | None = None) -> CodexPaths:
        return CodexPaths.resolve(platform_home, (role.name,) if role else ("DeepSeek",))

    def runtime_path(self, platform_home: str | None = None) -> str | None:
        return CodexRuntimeDetector.find()

    def require_runtime(self, platform_home: str | None = None) -> str:
        runtime = self.runtime_path(platform_home)
        if runtime is None:
            raise ManagerError(
                "desktop_codex_missing",
                "没有找到 Codex 桌面应用内置运行时。请先安装或启动桌面应用，或设置 CODEX_DESKTOP_BIN。",
            )
        return runtime

    def _read_manifest(self, state: ProjectStatePaths) -> tuple[dict, Path | None, bool]:
        return read_manifest_with_source(state.state_root)

    def _managed_manifest(self, state: ProjectStatePaths) -> dict[str, Any]:
        payload, _source, _legacy = self._read_manifest(state)
        if not payload:
            raise ManagerError("not_managed", "没有找到本项目的管理记录，拒绝修改现有配置。")
        recorded = payload.get("platform")
        if recorded and recorded != self.id:
            raise ManagerError(
                "platform_mismatch",
                f"manifest 记录了不受支持的旧宿主 {recorded}；当前版本只管理 Codex 安装。",
            )
        return payload

    def _resolve_install_home(self, manifest: dict[str, Any], platform_home: str | None) -> str | None:
        recorded = manifest.get("platform_home")
        if recorded:
            if platform_home and str(Path(platform_home).expanduser().resolve()) != recorded:
                raise ManagerError(
                    "platform_home_mismatch",
                    f"manifest 记录的安装目录是 {recorded}，显式指定的 {platform_home} 不一致；"
                    "如确需操作其他安装，请使用 manifest 记录的平台目录。",
                )
            return recorded
        return platform_home

    def _manifest_backend(self, manifest: dict[str, Any], fallback):
        # 旧 manifest 可能记录已移除的凭据后端；当前产品仅使用本地桥认证。
        return _InertBackend()

    def _role_names(self, manifest: dict[str, Any]) -> list[str]:
        return list(manifest.get("roles") or ["DeepSeek"])

    def _role_details(self, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return manifest.get("role_details") or {}

    def _role_managed(self, manifest: dict[str, Any], role: str) -> bool:
        details = self._role_details(manifest).get(role) or {}
        if "managed" in details:
            return bool(details["managed"])
        return bool(manifest.get("managed_agent_file"))

    def _role_sha(self, manifest: dict[str, Any], role: str) -> str | None:
        details = self._role_details(manifest).get(role) or {}
        if details.get("sha256"):
            return details["sha256"]
        return manifest.get("agent_sha256")

    def _cleanup_managed_config(
        self,
        text: str,
        manifest: dict[str, Any],
        codex_paths: CodexPaths,
        field_conflicts: list[str],
    ) -> str:
        if manifest.get("managed_provider_block"):
            text = remove_marked_block(text, PROVIDER_BEGIN, PROVIDER_END)
            text = remove_marked_block(text, LEGACY_PROVIDER_BEGIN, LEGACY_PROVIDER_END)
        catalog_field = self._managed_field(manifest, "model_catalog_json", codex_paths)
        if catalog_field is not None:
            current = top_level_key(text, "model_catalog_json")
            current = toml_unescape(current) if current is not None else None
            installed = catalog_field.get("installed")
            previous = catalog_field.get("previous")
            if current == installed:
                if previous is None:
                    text = remove_top_level_key_if_value(text, "model_catalog_json", installed)
                else:
                    text = set_top_level_key(text, "model_catalog_json", previous)
            elif current is not None and current != previous:
                field_conflicts.append("model_catalog_json")
        v2_field = self._managed_field(manifest, "features.multi_agent_v2", codex_paths)
        if v2_field is not None:
            text, v2_status = self._restore_feature_bool(text, "multi_agent_v2", v2_field)
            if v2_status == "conflict":
                field_conflicts.append("features.multi_agent_v2")
        v1_field = self._managed_field(manifest, "features.multi_agent", codex_paths)
        if v1_field is not None:
            text, v1_status = self._restore_feature_bool(text, "multi_agent", v1_field)
            if v1_status == "conflict":
                field_conflicts.append("features.multi_agent")
        return text

    def _disabled_status(
        self,
        state: ProjectStatePaths,
        backend,
        codex_paths: CodexPaths,
        manifest_payload: dict[str, Any],
    ) -> dict[str, Any]:
        conflicts = self._verify_against_state(manifest_payload, codex_paths)
        valid = not conflicts
        role_checks = self._role_checks(manifest_payload, codex_paths, self._default_role(None))
        checks: dict[str, Any] = {
            "installation_state": "disabled",
            "disabled_state_valid": valid,
            "disabled_state_conflicts": conflicts,
            "config_state": manifest_payload.get("config_state"),
            "catalog_state": manifest_payload.get("catalog_state"),
            "safe_to_uninstall_catalog": manifest_payload.get("safe_to_uninstall_catalog"),
            "config_exists": codex_paths.config.is_file(),
            "catalog_exists": codex_paths.catalog.is_file(),
            "agent_exists": codex_paths.agent.is_file(),
            "credential_backend": backend.id,
            "credential_present": backend.has_key(),
            "manifest_exists": True,
            "role_checks": role_checks,
            "role_checks_valid": all(item["valid"] for item in role_checks.values()) if role_checks else True,
            "adapter_platform": self.id,
            "adapter_implemented": True,
        }
        return {
            "status": "disabled" if valid else "partial",
            "checks": checks,
            "errors": list(conflicts),
            "required": [],
        }

    def _role_checks(
        self,
        manifest: dict[str, Any],
        codex_paths: CodexPaths,
        fallback_role: AgentRoleDefinition,
    ) -> dict[str, dict[str, Any]]:
        roles = manifest.get("roles") or [fallback_role.name]
        role_details = manifest.get("role_details") or {}
        disabled = (manifest.get("installation_state") or "active") == "disabled"
        checks: dict[str, dict[str, Any]] = {}
        for role_name in roles:
            details = role_details.get(role_name) or {}
            managed = details.get("managed") if "managed" in details else bool(manifest.get("managed_agent_file"))
            expected = details.get("sha256") or manifest.get("agent_sha256")
            agent_path = codex_paths.agent_for(role_name)
            exists = agent_path.is_file()
            valid = False
            if disabled and managed:
                valid = not exists
            elif exists:
                if managed and expected:
                    valid = sha256_bytes(agent_path.read_bytes()) == expected
                else:
                    valid = True
            elif not managed:
                valid = True
            checks[role_name] = {
                "path": str(agent_path),
                "exists": exists,
                "managed": managed,
                "valid": valid,
            }
        return checks

    def compatible_existing(
        self,
        parsed: dict[str, Any],
        codex_paths: CodexPaths,
        provider: ProviderDefinition,
        auth_spec: ProviderAuthSpec,
        role: AgentRoleDefinition,
    ) -> tuple[bool, list[str]]:
        issues: list[str] = []
        issues.extend(
            provider_conflicts((parsed.get("model_providers") or {}).get(provider.platform_id()), provider, auth_spec)
        )
        agent = (parsed.get("agents") or {}).get(role.name)
        if agent:
            if set(agent) - {"description", "config_file"}:
                issues.append(f"agents.{role.name}")
            if Path(agent.get("config_file", "")).expanduser() != codex_paths.agent_for(role.name):
                issues.append(f"agents.{role.name}.config_file")
        return not issues, issues

    def status(
        self,
        state: ProjectStatePaths,
        backend,
        role: AgentRoleDefinition | None = None,
        provider: ProviderDefinition | None = None,
        platform_home: str | None = None,
    ) -> dict[str, Any]:
        role = role or self._default_role(provider)
        codex_paths = self.resolve_paths(platform_home, role)
        manifest_payload = read_manifest_with_source(state.state_root)[0]
        bridge_managed = manifest_payload.get("credential_backend") == "bridge" or bool(manifest_payload.get("experimental"))
        if (manifest_payload.get("installation_state") or "active") == "disabled":
            return self._disabled_status(state, backend, codex_paths, manifest_payload)
        checks: dict[str, Any] = {
            "config_exists": codex_paths.config.is_file(),
            "catalog_exists": codex_paths.catalog.is_file(),
            "agent_exists": codex_paths.agent.is_file(),
            "credential_backend": "bridge",
            "credential_present": True,
            "manifest_exists": state.manifest.is_file() or bool(
                read_manifest_with_source(state.state_root)[2]
            ),
        }
        errors: list[str] = []
        parsed: dict[str, Any] = {}
        if codex_paths.config.is_file():
            try:
                parsed = parse_toml_text(codex_paths.config.read_text(encoding="utf-8"))
                checks["config_valid"] = True
            except ManagerError as exc:
                checks["config_valid"] = False
                errors.append(str(exc))
        provider_def = provider or self._default_provider()
        configured = (parsed.get("model_providers") or {}).get(provider_def.platform_id())
        if bridge_managed:
            expected_auth = manifest_payload.get("bridge_auth") or manifest_payload.get("experimental_auth") or {}
            expected_base_url = manifest_payload.get("bridge_base_url") or manifest_payload.get("experimental_base_url") or provider_def.base_url
            provider_ok = bool(configured) and (
                (configured.get("auth") or {}) == expected_auth
                and configured.get("name") == provider_def.name
                and configured.get("base_url") == expected_base_url
                and configured.get("wire_api") == provider_def.wire_api
            )
        else:
            auth_spec = backend.auth_spec()
            provider_ok = bool(configured) and not provider_conflicts(configured, provider_def, auth_spec)
        agent = (parsed.get("agents") or {}).get(role.name)
        checks["provider_registered"] = bool(configured)
        checks["provider_valid"] = provider_ok
        checks["agent_discovery"] = "standalone"
        checks["legacy_role_registration_present"] = bool(agent)
        checks["legacy_role_registration_absent"] = not bool(agent)
        checks["catalog_selected"] = Path(parsed.get("model_catalog_json", "")).expanduser() == codex_paths.catalog
        checks["desktop_multi_agent"] = (parsed.get("features") or {}).get("multi_agent")
        checks["desktop_multi_agent_enabled"] = checks["desktop_multi_agent"] is DESKTOP_MULTI_AGENT_V1
        checks["desktop_multi_agent_v2"] = (parsed.get("features") or {}).get("multi_agent_v2")
        checks["desktop_multi_agent_v2_disabled"] = checks["desktop_multi_agent_v2"] is DESKTOP_MULTI_AGENT_V2
        parent_model = configured_parent_model(parsed)
        checks["parent_model"] = parent_model
        checks["parent_model_configured"] = bool(parent_model)
        if codex_paths.catalog.is_file():
            try:
                data = json.loads(codex_paths.catalog.read_text(encoding="utf-8"))
                checks["model_registered"] = any(item.get("slug") == role.model for item in data.get("models", []))
                parent_entry = next(
                    (item for item in data.get("models", []) if parent_model and item.get("slug") == parent_model),
                    None,
                )
                checks["parent_model_multi_agent_version"] = (
                    parent_entry.get("multi_agent_version") if parent_entry else None
                )
                checks["parent_uses_plaintext_v1"] = (
                    checks["parent_model_multi_agent_version"] == PARENT_MULTI_AGENT_VERSION
                )
                role_entry = next(
                    (item for item in data.get("models", []) if item.get("slug") == role.model),
                    None,
                )
                checks["role_model_multi_agent_version"] = (
                    role_entry.get("multi_agent_version") if role_entry else None
                )
                checks["role_model_uses_plaintext_v1"] = (
                    checks["role_model_multi_agent_version"] == MANAGED_MODEL_MULTI_AGENT_VERSION
                )
                installed_managed = manifest_payload.get("managed_model_installed_multi_agent_versions") or {}
                checks["managed_models_installed"] = bool(installed_managed)
                checks["managed_models_v1_ok"] = all(
                    checks["role_model_multi_agent_version"] == MANAGED_MODEL_MULTI_AGENT_VERSION
                    for slug in installed_managed
                    if slug == role.model
                )
            except (OSError, json.JSONDecodeError):
                checks["model_registered"] = False
                checks["parent_uses_plaintext_v1"] = False
                checks["role_model_uses_plaintext_v1"] = False
                errors.append("模型目录无法解析。")
        else:
            checks["model_registered"] = False
            checks["parent_uses_plaintext_v1"] = False
            checks["role_model_uses_plaintext_v1"] = False
        checks["compatibility_mode"] = manifest_payload.get("compatibility_mode")
        checks["compatibility_mode_ok"] = checks["compatibility_mode"] == self.compatibility_mode
        checks["agent_content_valid"] = codex_paths.agent.is_file() and codex_paths.agent.read_text(encoding="utf-8") == expected_agent_text(role)

        role_checks = self._role_checks(manifest_payload, codex_paths, role)
        checks["role_checks"] = role_checks
        checks["role_checks_valid"] = all(item["valid"] for item in role_checks.values()) if role_checks else True

        runtime = self.runtime_path(platform_home)
        checks["desktop_codex_detected"] = runtime is not None
        if runtime:
            try:
                checks["desktop_codex_path"] = runtime
                checks["desktop_codex_version"] = CodexRuntimeDetector.version(runtime)
            except ManagerError as exc:
                # Runtime discovery is authoritative for E2E execution. Version
                # reporting is diagnostic only and must not hide a usable binary.
                checks["desktop_codex_version_error"] = exc.code
        checks["adapter_platform"] = self.id
        checks["adapter_implemented"] = True
        checks["adapter_compatibility_mode"] = self.compatibility_mode
        required = (
            "config_valid",
            "provider_valid",
            "catalog_selected",
            "model_registered",
            "parent_model_configured",
            "parent_uses_plaintext_v1",
            "role_model_uses_plaintext_v1",
            "compatibility_mode_ok",
            "desktop_multi_agent_enabled",
            "desktop_multi_agent_v2_disabled",
            "agent_content_valid",
            "role_checks_valid",
            "credential_present",
            "manifest_exists",
        )
        ready = all(checks.get(key) is True for key in required)
        return {"status": "configured" if ready else "partial", "checks": checks, "errors": errors, "required": required}

    def install_bridge(
        self,
        state: ProjectStatePaths,
        codex_paths: CodexPaths,
        role: AgentRoleDefinition,
        provider: ProviderDefinition,
        catalog_payload: dict[str, Any],
        codex_auth: dict[str, Any],
        bridge_base_url: str,
    ) -> dict[str, Any]:
        """事务性安装固定的 Codex + OpenCode Go 本地桥配置。"""

        config_text = codex_paths.config.read_text(encoding="utf-8") if codex_paths.config.is_file() else ""
        parsed = parse_toml_text(config_text) if config_text.strip() else {}
        previous_manifest = read_manifest_with_source(state.state_root)[0]
        unmanaged_config = remove_managed_blocks(config_text)
        unmanaged_parsed = parse_toml_text(unmanaged_config) if unmanaged_config.strip() else {}
        selected_before = parsed.get("model_catalog_json") == str(codex_paths.catalog)
        previous_schema = previous_manifest.get("schema_version", 1) if previous_manifest else 2
        previous_selection_managed = bool(previous_manifest.get("managed_catalog_selection"))
        if previous_manifest and previous_schema < 4 and selected_before:
            previous_selection_managed = True
        managed_catalog_selection = previous_selection_managed or not selected_before
        previous_catalog_value = (
            previous_manifest.get("previous_model_catalog_json")
            if previous_selection_managed and selected_before
            else parsed.get("model_catalog_json")
        )
        previous_multi_agent = (parsed.get("features") or {}).get("multi_agent")
        previous_multi_agent_v2 = (parsed.get("features") or {}).get("multi_agent_v2")
        catalog_preexisted = bool(previous_manifest.get("catalog_preexisted", codex_paths.catalog.is_file()))

        targets = (*codex_paths.transaction_targets(), state.manifest)
        backup = make_backup(state, targets, new_modes={codex_paths.agent: 0o644})
        try:
            if provider.platform_id() not in (unmanaged_parsed.get("model_providers") or {}):
                block = managed_provider_block(
                    provider,
                    ProviderAuthSpec(kind="local_bridge", installable=True),
                    auth_config=codex_auth,
                    base_url_override=bridge_base_url,
                )
                new_config = unmanaged_config.rstrip() + "\n" + block
            else:
                new_config = unmanaged_config
            new_config = set_top_level_key(new_config, "model_catalog_json", str(codex_paths.catalog))
            new_config = set_table_bool(new_config, "features", "multi_agent", True)
            new_config = set_table_bool(new_config, "features", "multi_agent_v2", False)
            parse_toml_text(new_config)
            json.loads(json.dumps(catalog_payload))

            catalog_bytes = (json.dumps(catalog_payload, ensure_ascii=False, indent=2) + "\n").encode()
            atomic_write(codex_paths.catalog, catalog_bytes)
            expected_agent = expected_agent_text(role).encode()
            if not codex_paths.agent.is_file():

                atomic_write(codex_paths.agent, expected_agent, mode=0o644)
            elif codex_paths.agent.read_bytes() != expected_agent:
                # 受管 Agent 文件（项目维护）内容与当前角色不一致时事务性更新
                atomic_write(codex_paths.agent, expected_agent, mode=0o644)
            atomic_write(codex_paths.config, new_config.encode())

            agent_sha = sha256_bytes(expected_agent_text(role).encode())
            manifest = {
                "schema_version": 4,
                "project": PROJECT_NAME,
                "platform": self.id,
                "platform_home": str(codex_paths.home),
                "provider": provider.id,
                "model": role.model,
                "credential_backend": "bridge",
                "installation_state": "active",
                "config_state": "trusted",
                "catalog_state": "trusted",
                "safe_to_uninstall_catalog": True,
                "bridge_auth": dict(codex_auth),
                "bridge_base_url": bridge_base_url,
                "managed_config_fields": {
                    "model_catalog_json": {
                        "installed": str(codex_paths.catalog),
                        "previous": previous_catalog_value,
                    },
                    "features.multi_agent_v2": {
                        "installed": False,
                        "previous": previous_multi_agent_v2,
                    },
                    "features.multi_agent": {
                        "installed": True,
                        "previous": previous_multi_agent,
                    },
                },
                "managed_multi_agent": True,
                "managed_multi_agent_v2": True,
                "config_cleanup_state": "clean",
                "safe_to_finalize_uninstall": True,
                "roles": [role.name],
                "role_details": {
                    role.name: {
                        "path": str(codex_paths.agent),
                        "sha256": agent_sha,
                        "managed": True,
                        "preexisted": False,
                    }
                },
                "compatibility_mode": self.compatibility_mode,
                "allowed_multi_agent_versions": ["v1"],
                "multi_agent_fallback": False,
                "adapter_version": self.adapter_version,
                "installed_at": datetime.now().isoformat(timespec="seconds"),
                "backup": str(backup),
                "baseline_backup": previous_manifest.get("baseline_backup"),
                "previous_model_catalog_json": previous_catalog_value,
                "managed_catalog_selection": managed_catalog_selection,
                "managed_provider_block": True,
                "managed_agent_file": True,
                "catalog_preexisted": catalog_preexisted,
                "catalog_original_backup": _find_backup_copy(backup, codex_paths.catalog),
                "agent_preexisted": False,
                "legacy_role_block_removed": False,
                "adopted_existing": False,
                "config_sha256": sha256_bytes(new_config.encode()),
                "catalog_sha256": sha256_bytes(catalog_bytes),
                "agent_sha256": agent_sha,
            }
            write_manifest(state.state_root, manifest)
            return {"backup": str(backup), "adopted_existing": False}
        except Exception:
            restore_backup(backup, targets)
            raise

    def disable(self, state: ProjectStatePaths, backend, platform_home: str | None = None) -> dict[str, Any]:
        manifest = self._managed_manifest(state)
        backend = self._manifest_backend(manifest, backend)
        roles = self._role_names(manifest)
        home = self._resolve_install_home(manifest, platform_home)
        codex_paths = CodexPaths.resolve(home, tuple(roles))
        if manifest.get("installation_state") == "disabled":
            drift = self._disabled_drift(manifest, codex_paths)
            if drift:
                return {
                    "status": "disabled_with_conflicts",
                    "changed": False,
                    "roles_disabled": [],
                    "multi_agent_setting_restored": False,
                    "parent_model_version_restored": False,
                    "installation_state": "disabled",
                    "warnings": ["已停用状态与记录不一致："] + drift,
                    "agent_preserved": [role for role in roles if codex_paths.agent_for(role).is_file()],
                    "credential_preserved": backend.has_key(),
                }
            return {
                "status": "disabled",
                "changed": False,
                "roles_disabled": [],
                "multi_agent_setting_restored": False,
                "parent_model_version_restored": False,
                "installation_state": "disabled",
                "warnings": ["already_disabled"],
                "agent_preserved": [role for role in roles if codex_paths.agent_for(role).is_file()],
                "credential_preserved": backend.has_key(),
            }
        targets = (*codex_paths.transaction_targets(), state.manifest)
        backup = make_backup(state, targets, new_modes={})
        try:
            role_conflicts: list[str] = []
            for role in roles:
                agent_path = codex_paths.agent_for(role)
                expected = self._role_sha(manifest, role)
                if self._role_managed(manifest, role) and expected:
                    if not agent_path.is_file():
                        role_conflicts.append(f"{agent_path}（受管角色文件缺失）")
                    elif sha256_bytes(agent_path.read_bytes()) != expected:
                        role_conflicts.append(f"{agent_path}（内容与安装记录不一致）")
            if role_conflicts:
                raise ManagerError("conflict", "以下角色文件异常，拒绝停用。", {"paths": role_conflicts})

            warnings: list[str] = []
            field_conflicts: list[str] = []
            config_processed = False
            if not codex_paths.config.is_file():
                config_state = "missing"
                warnings.append("config.toml 缺失，未恢复多 Agent 路由设置。")
            elif self._hash_matches(codex_paths.config, manifest.get("config_sha256")):
                config_state = "trusted"
            else:
                config_state = "externally_modified"
            if config_state != "missing":
                config_processed = True
                text = codex_paths.config.read_text(encoding="utf-8")
                updated = remove_marked_block(text, ROLE_BEGIN, ROLE_END)
                updated = remove_marked_block(updated, LEGACY_ROLE_BEGIN, LEGACY_ROLE_END)
                v2_field = self._managed_field(manifest, "features.multi_agent_v2", codex_paths)
                if v2_field is not None:
                    updated, v2_status = self._restore_feature_bool(updated, "multi_agent_v2", v2_field)
                    if v2_status == "conflict":
                        field_conflicts.append("features.multi_agent_v2")
                        warnings.append("features.multi_agent_v2 已被修改，未覆盖；该字段仍残留项目值。")
                v1_field = self._managed_field(manifest, "features.multi_agent", codex_paths)
                if v1_field is not None:
                    updated, v1_status = self._restore_feature_bool(updated, "multi_agent", v1_field)
                    if v1_status == "conflict":
                        field_conflicts.append("features.multi_agent")
                        warnings.append("features.multi_agent 已被修改，未覆盖；该字段仍残留项目值。")
                if updated != text:
                    parse_toml_text(updated)
                    atomic_write(codex_paths.config, updated.encode())
            if not codex_paths.catalog.is_file():
                catalog_state = "missing"
            elif self._hash_matches(codex_paths.catalog, manifest.get("catalog_sha256")):
                catalog_state = "trusted"
            else:
                catalog_state = "externally_modified"
            parent_recorded = bool(manifest.get("parent_model"))
            managed_recorded = bool(manifest.get("managed_model_original_multi_agent_versions"))
            parent_restored = False
            managed_restored = False
            if catalog_state == "trusted" and (parent_recorded or managed_recorded):
                parent_restored, managed_restored = self._restore_versions(manifest, codex_paths)
                if not parent_restored and parent_recorded:
                    warnings.append("模型目录无法安全恢复父模型版本。")
                if not managed_restored and managed_recorded:
                    warnings.append("模型目录无法安全恢复受管子模型版本。")
            else:
                if parent_recorded:
                    warnings.append("模型目录无法安全恢复父模型版本（已被修改或缺失）。")
                if managed_recorded:
                    warnings.append("模型目录无法安全恢复受管子模型版本（已被修改或缺失）。")
                parent_restored = not parent_recorded
                managed_restored = not managed_recorded

            roles_disabled: list[str] = []
            preserved: list[str] = []
            for role in roles:
                agent_path = codex_paths.agent_for(role)
                if self._role_managed(manifest, role):
                    if agent_path.is_file():
                        agent_path.unlink()
                        roles_disabled.append(role)
                elif agent_path.is_file():
                    preserved.append(role)
            changed = bool(config_processed or parent_restored or roles_disabled)
            multi_agent_restored = not {
                "features.multi_agent",
                "features.multi_agent_v2",
            }.intersection(field_conflicts)

            updated_manifest = dict(manifest)

            updated_manifest["installation_state"] = "disabled"
            updated_manifest["config_state"] = config_state
            updated_manifest["catalog_state"] = catalog_state
            updated_manifest["safe_to_uninstall_catalog"] = catalog_state == "trusted"
            updated_manifest["disabled_config_sha256"] = (
                sha256_bytes(codex_paths.config.read_bytes()) if codex_paths.config.is_file() else None
            )
            updated_manifest["disabled_catalog_sha256"] = (
                sha256_bytes(codex_paths.catalog.read_bytes()) if codex_paths.catalog.is_file() else None
            )
            updated_manifest["disabled_at"] = datetime.now().isoformat(timespec="seconds")
            write_manifest(state.state_root, updated_manifest)
        except Exception:
            restore_backup(backup, targets)
            raise
        warnings.extend(self._cleanup_bridge_lifecycle(state))
        return {
            "status": "disabled",
            "changed": changed,
            "roles_disabled": roles_disabled,
            "multi_agent_setting_restored": multi_agent_restored,
            "parent_model_version_restored": parent_restored,
            "installation_state": "disabled",
            "config_state": config_state,
            "catalog_state": catalog_state,
            "safe_to_uninstall_catalog": catalog_state == "trusted",
            "field_conflicts": field_conflicts,
            "warnings": warnings,
            "agent_preserved": preserved,
            "credential_preserved": backend.has_key(),
        }

    def _cleanup_bridge_lifecycle(self, state: ProjectStatePaths) -> list[str]:
        """停止受管桥后台进程并删除受管计划任务（disable/uninstall 钩子）。

        仅在存在项目运行时状态（bridge-runtime.json）时执行，幂等；
        失败不阻塞 disable/uninstall，记录警告。
        """
        runtime_file = Path(state.state_root) / "bridge-runtime.json"
        if not runtime_file.is_file():
            return []
        try:
            from ...bridges.opencode_go.lifecycle import BridgeLifecycle

            BridgeLifecycle(state.state_root).uninstall_cleanup()
        except Exception as exc:  # noqa: BLE001 - 清理失败不应阻塞卸载
            return [f"桥后台进程清理失败：{exc}"]
        return []

    def _managed_field(
        self,
        manifest: dict[str, Any],
        name: str,
        codex_paths: CodexPaths,
    ) -> dict[str, Any] | None:
        fields = manifest.get("managed_config_fields") or {}
        if name in fields:
            return fields[name]
        if name == "features.multi_agent_v2" and manifest.get("managed_multi_agent_v2"):
            return {
                "installed": DESKTOP_MULTI_AGENT_V2,
                "previous": manifest.get("previous_multi_agent_v2"),
            }
        if name == "features.multi_agent" and manifest.get("managed_multi_agent"):
            return {
                "installed": DESKTOP_MULTI_AGENT_V1,
                "previous": manifest.get("previous_multi_agent"),
            }
        if name == "model_catalog_json" and manifest.get("managed_catalog_selection"):
            return {
                "installed": str(codex_paths.catalog),
                "previous": manifest.get("previous_model_catalog_json"),
            }
        return None

    @staticmethod
    def _restore_feature_bool(text: str, key: str, field: dict[str, Any]) -> tuple[str, str]:
        """受管 features 布尔字段的 compare-and-swap 恢复。

        返回 (text, status)，status ∈ {"restored", "clean", "conflict"}：
        - 当前值 == 项目写入值 → 恢复安装前值（或移除键）；
        - 当前值 == 安装前值或键已不存在 → 无需处理（clean）；
        - 当前值已被用户修改 → 不覆盖（conflict）。
        """

        parsed = parse_toml_text(text)
        features = parsed.get("features") or {}
        if key not in features:
            return text, "clean"
        current = features[key]
        if current == field.get("installed"):
            previous = field.get("previous")
            if isinstance(previous, bool):
                return set_table_bool(text, "features", key, previous), "restored"
            return remove_table_bool_if_value(
                text, "features", key, bool(field.get("installed"))
            ), "restored"
        if current == field.get("previous"):
            return text, "clean"
        return text, "conflict"

    def _disabled_drift(self, manifest: dict[str, Any], codex_paths: CodexPaths) -> list[str]:
        drift: list[str] = []
        expected_config = manifest.get("disabled_config_sha256")
        if expected_config is not None:
            if not codex_paths.config.is_file():
                drift.append(f"{codex_paths.config}（停用后配置文件缺失）")
            elif not self._hash_matches(codex_paths.config, expected_config):
                drift.append(f"{codex_paths.config}（内容与停用后记录不一致）")
        expected_catalog = manifest.get("disabled_catalog_sha256")
        if expected_catalog is not None:
            if not codex_paths.catalog.is_file():
                drift.append(f"{codex_paths.catalog}（停用后模型目录缺失）")
            elif not self._hash_matches(codex_paths.catalog, expected_catalog):
                drift.append(f"{codex_paths.catalog}（内容与停用后记录不一致）")
        for role in self._role_names(manifest):
            if self._role_managed(manifest, role) and codex_paths.agent_for(role).is_file():
                drift.append(f"{codex_paths.agent_for(role)}（停用后角色文件不应存在）")
        return drift

    def _restore_versions(self, manifest: dict[str, Any], codex_paths: CodexPaths) -> tuple[bool, bool]:
        """一次性恢复父模型与受管子模型的安装前版本（单次读改写）。

        返回 (parent_restored, managed_restored)。仅在目录与安装记录
        （catalog_sha256）一致时执行，避免覆盖外部修改。
        """

        parent = manifest.get("parent_model")
        original = manifest.get("parent_original_multi_agent_version")
        originals = manifest.get("managed_model_original_multi_agent_versions") or {}
        if not codex_paths.catalog.is_file():
            return (False, False)
        if sha256_bytes(codex_paths.catalog.read_bytes()) != manifest.get("catalog_sha256"):
            return (False, False)
        try:
            data = json.loads(codex_paths.catalog.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return (False, False)
        parent_restored = True
        managed_restored = True
        if parent:
            restore_previous_parent(data, parent, original)
        if originals:
            restore_managed_model_versions(data, originals)
        atomic_write(codex_paths.catalog, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())
        return (parent_restored, managed_restored)

    @staticmethod
    def _hash_matches(path: Path, expected: str | None) -> bool:
        if expected is None:
            return False
        try:
            return sha256_bytes(path.read_bytes()) == expected
        except OSError:
            return False

    def _verify_against_state(self, manifest: dict[str, Any], codex_paths: CodexPaths) -> list[str]:
        """按 installation_state 校验当前文件与记录一致，返回冲突路径列表。"""

        state = manifest.get("installation_state") or "active"
        conflicts: list[str] = []
        if state == "disabled":
            if manifest.get("disabled_config_sha256") is not None:
                if not codex_paths.config.is_file():
                    conflicts.append(f"{codex_paths.config}（停用后配置文件缺失）")
                elif not self._hash_matches(codex_paths.config, manifest.get("disabled_config_sha256")):
                    conflicts.append(f"{codex_paths.config}（停用后内容发生变化）")
            if manifest.get("disabled_catalog_sha256") is not None:
                if not codex_paths.catalog.is_file():
                    conflicts.append(f"{codex_paths.catalog}（停用后模型目录缺失）")
                elif not self._hash_matches(codex_paths.catalog, manifest.get("disabled_catalog_sha256")):
                    conflicts.append(f"{codex_paths.catalog}（停用后模型目录发生变化）")
            for role in self._role_names(manifest):
                if self._role_managed(manifest, role) and codex_paths.agent_for(role).is_file():
                    conflicts.append(f"{codex_paths.agent_for(role)}（停用后角色文件不应存在）")
            return conflicts

        if manifest.get("config_sha256") is not None:
            if not codex_paths.config.is_file():
                conflicts.append(f"{codex_paths.config}（安装记录存在但文件缺失）")
            elif not self._hash_matches(codex_paths.config, manifest.get("config_sha256")):
                conflicts.append(f"{codex_paths.config}（内容与安装记录不一致）")
        if manifest.get("catalog_sha256") is not None:
            if not codex_paths.catalog.is_file():
                conflicts.append(f"{codex_paths.catalog}（安装记录存在但文件缺失）")
            elif not self._hash_matches(codex_paths.catalog, manifest.get("catalog_sha256")):
                conflicts.append(f"{codex_paths.catalog}（内容与安装记录不一致）")
        for role in self._role_names(manifest):
            expected = self._role_sha(manifest, role)
            if self._role_managed(manifest, role) and expected:
                agent_path = codex_paths.agent_for(role)
                if not agent_path.is_file():
                    conflicts.append(f"{agent_path}（受管角色文件缺失）")
                elif not self._hash_matches(agent_path, expected):
                    conflicts.append(f"{agent_path}（内容与安装记录不一致）")
        return conflicts

    def uninstall(
        self,
        state: ProjectStatePaths,
        backend,
        remove_credential: bool = False,
        platform_home: str | None = None,
    ) -> dict[str, Any]:
        manifest, manifest_source, _legacy = self._read_manifest(state)
        if not manifest:
            raise ManagerError("not_managed", "没有找到本项目的管理记录，拒绝修改现有配置。")
        recorded = manifest.get("platform")
        if recorded and recorded != self.id:
            raise ManagerError(
                "platform_mismatch",
                f"manifest 记录了不受支持的旧宿主 {recorded}；当前版本只管理 Codex 安装。",
            )
        backend = self._manifest_backend(manifest, backend)
        roles = self._role_names(manifest)
        home = self._resolve_install_home(manifest, platform_home)
        codex_paths = CodexPaths.resolve(home, tuple(roles))
        targets = (*codex_paths.transaction_targets(), state.manifest)
        conflicts = self._verify_against_state(manifest, codex_paths)
        if conflicts:
            raise ManagerError(
                "conflict",
                "配置与安装记录不一致，拒绝卸载。",
                {"paths": conflicts},
            )
        backup = make_backup(state, targets)
        try:
            disabled = self.disable(state, backend, platform_home)
            if disabled.get("status") != "disabled":
                raise ManagerError(
                    "uninstall_blocked",
                    "停用状态未就绪（内部 disable 未返回健康 disabled），拒绝卸载。",
                    {
                        "disabled_status": disabled.get("status"),
                        "warnings": disabled.get("warnings", []),
                    },
                )
            field_conflicts: list[str] = []
            warnings: list[str] = []
            if codex_paths.config.is_file():
                text = codex_paths.config.read_text(encoding="utf-8")
                if manifest.get("credential_backend") == "bridge" or manifest.get("experimental"):
                    # The backup identifies the pre-install baseline, but a
                    # whole-file restore would erase unrelated edits made
                    # after installation. Remove only fields owned by this
                    # manifest and preserve all other TOML content.
                    text = remove_marked_block(text, PROVIDER_BEGIN, PROVIDER_END)
                    text = remove_marked_block(text, LEGACY_PROVIDER_BEGIN, LEGACY_PROVIDER_END)
                    text = remove_top_level_key_if_value(text, "model_catalog_json", str(codex_paths.catalog))
                    v2_field = self._managed_field(manifest, "features.multi_agent_v2", codex_paths)
                    if v2_field is not None:
                        text, v2_status = self._restore_feature_bool(text, "multi_agent_v2", v2_field)
                        if v2_status == "conflict":
                            field_conflicts.append("features.multi_agent_v2")
                    v1_field = self._managed_field(manifest, "features.multi_agent", codex_paths)
                    if v1_field is not None:
                        text, v1_status = self._restore_feature_bool(text, "multi_agent", v1_field)
                        if v1_status == "conflict":
                            field_conflicts.append("features.multi_agent")
                else:
                    text = self._cleanup_managed_config(text, manifest, codex_paths, field_conflicts)
                parse_toml_text(text)
                atomic_write(codex_paths.config, text.encode())
            if field_conflicts:
                updated = dict(manifest)
                updated["config_cleanup_state"] = "field_conflict"
                updated["safe_to_finalize_uninstall"] = False
                updated["partial_uninstall_conflicts"] = field_conflicts
                write_manifest(state.state_root, updated)
                return {
                    "status": "partially_uninstalled",
                    "conflicts": field_conflicts,
                    "warnings": ["以下受管字段已被用户修改，未覆盖："] + field_conflicts,
                    "manifest_preserved": True,
                }
            catalog_removed = False
            catalog_restored = False
            catalog_preserved = False
            if codex_paths.catalog.is_file():
                if manifest.get("catalog_state") == "externally_modified" or manifest.get(
                    "safe_to_uninstall_catalog"
                ) is False:
                    catalog_preserved = True
                elif manifest.get("catalog_preexisted") and manifest.get("catalog_original_backup") and Path(
                    manifest.get("catalog_original_backup")
                ).is_file():
                    atomic_write(codex_paths.catalog, Path(manifest.get("catalog_original_backup")).read_bytes())
                    catalog_restored = True
                elif not manifest.get("catalog_preexisted"):
                    codex_paths.catalog.unlink()
                    catalog_removed = True
            _unlink_manifests(state, manifest_source)
        except Exception:
            restore_backup(backup, targets)
            raise
        removed_credential = backend.remove() if remove_credential else False
        if catalog_preserved:
            warnings.append(
                "模型目录存在外部修改（catalog_state=externally_modified），已保留原文件，"
                "未删除或覆盖；请自行确认该文件是否还需要。"
            )
        return {
            "status": "uninstalled",
            "disabled": disabled,
            "catalog_removed": catalog_removed,
            "catalog_restored": catalog_restored,
            "catalog_preserved": catalog_preserved,
            "warnings": warnings,
            "credential_removed": removed_credential,
        }

    def _default_role(self, provider: ProviderDefinition | None) -> AgentRoleDefinition:
        from ...core.agent_role import make_role
        from ...providers import get_provider

        return make_role(provider or get_provider())

    def _default_provider(self) -> ProviderDefinition:
        from ...providers import get_provider

        return get_provider()


class _InertBackend:
    """本地桥认证状态；不读取或管理上游凭据。"""

    id = "bridge"
    name = "bridge"

    def available(self) -> bool:
        return True

    def has_key(self) -> bool:
        return True

    def can_store(self) -> bool:
        return False

    def store(self, secret: str) -> None:
        raise ManagerError("store_unsupported", "桥安装不支持 store。")

    def remove(self) -> bool:
        return False

    def persistent(self) -> bool:
        return False

    def auth_spec(self):
        from ...core.provider import ProviderAuthSpec

        return ProviderAuthSpec(kind="local_bridge", installable=False)


def _find_backup_copy(backup: Path, target: Path) -> str | None:
    for item in sorted((backup / "files").glob(f"*-{target.name}")):
        return str(item)
    return None


def _unlink_manifests(state: ProjectStatePaths, manifest_source: Path | None) -> None:
    state.manifest.unlink(missing_ok=True)
    if manifest_source is not None and manifest_source != state.manifest:
        manifest_source.unlink(missing_ok=True)
