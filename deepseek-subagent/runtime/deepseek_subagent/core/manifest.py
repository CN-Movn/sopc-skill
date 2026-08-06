"""Codex 安装 manifest 读写及旧名称、旧位置的只读兼容。

新写入固定记录 ``platform=codex`` 与 ``provider=opencode-go``。

旧版本兼容（只读，不静默删除）：
- schema 1/2/3：读取旧字段，缺失的新字段按推断或 None 填充；
- 旧状态目录 codex-deepseek-subagent/manifest.json（与状态根同父目录）；
- 旧 CODEX_HOME（~/.codex）下的 deepseek-subagent/ 与 codex-deepseek-subagent/。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic
from .paths import LEGACY_PROJECT_NAME, PROJECT_NAME

SCHEMA_VERSION = 4
CURRENT_PROJECT = PROJECT_NAME


def manifest_file(state_root: Path) -> Path:
    return state_root / "manifest.json"


def legacy_manifest_candidates(state_root: Path) -> list[Path]:
    candidates = [
        state_root.parent / LEGACY_PROJECT_NAME / "manifest.json",
    ]
    try:
        codex_default = Path.home() / ".codex"
    except RuntimeError:
        return candidates
    if codex_default != state_root:
        candidates.append(codex_default / PROJECT_NAME / "manifest.json")
        candidates.append(codex_default / LEGACY_PROJECT_NAME / "manifest.json")
    return candidates


def write_manifest(state_root: Path, payload: dict) -> Path:
    target = manifest_file(state_root)
    atomic.atomic_write(target, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())
    return target


def read_manifest(state_root: Path) -> dict:
    payload, _source, _legacy = read_manifest_with_source(state_root)
    return payload


def read_manifest_with_source(state_root: Path) -> tuple[dict, Path | None, bool]:
    new_path = manifest_file(state_root)
    if new_path.is_file():
        return _load(new_path), new_path, False
    for candidate in legacy_manifest_candidates(state_root):
        if candidate.is_file():
            return _load(candidate), candidate, True
    return {}, None, False


def upgrade_payload(payload: dict) -> dict:
    """旧 schema 读取时补齐 v4 字段（推断或 None），保留存储的 schema_version。"""

    schema = payload.get("schema_version", 1)
    if schema >= SCHEMA_VERSION:
        return payload
    upgraded = dict(payload)
    upgraded.setdefault("project", CURRENT_PROJECT)
    upgraded.setdefault("platform", "codex" if schema >= 2 else None)
    upgraded.setdefault("platform_home", None)
    # 历史 provider 字段仅用于读取旧事务，不再参与配置选择。显示视图
    # 统一为当前固定链路；下一次成功 setup/repair 会写回收敛后的 manifest。
    upgraded["platform"] = "codex"
    upgraded["provider"] = "opencode-go"
    upgraded.setdefault("model", payload.get("model") or "deepseek-v4-flash")
    upgraded.setdefault("credential_backend", None)
    upgraded.setdefault("roles", [payload["agent_role"]] if payload.get("agent_role") else [])
    upgraded.setdefault("compatibility_mode", "multi-agent-v1")
    upgraded.setdefault("adapter_version", 1)
    return upgraded


def _load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload
