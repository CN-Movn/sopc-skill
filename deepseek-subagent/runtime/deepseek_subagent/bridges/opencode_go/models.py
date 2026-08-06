"""/v1/models 响应转换：OpenCode Go data[] → Codex models[]。"""

from __future__ import annotations

from typing import Any


def upstream_to_codex_models(payload: dict[str, Any]) -> dict[str, Any]:
    """把 OpenCode Go 的 {"data": [...]} 转换为 Codex 能识别的
    {"models": [{"slug": ...}]}，避免 fallback metadata warning。"""

    models: list[dict[str, Any]] = []
    for item in payload.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        slug = item.get("id")
        if not slug:
            continue
        entry: dict[str, Any] = {"slug": slug}
        for key in ("name", "created", "owned_by"):
            if key in item:
                entry[key] = item[key]
        models.append(entry)
    return {"models": models}
