"""Codex config.toml 文本适配（CodexConfigManager）。

包括标记块管理、TOML 表/顶层键的确定性文本编辑，以及把通用
ProviderAuthSpec 转换为 Codex model_providers 认证字段。
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from ...core.errors import ManagerError
from ...core.provider import ProviderAuthSpec, ProviderDefinition
from ...core.tomlutil import toml_escape, toml_unescape

PROVIDER_BEGIN = "# BEGIN DEEPSEEK-SUBAGENT PROVIDER"
PROVIDER_END = "# END DEEPSEEK-SUBAGENT PROVIDER"
ROLE_BEGIN = "# BEGIN DEEPSEEK-SUBAGENT ROLE"
ROLE_END = "# END DEEPSEEK-SUBAGENT ROLE"

LEGACY_PROVIDER_BEGIN = "# BEGIN CODEX-DEEPSEEK-SUBAGENT PROVIDER"
LEGACY_PROVIDER_END = "# END CODEX-DEEPSEEK-SUBAGENT PROVIDER"
LEGACY_ROLE_BEGIN = "# BEGIN CODEX-DEEPSEEK-SUBAGENT ROLE"
LEGACY_ROLE_END = "# END CODEX-DEEPSEEK-SUBAGENT ROLE"

DESKTOP_MULTI_AGENT_V2 = False
AUTH_TIMEOUT_MS = 5000
AUTH_REFRESH_INTERVAL_MS = 0


def parse_toml_text(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManagerError("invalid_config", f"config.toml 无法解析：{exc}") from exc


def remove_marked_block(text: str, begin: str, end: str) -> str:
    pattern = re.compile(rf"{re.escape(begin)}.*?{re.escape(end)}\n?", flags=re.DOTALL)
    return pattern.sub("", text).rstrip() + ("\n" if text else "")


def _remove_any_marked_block(text: str, begins: tuple[str, ...], ends: tuple[str, ...]) -> str:
    for begin, end in zip(begins, ends):
        text = remove_marked_block(text, begin, end)
    return text


def remove_managed_blocks(text: str) -> str:
    text = _remove_any_marked_block(text, (PROVIDER_BEGIN, LEGACY_PROVIDER_BEGIN), (PROVIDER_END, LEGACY_PROVIDER_END))
    return _remove_any_marked_block(text, (ROLE_BEGIN, LEGACY_ROLE_BEGIN), (ROLE_END, LEGACY_ROLE_END))


def toml_table_header(table: str) -> re.Pattern[str]:
    tokens = [
        rf"(?:{re.escape(part)}|\"{re.escape(part)}\"|'{re.escape(part)}')"
        for part in table.split(".")
    ]
    return re.compile(r"^\[\s*" + r"\s*\.\s*".join(tokens) + r"\s*\]\s*(?:#.*)?$")


def remove_toml_table(text: str, table: str) -> str:
    lines = text.splitlines()
    header = toml_table_header(table)
    start = next((index for index, line in enumerate(lines) if header.match(line.strip())), None)
    if start is None:
        return text
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].strip().startswith("[")),
        len(lines),
    )
    kept = lines[:start] + lines[end:]
    return "\n".join(kept).rstrip() + "\n"


def top_level_key(text: str, key: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            break
        match = re.match(rf"{re.escape(key)}\s*=\s*\"([^\"]+)\"", stripped)
        if match:
            return match.group(1)
    return None


def set_top_level_key(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    assignment = f'{key} = "{toml_escape(value)}"'
    first_table = next((i for i, line in enumerate(lines) if line.strip().startswith("[")), len(lines))
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(first_table):
        if key_pattern.match(lines[index]):
            lines[index] = assignment
            return "\n".join(lines).rstrip() + "\n"
    lines.insert(first_table, assignment)
    if first_table and lines[first_table - 1].strip():
        lines.insert(first_table + 1, "")
    return "\n".join(lines).rstrip() + "\n"


def remove_top_level_key_if_value(text: str, key: str, expected: str) -> str:
    lines = text.splitlines()
    first_table = next((i for i, line in enumerate(lines) if line.strip().startswith("[")), len(lines))
    pattern = re.compile(rf'^\s*{re.escape(key)}\s*=\s*"{re.escape(expected)}"\s*$')
    removed = {
        index
        for index, line in enumerate(lines)
        if index < first_table and pattern.match(toml_unescape(line))
    }
    for index in tuple(removed):
        if index + 1 < first_table and not lines[index + 1].strip():
            removed.add(index + 1)
    kept = [
        line
        for index, line in enumerate(lines)
        if index not in removed
    ]
    return "\n".join(kept).rstrip() + "\n"


def set_table_bool(text: str, table: str, key: str, value: bool) -> str:
    lines = text.splitlines()
    assignment = f"{key} = {'true' if value else 'false'}"
    header = toml_table_header(table)
    start = next((index for index, line in enumerate(lines) if header.match(line.strip())), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend((f"[{table}]", assignment))
        return "\n".join(lines).rstrip() + "\n"
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].strip().startswith("[")),
        len(lines),
    )
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(start + 1, end):
        if key_pattern.match(lines[index]):
            lines[index] = assignment
            return "\n".join(lines).rstrip() + "\n"
    lines.insert(start + 1, assignment)
    return "\n".join(lines).rstrip() + "\n"


def remove_table_bool_if_value(text: str, table: str, key: str, expected: bool) -> str:
    lines = text.splitlines()
    header = toml_table_header(table)
    start = next((index for index, line in enumerate(lines) if header.match(line.strip())), None)
    if start is None:
        return text
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].strip().startswith("[")),
        len(lines),
    )
    expected_text = "true" if expected else "false"
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*{expected_text}\s*(?:#.*)?$")
    kept = [
        line
        for index, line in enumerate(lines)
        if not (start < index < end and pattern.match(line))
    ]
    body_start = start
    body_end = end - 1
    if not any(line.strip() for line in kept[body_start + 1 : body_end + 1]):
        kept.pop(start)
        if start > 0 and not kept[start - 1].strip():
            kept.pop(start - 1)
    return "\n".join(kept).rstrip() + "\n"


def codex_auth_config(auth_spec: ProviderAuthSpec) -> dict[str, Any]:
    if auth_spec.kind == "keychain":
        if not auth_spec.keychain_service or not auth_spec.keychain_account:
            raise ManagerError("provider_auth_incomplete", "Keychain 凭据后端缺少服务名或账户。")
        return {
            "command": "/usr/bin/security",
            "args": [
                "find-generic-password",
                "-a",
                auth_spec.keychain_account,
                "-s",
                auth_spec.keychain_service,
                "-w",
            ],
            "timeout_ms": AUTH_TIMEOUT_MS,
            "refresh_interval_ms": AUTH_REFRESH_INTERVAL_MS,
        }
    if auth_spec.kind == "environment":
        if not auth_spec.env_var:
            raise ManagerError("provider_auth_incomplete", "环境变量凭据后端缺少变量名。")
        return {
            "env_key": auth_spec.env_var,
            "timeout_ms": AUTH_TIMEOUT_MS,
            "refresh_interval_ms": AUTH_REFRESH_INTERVAL_MS,
        }
    raise ManagerError(
        "provider_auth_not_installable",
        f"凭据后端 {auth_spec.kind} 不能生成可安装的 Provider 认证配置。",
    )


def expected_provider_auth(auth_spec: ProviderAuthSpec) -> dict[str, Any]:
    return codex_auth_config(auth_spec)


def managed_provider_block(
    provider: ProviderDefinition,
    auth_spec: ProviderAuthSpec,
    auth_config: dict[str, Any] | None = None,
    base_url_override: str | None = None,
) -> str:
    auth = auth_config if auth_config is not None else codex_auth_config(auth_spec)
    table = provider.platform_id()
    base_url = base_url_override if base_url_override is not None else provider.base_url
    lines = [
        PROVIDER_BEGIN,
        f"[model_providers.{table}]",
        f'name = "{provider.name}"',
        f'base_url = "{base_url}"',
        f'wire_api = "{provider.wire_api}"',
        "",
        f"[model_providers.{table}.auth]",
    ]
    for key, value in auth.items():
        if isinstance(value, str):
            lines.append(f'{key} = "{toml_escape(value)}"')
        elif isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        else:
            lines.append(f"{key} = {json.dumps(value)}")
    lines.append(PROVIDER_END)
    return "\n" + "\n".join(lines) + "\n"


def provider_conflicts(
    provider: dict[str, Any] | None,
    provider_def: ProviderDefinition,
    auth_spec: ProviderAuthSpec,
) -> list[str]:
    if not provider:
        return []
    table = provider_def.platform_id()
    issues: list[str] = []
    expected = {
        "name": provider_def.name,
        "base_url": provider_def.base_url,
        "wire_api": provider_def.wire_api,
    }
    for key, value in expected.items():
        if provider.get(key) != value:
            issues.append(f"model_providers.{table}.{key}")
    auth = provider.get("auth")
    if not isinstance(auth, dict):
        issues.append(f"model_providers.{table}.auth")
        return issues
    for key, value in expected_provider_auth(auth_spec).items():
        if auth.get(key) != value:
            issues.append(f"model_providers.{table}.auth.{key}")
    return issues
