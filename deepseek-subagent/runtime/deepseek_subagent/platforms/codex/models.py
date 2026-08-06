"""Codex 模型目录适配（合并 Provider 模型条目并固定父/受管子模型版本）。

合并逻辑只处理父模型与受管子模型（用于非 OpenAI 跨 Provider 子 Agent 的
模型条目）的 multi-agent 版本策略；Provider 的模型目录条目由调用方
（CLI/registry）预先准备好传入，本模块不知道任何 Provider 专属安装脚本
或端点。从 Provider 注册表取得的条目在写入前必须深拷贝，避免污染注册表
缓存的共享对象。
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
from typing import Any

from ...core.errors import ManagerError
from .paths import CodexPaths

PARENT_MULTI_AGENT_VERSION = "v1"
MANAGED_MODEL_MULTI_AGENT_VERSION = "v1"


def run_codex_models(codex_bin: str, paths: CodexPaths) -> dict[str, Any]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.home)
    proc = subprocess.run(
        [codex_bin, "debug", "models"],
        capture_output=True,
        text=True,
        env=env,
        timeout=45,
    )
    if proc.returncode != 0:
        raise ManagerError("codex_catalog_failed", "Codex 无法读取当前模型目录。", {"stderr": proc.stderr[-800:]})
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ManagerError("codex_catalog_invalid", "Codex 返回的模型目录不是有效 JSON。") from exc


def load_base_catalog(codex_bin: str, paths: CodexPaths, config: dict[str, Any]) -> dict[str, Any]:
    configured_path = config.get("model_catalog_json")
    if configured_path:
        from pathlib import Path

        candidate = Path(configured_path).expanduser()
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data.get("models"), list):
                    return data
            except (OSError, json.JSONDecodeError):
                pass
    return run_codex_models(codex_bin, paths)


def merged_catalog(
    base: dict[str, Any],
    provider_model_entry: dict[str, Any],
    parent_model: str,
    managed_models: tuple[str, ...] = (),
) -> dict[str, Any]:
    """合并模型目录：深拷贝 Provider 条目后追加，固定父模型与所有受管
    子模型（跨 Provider 子 Agent 的模型条目）的 multi_agent_version。

    - provider_model_entry 来自 Provider 注册表（共享对象），必须先深拷贝
      再写入，避免污染注册表缓存；
    - managed_models 为受管子模型 slug 列表（本项目管理、用于非 OpenAI
      跨 Provider 子 Agent 的模型），重复 slug 去重，不互相覆盖；
    - 父模型条目与受管条目重叠时按父模型处理（同一版本策略）。
    """

    entry = copy.deepcopy(provider_model_entry)
    models = [model for model in base.get("models", []) if model.get("slug") != entry.get("slug")]
    models.append(entry)
    parent_found = False
    managed = tuple(dict.fromkeys(managed_models))
    for model in models:
        if model.get("slug") == parent_model:
            model["multi_agent_version"] = PARENT_MULTI_AGENT_VERSION
            parent_found = True
        elif model.get("slug") in managed:
            model["multi_agent_version"] = MANAGED_MODEL_MULTI_AGENT_VERSION
    if not parent_found:
        raise ManagerError("parent_model_missing", f"模型目录中没有父模型 {parent_model}。")
    models.sort(key=lambda item: item.get("slug", ""))
    return {"models": models}


def configured_parent_model(config: dict[str, Any]) -> str | None:
    model = config.get("model")
    if isinstance(model, str) and model and not model.startswith("deepseek"):
        return model
    return None


def restore_previous_parent(base: dict[str, Any], previous_parent: str | None, previous_original: Any) -> None:
    if not previous_parent:
        return
    previous_entry = next((item for item in base.get("models", []) if item.get("slug") == previous_parent), None)
    if previous_entry is None:
        return
    if previous_original is None:
        previous_entry.pop("multi_agent_version", None)
    else:
        previous_entry["multi_agent_version"] = previous_original


def restore_managed_model_versions(base: dict[str, Any], original_versions: dict[str, Any]) -> None:
    """把受管子模型条目恢复到安装前版本。

    original_versions 为 slug → 安装前 multi_agent_version（None 表示安装前
    无该字段，恢复时删除键），仅影响记录的受管 slug，其他条目不动。
    """

    for slug, original in (original_versions or {}).items():
        entry = next((item for item in base.get("models", []) if item.get("slug") == slug), None)
        if entry is None:
            continue
        if original is None:
            entry.pop("multi_agent_version", None)
        else:
            entry["multi_agent_version"] = original


def clone_catalog(base: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(base)
